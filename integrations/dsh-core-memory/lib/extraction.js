import { createHash } from 'node:crypto'
import { CoreMemoryError, workspaceKey } from './core.js'

const fail = code => { throw new CoreMemoryError(code) }
const validToken = value => typeof value === 'string' && /^[\x21-\x7e]{1,8192}$/u.test(value)

/** Verify the existing authenticated identity before releasing any dialogue text. */
export async function submitScopedDialogue({ authMode, binding, payload, request, signal }) {
  if (typeof binding?.userId !== 'string' || !binding.userId || payload?.user_id !== binding.userId) fail('ownership_mismatch')
  const tenantId = binding.tenantId ?? null
  // Host bindings must round-trip unchanged through an HTTP header.
  if (tenantId !== null && (typeof tenantId !== 'string' || !/^[\x21-\x7e]{1,256}$/u.test(tenantId))) fail('invalid_config')
  signal?.throwIfAborted()
  if (authMode === 'api-key') {
    // Environment API_KEY has the one fixed admin identity. The admin-only
    // read also distinguishes a valid key from optional loopback DEV MODE.
    if (binding.userId !== 'api_key') fail('ownership_mismatch')
    const metadata = await request('GET', 'admin/api/sysinfo', undefined, tenantId)
    if (!Number.isSafeInteger(metadata?.pid) || metadata.pid < 1 || typeof metadata.system !== 'string') fail('invalid_response')
  } else if (authMode === 'bearer') {
    // The identity endpoint returns only the authenticated principal metadata.
    const identity = await request('GET', 'dialogue/identity', undefined, tenantId)
    if (identity?.user_id !== binding.userId || identity.tenant_id !== tenantId) fail('ownership_mismatch')
  } else fail('invalid_config')
  signal?.throwIfAborted()
  return request('POST', 'dialogue/async', payload, tenantId)
}

/** Submit to Lantai's existing extraction pipeline. A receipt is not a stored memory. */
export async function submitDialogue(config, binding, token, payload, signal) {
  if (config.transport !== 'direct') fail('invalid_config')
  if (!validToken(token)) fail('missing_credentials')
  const request = (method, path, body, tenantId) => requestDialogueJson(config, token, signal, method, path, body, tenantId)
  return submitScopedDialogue({ authMode: config.authMode, binding, payload, request, signal })
}

async function requestDialogueJson(config, token, signal, method, endpoint, payload, tenantId) {
  let response
  let reader
  try {
    signal?.throwIfAborted()
    const headers = { accept: 'application/json' }
    if (config.authMode === 'api-key') headers['X-API-Key'] = token
    else headers.authorization = `Bearer ${token}`
    if (tenantId !== null) headers['X-Tenant-Id'] = tenantId
    if (payload !== undefined) headers['content-type'] = 'application/json'
    response = await fetch(new URL(endpoint, config.baseUrl), {
      method, redirect: 'error', cache: 'no-store', signal, headers,
      body: payload === undefined ? undefined : JSON.stringify(payload),
    })
    if (response.status === 401 || response.status === 403) fail('authentication_failed')
    if (response.status === 404 && endpoint === 'dialogue/identity') fail('identity_unavailable')
    if (!response.ok) fail(endpoint === 'dialogue/async' && response.status === 409 ? 'dialogue_disabled' : 'remote_error')
    const type = (response.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase()
    if (type !== 'application/json' && !/^application\/[a-z0-9.+-]+\+json$/u.test(type)) fail('invalid_response')
    const length = response.headers.get('content-length')
    if (length !== null && (!/^\d+$/u.test(length) || Number(length) > config.maxResponseBytes)) fail('response_too_large')
    if (!response.body) fail('invalid_response')
    reader = response.body.getReader()
    const chunks = []
    let bytes = 0
    while (true) {
      signal?.throwIfAborted()
      const part = await reader.read()
      if (part.done) break
      bytes += part.value.byteLength
      if (bytes > config.maxResponseBytes) fail('response_too_large')
      chunks.push(part.value)
    }
    try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, bytes))) }
    catch { fail('invalid_response') }
  } catch (error) {
    if (signal?.aborted) fail('aborted')
    if (error instanceof CoreMemoryError) throw error
    fail('transport_error')
  } finally {
    if (reader) {
      try { await reader.cancel() } catch { /* Remote bodies never enter diagnostics. */ }
      reader.releaseLock()
    } else if (response?.body) {
      try { await response.body.cancel() } catch { /* No remote error body is retained. */ }
    }
  }
}

