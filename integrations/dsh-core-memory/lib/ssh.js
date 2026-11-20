import { spawn as nodeSpawn } from 'node:child_process'
import { request as httpRequest } from 'node:http'
import { Duplex } from 'node:stream'
import path from 'node:path'
import { CoreMemoryError } from './core.js'

const fail = code => { throw new CoreMemoryError(code) }
const validToken = value => typeof value === 'string' && /^[\x21-\x7e]{1,8192}$/u.test(value)
const validAtom = (value, pattern) => typeof value === 'string' && pattern.test(value)
const envNames = ['PATH', 'Path', 'SystemRoot', 'WINDIR', 'USERPROFILE', 'HOME', 'TMP', 'TEMP', 'TMPDIR', 'LANG', 'LC_ALL', 'SSH_AUTH_SOCK']

function validate(options) {
  if (!options || typeof options !== 'object' || typeof options.executable !== 'string' || !path.isAbsolute(options.executable) || /[\u0000-\u001f]/u.test(options.executable) ||
      !validAtom(options.alias, /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/u) ||
      !validAtom(options.expectedHost, /^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$/u) ||
      !validAtom(options.user, /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/u) ||
      !Number.isSafeInteger(options.sshPort) || options.sshPort < 1 || options.sshPort > 65535 ||
      !Number.isSafeInteger(options.remotePort) || options.remotePort < 1 || options.remotePort > 65535 ||
      !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs < 50 || options.timeoutMs > 10000 ||
      !Number.isSafeInteger(options.maxResponseBytes) || options.maxResponseBytes < 1 || options.maxResponseBytes > 1048576 ||
      !['api-key', 'bearer'].includes(options.authMode)) fail('invalid_config')
}

/** A connected byte stream with explicit half-close semantics, not a network listener. */
function tunnelStream(child) {
  const tunnel = new Duplex({
    allowHalfOpen: true,
    autoDestroy: false,
    read() { child.stdout.resume() },
    write(chunk, encoding, callback) { child.stdin.write(chunk, encoding, callback) },
    final(callback) { child.stdin.end(callback) },
    destroy(error, callback) {
      child.stdout.destroy()
      child.stdin.destroy()
      callback(error)
    },
  })
  child.stdout.on('data', chunk => { if (!tunnel.push(chunk)) child.stdout.pause() })
  child.stdout.once('end', () => tunnel.push(null))
  child.stdout.on('error', error => tunnel.destroy(error))
  child.stdin.on('error', error => tunnel.destroy(error))
  return tunnel
}

/** Wait only for this owned process, escalating termination without leaking it. */
async function terminate(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const wait = milliseconds => new Promise(resolve => {
    const done = () => { clearTimeout(timer); child.removeListener('exit', done); resolve(true) }
    const timer = setTimeout(() => { child.removeListener('exit', done); resolve(false) }, milliseconds)
    child.once('exit', done)
    if (child.exitCode !== null || child.signalCode !== null) done()
  })
  try { child.kill() } catch { /* It may already have exited. */ }
  if (await wait(250)) return
  try { child.kill('SIGKILL') } catch { /* Final status is checked below. */ }
  if (!await wait(250)) throw new CoreMemoryError('transport_cleanup_failed')
}

