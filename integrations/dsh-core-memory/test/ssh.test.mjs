import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createSshCoreReader } from '../lib/ssh.js'
import { CoreMemoryError } from '../lib/core.js'

const node = process.execPath
const options = extra => ({ executable: node, alias: 'aliyun', expectedHost: 'memory.example', user: 'root', sshPort: 22, remotePort: 18767, timeoutMs: 1200, maxResponseBytes: 4096, authMode: 'api-key', ...extra })
const fixtureProcessOptions = value => ({ ...value, env: { ...value.env, ...(process.versions.electron ? { ELECTRON_RUN_AS_NODE: '1' } : {}) } })
const errorCode = code => error => error instanceof CoreMemoryError && error.code === code && error.message === code

async function fixture(t, behavior) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'ssh-reader-'))
  const script = path.join(dir, 'fake-ssh.mjs')
  await writeFile(script, `
let input = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', part => {
  input += part
  if (input.includes('\\r\\n\\r\\n')) respond()
})
function respond() {
  const mode = process.argv[2]
  if (mode === 'hang') return
  const status = mode === 'unauthorized' ? '401 Unauthorized' : mode === 'redirect' ? '302 Found' : mode === 'server-error' ? '500 Error' : '200 OK'
  const body = mode === 'invalid-utf8' ? Buffer.from([0xc3, 0x28]) : Buffer.from(mode === 'bad-json' ? '{' : JSON.stringify({ blocks: [{ block: 'policy' }] }))
  const headers = mode === 'length' ? 'Content-Type: application/json\\r\\nContent-Length: 99999\\r\\n' : 'Content-Type: application/json\\r\\n'
  process.stdout.write('HTTP/1.1 ' + status + '\\r\\n' + headers + 'Connection: close\\r\\n\\r\\n')
  // Model SSH closing its direct channel after the HTTP server closes, not a
  // Node fixture held alive forever by its still-readable standard input.
  const closeChannel = part => process.stdout.end(part, () => process.stdin.destroy())
  if (mode === 'stream') { process.stdout.write(body.subarray(0, 3)); setTimeout(() => closeChannel(body.subarray(3)), 10) }
  else closeChannel(body)
}
`)
  t.after(() => rm(dir, { recursive: true, force: true }))
  const launches = []
  const children = []
  const reader = createSshCoreReader(options(), { spawn: (...args) => {
    launches.push(args)
    const child = spawn(node, [script, behavior], fixtureProcessOptions(args[2]))
    children.push(child)
    return child
  } })
  t.after(() => reader.dispose())
  return { reader, launches, children }
}

test('uses a bounded SSH process and returns parsed JSON over HTTP stdin', async t => {
  const f = await fixture(t, 'stream')
  const result = await f.reader.read({ namespace: 'alpha & beta' }, 'top-secret')
  assert.deepEqual(result, { blocks: [{ block: 'policy' }] })
  assert.equal(f.launches.length, 1)
  const [exe, argv, config] = f.launches[0]
  assert.equal(exe, node)
  assert(argv.includes('-W') && argv.includes('127.0.0.1:18767'))
  assert(argv.includes('HostName=memory.example') && argv.includes('User=root') && argv.includes('Port=22'))
  assert(!JSON.stringify([argv, config.env]).includes('top-secret'))
  assert.equal(config.env.DSH_TOKEN, undefined)
})

test('rejects invalid input before starting SSH and closes after dispose', async t => {
  const f = await fixture(t, 'ok')
  await assert.rejects(f.reader.read({ namespace: 'valid' }, 'bad\nvalue'), errorCode('missing_credentials'))
  await assert.rejects(f.reader.read({ namespace: 'a\0b' }, 'valid'), errorCode('invalid_config'))
  assert.equal(f.launches.length, 0)
  await f.reader.dispose()
  await assert.rejects(f.reader.read({ namespace: 'valid' }, 'valid'), errorCode('disposed'))
})

for (const [mode, code] of [['unauthorized', 'authentication_failed'], ['redirect', 'remote_error'], ['server-error', 'remote_error'], ['bad-json', 'invalid_response'], ['invalid-utf8', 'invalid_response'], ['length', 'response_too_large']]) {
  test(`maps ${mode} to a safe error`, async t => {
    const f = await fixture(t, mode)
    await assert.rejects(f.reader.read({ namespace: 'valid' }, 'secret'), errorCode(code))
  })
}

test('aborting a stalled read terminates the SSH process', async t => {
  const f = await fixture(t, 'hang')
  const abort = new AbortController()
  const pending = f.reader.read({ namespace: 'valid' }, 'secret', abort.signal)
  setTimeout(() => abort.abort(), 30)
  await assert.rejects(pending, errorCode('aborted'))
  assert.equal(f.launches.length, 1)
  assert(f.children.every(child => child.exitCode !== null || child.signalCode !== null), 'read must await its owned process termination')
})

test('disposing an active reader cancels and awaits only its own child', async t => {
  const f = await fixture(t, 'hang')
  const pending = f.reader.read({ namespace: 'valid' }, 'secret').then(() => null, error => error)
  await f.reader.dispose()
  assert.equal((await pending).code, 'disposed')
  assert(f.children.every(child => child.exitCode !== null || child.signalCode !== null))
  await f.reader.dispose()
})

test('an unavailable SSH executable fails safely and leaves no active read', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'ssh-reader-missing-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const reader = createSshCoreReader(options({ executable: path.join(dir, 'not-present') }))
  t.after(() => reader.dispose())
  await assert.rejects(reader.read({ namespace: 'valid' }, 'secret'), errorCode('transport_error'))
  await reader.dispose()
})

test('timeout terminates a stalled SSH process', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'ssh-reader-'))
  const script = path.join(dir, 'hang.mjs')
  await writeFile(script, 'process.stdin.resume()')
  t.after(() => rm(dir, { recursive: true, force: true }))
  const reader = createSshCoreReader(options({ timeoutMs: 50 }), { spawn: (...args) => spawn(node, [script], fixtureProcessOptions(args[2])) })
  t.after(() => reader.dispose())
  await assert.rejects(reader.read({ namespace: 'valid' }, 'secret'), errorCode('timeout'))
})