function abortable(value, signal) {
  if (signal.aborted) return Promise.reject(new CoreMemoryError('aborted'))
  return new Promise((resolve, reject) => {
    const abort = () => reject(new CoreMemoryError('aborted'))
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(value).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/** Consume only the live committed event feed; never scan, replay or retain history. */
export function createSessionExtractor({ config, binding, sessionId, getWorkspace, resolveCredential, authorize = () => false, writeDialogue = submitDialogue }) {
  let disposed = false
  let generation = 0
  let lastSeen = -1
  let currentTurn
  let submittedCount = 0
  let state = binding ? 'idle' : 'not_bound'
  let reason = binding ? '' : 'not_bound'
  let taskId = ''
  let active
  let worker
  const queue = []
  const inScope = () => {
    try { return Boolean(binding && workspaceKey(getWorkspace()) === binding.workspaceKey) } catch { return false }
  }
  const permitted = () => {
    try { return !disposed && Boolean(binding) && authorize() === true && inScope() } catch { return false }
  }
  const status = () => ({ state, reason, pendingCount: queue.length + (active ? 1 : 0), submittedCount, taskId })

  function invalidate(code = 'disabled') {
    generation++
    queue.length = 0
    active?.abort()
    state = disposed ? 'disposed' : 'unavailable'
    reason = code
    taskId = ''
  }

  async function run() {
    while (queue.length && !disposed) {
      const item = queue.shift()
      if (!permitted() || item.generation !== generation) continue
      const controller = new AbortController()
      active = controller
      const timer = setTimeout(() => controller.abort(), config.timeoutMs)
      state = 'submitting'
      reason = ''
      const check = () => {
        controller.signal.throwIfAborted()
        if (!permitted() || item.generation !== generation) fail('authorization_required')
      }
      try {
        check()
        const credential = await abortable(resolveCredential(config.apiKeyEnv, controller.signal), controller.signal)
        check()
        if (!validToken(credential?.value)) fail('missing_credentials')
        const receipt = await abortable(writeDialogue(config, binding, credential.value, item.payload, controller.signal), controller.signal)
        check()
        if (receipt?.status !== 'queued' || typeof receipt.task_id !== 'string' || !/^task_[A-Za-z0-9_-]{1,128}$/u.test(receipt.task_id)) fail('invalid_response')
        submittedCount++
        state = 'submitted'
        reason = ''
        taskId = receipt.task_id
      } catch (error) {
        if (!disposed && item.generation === generation) {
          state = 'unavailable'
          reason = controller.signal.aborted ? 'timeout' : error instanceof CoreMemoryError ? error.code : 'transport_error'
          taskId = ''
        }
      } finally {
        clearTimeout(timer)
        if (active === controller) active = undefined
      }
    }
  }

  function start() {
    if (worker || !queue.length || disposed) return
    worker = run().finally(() => { worker = undefined; start() })
  }

  function accept(event) {
    if (disposed) return
    if (event?.type === 'turn/start') {
      currentTurn = Number.isSafeInteger(event.data?.turn) && event.data.turn >= 0 ? event.data.turn : undefined
      return
    }
    if (event?.type !== 'user/message' || event.surfaceOp !== 'append' || event.sourceEventSeqs !== undefined ||
        event.data?.role !== 'user' || event.data.source?.kind !== 'user' || event.data.source.form !== undefined ||
        !Number.isSafeInteger(event.seq) || event.seq < 0 || event.seq <= lastSeen) return
    lastSeen = event.seq
    if (!permitted()) return
    if (typeof sessionId !== 'string' || !sessionId || sessionId.length > 128) { state = 'unavailable'; reason = 'invalid_session'; return }
    const blocks = event.data.content
    if (!Array.isArray(blocks) || blocks.length === 0 || blocks.some(block => block.type !== 'text' || typeof block.text !== 'string')) return
    // Check before joining so oversized messages cannot multiply temporary allocations.
    if (blocks.reduce((size, block) => size + block.text.length + 1, -1) > 50000) { state = 'unavailable'; reason = 'input_too_large'; return }
    const text = blocks.map(block => block.text).join('\n')
    if (!text.trim()) return
    if (queue.length >= 16) { state = 'unavailable'; reason = 'queue_full'; return }
    const key = createHash('sha256').update(JSON.stringify([config.scopeKey, sessionId, event.seq, event.data.id])).digest('hex')
    queue.push({ generation, payload: {
      text, user_id: binding.userId, source: 'dsh-dialogue', session_id: sessionId,
      ...(currentTurn === undefined ? {} : { turn: currentTurn }),
      idempotency_key: `dsh:${key}`,
      attribution: { version: 1, request_session_id: sessionId, request_id: `dsh:${key}` },
    } })
    start()
  }

  async function drain() { while (worker) await worker }
  async function dispose() {
    if (!disposed) { disposed = true; invalidate('disposed') }
    await drain()
  }
  return Object.freeze({ accept, status, invalidate, drain, dispose })
}