/** Read one bounded CoreMemory response over verified SSH stdio; never run a remote shell. */
export function createSshCoreReader(input, deps = {}) {
  validate(input)
  const options = Object.freeze({ ...input })
  const spawn = deps.spawn ?? nodeSpawn
  const active = new Set()
  let disposed = false

  async function read(binding, token, signal) {
    if (disposed) fail('disposed')
    if (!binding || typeof binding.namespace !== 'string' || !binding.namespace || binding.namespace.length > 256 || /[\u0000-\u001f\u007f]/u.test(binding.namespace)) fail('invalid_config')
    if (!validToken(token)) fail('missing_credentials')
    if (signal?.aborted) fail('aborted')
    const childEnv = Object.create(null)
    for (const key of envNames) if (process.env[key] !== undefined) childEnv[key] = process.env[key]
    // Windows OpenSSH needs ProgramData to locate its system configuration.
    if (process.platform === 'win32' && process.env.ProgramData) childEnv.ProgramData = process.env.ProgramData
    const argv = ['-T', '-W', `127.0.0.1:${options.remotePort}`, '-o', `HostName=${options.expectedHost}`, '-o', `HostKeyAlias=${options.expectedHost}`, '-o', `User=${options.user}`, '-o', `Port=${options.sshPort}`, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', `ConnectTimeout=${Math.max(1, Math.ceil(options.timeoutMs / 1000))}`, '-o', 'ConnectionAttempts=1', '-o', 'PermitLocalCommand=no', '-o', 'ClearAllForwardings=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '--', options.alias]
    let child
    try { child = spawn(options.executable, argv, { shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'], env: childEnv }) } catch { fail('transport_error') }
    let request
    let tunnel
    let abort
    let timer
    let cancelOperation
    let settled = false
    let completeCleanup
    const operation = {
      cancel: () => cancelOperation?.('disposed'),
      done: new Promise(resolve => { completeCleanup = resolve }),
    }
    active.add(operation)
    try {
      const payload = await new Promise((resolve, reject) => {
        const settle = (error, value) => {
          if (settled) return
          settled = true
          clearTimeout(timer)
          signal?.removeEventListener('abort', abort)
          if (error) reject(error)
          else resolve(value)
        }
        const rejectCode = code => settle(new CoreMemoryError(code))
        cancelOperation = rejectCode
        abort = () => rejectCode('aborted')
        timer = setTimeout(() => rejectCode('timeout'), options.timeoutMs)
        signal?.addEventListener('abort', abort, { once: true })
        child.once('error', () => rejectCode('transport_error'))
        child.once('close', () => queueMicrotask(() => { if (!settled) rejectCode('transport_error') }))
        tunnel = tunnelStream(child)
        tunnel.on('error', () => rejectCode('transport_error'))
        const headers = { Accept: 'application/json', 'Accept-Encoding': 'identity', Connection: 'close' }
        if (options.authMode === 'api-key') headers['X-API-Key'] = token
        else headers.Authorization = `Bearer ${token}`
        request = httpRequest({ method: 'GET', host: '127.0.0.1', port: options.remotePort, path: `/core-memory?namespace=${encodeURIComponent(binding.namespace)}`, headers, createConnection: () => tunnel, maxHeaderSize: 16384 }, response => {
          const status = response.statusCode ?? 0
          if (status === 401 || status === 403) { rejectCode('authentication_failed'); return }
          if (status < 200 || status >= 300) { rejectCode('remote_error'); return }
          const type = (response.headers['content-type'] ?? '').split(';', 1)[0].trim().toLowerCase()
          if (type !== 'application/json' && !/^application\/[a-z0-9.+-]+\+json$/u.test(type)) { rejectCode('invalid_response'); return }
          const encoding = response.headers['content-encoding']
          if (encoding && encoding.toLowerCase() !== 'identity') { rejectCode('invalid_response'); return }
          const length = response.headers['content-length']
          if (length !== undefined && (!/^\d+$/u.test(length) || Number(length) > options.maxResponseBytes)) { rejectCode('response_too_large'); return }
          const chunks = []
          let bytes = 0
          response.on('data', chunk => {
            if (settled) return
            bytes += chunk.length
            if (bytes > options.maxResponseBytes) { rejectCode('response_too_large'); return }
            chunks.push(chunk)
          })
          response.once('aborted', () => rejectCode('transport_error'))
          response.once('error', () => rejectCode('transport_error'))
          response.once('end', () => {
            if (settled) return
            try { settle(null, JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, bytes)))) }
            catch { rejectCode('invalid_response') }
          })
        })
        request.once('error', () => rejectCode('transport_error'))
        request.end()
        if (signal?.aborted) abort()
      })
      if (disposed) fail('disposed')
      if (signal?.aborted) fail('aborted')
      return payload
    } catch (error) {
      if (error instanceof CoreMemoryError) throw error
      fail('transport_error')
    } finally {
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
      let cleanupFailure
      try {
        request?.destroy()
        tunnel?.destroy()
        await terminate(child)
      } catch { cleanupFailure = new CoreMemoryError('transport_cleanup_failed') }
      finally {
        active.delete(operation)
        completeCleanup(cleanupFailure ?? null)
      }
      if (cleanupFailure) throw cleanupFailure
    }
  }

  async function dispose() {
    disposed = true
    const pending = [...active]
    for (const operation of pending) operation.cancel()
    const failures = await Promise.all(pending.map(operation => operation.done))
    if (failures.some(Boolean)) fail('transport_cleanup_failed')
  }

  return Object.freeze({ read, dispose })
}
