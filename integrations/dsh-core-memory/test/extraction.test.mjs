import test from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { once } from 'node:events'
import http from 'node:http'
import { createSessionExtractor, submitDialogue } from '../lib/extraction.js'
import { workspaceKey } from '../lib/core.js'

const workspace = path.resolve('fixture-workspace')
const binding = { workspaceKey: workspaceKey(workspace), userId: 'fixture-user', tenantId: null, namespace: 'fixture' }
const config = { transport: 'direct', baseUrl: '', authMode: 'bearer', timeoutMs: 300, maxResponseBytes: 2048, scopeKey: 'a'.repeat(64), apiKeyEnv: 'FIXTURE_TOKEN' }
const userEvent = (seq, text = '我喜欢用清楚简洁的中文说明。') => ({ seq, type: 'user/message', surfaceOp: 'append', data: { id: `message-${seq}`, role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text }] } })
function fixture(t, extra = {}) {
  const requests = []
  const f = { enabled: false, directory: workspace, reads: 0 }
  const extractor = createSessionExtractor({
    config, binding, sessionId: 'fixture-session', getWorkspace: () => f.directory,
    authorize: () => f.enabled,
    resolveCredential: async () => { f.reads++; return { value: 'fixture-secret' } },
    writeDialogue: async (_config, _binding, token, payload, signal) => {
      signal.throwIfAborted(); requests.push({ token, payload }); return { task_id: 'task_fixture', status: 'queued' }
    }, ...extra,
  })
  t.after(() => extractor.dispose())
  return Object.assign(f, { extractor, requests })
}

test('automatic extraction defaults off and only accepts new direct user text while enabled', async t => {
  const f = fixture(t)
  f.extractor.accept(userEvent(1, 'before-enable'))
  await f.extractor.drain()
  assert.equal(f.reads, 0)
  f.enabled = true
  f.extractor.accept({ seq: 2, type: 'turn/start', data: { turn: 3 } })
  f.extractor.accept(userEvent(3))
  await f.extractor.drain()
  assert.equal(f.requests.length, 1)
  assert.equal(f.requests[0].payload.text, '我喜欢用清楚简洁的中文说明。')
  assert.equal(f.requests[0].payload.user_id, 'fixture-user')
  assert.equal(f.requests[0].payload.session_id, 'fixture-session')
  assert.equal(f.requests[0].payload.turn, 3)
  assert.equal(f.requests[0].payload.source, 'dsh-dialogue')
  assert.match(f.requests[0].payload.idempotency_key, /^dsh:[a-f0-9]{64}$/u)
  assert.equal(f.requests[0].payload.attribution.request_session_id, 'fixture-session')
  assert.equal(f.extractor.status().submittedCount, 1)
  assert.equal(f.extractor.status().state, 'submitted')
  assert.doesNotMatch(JSON.stringify(f.extractor.status()), /fixture-secret|我喜欢/u)
})

test('system snapshots, tool and assistant content, attachments, replacements and duplicate events never submit', async t => {
  const f = fixture(t); f.enabled = true
  const base = userEvent(10)
  for (const event of [
    { ...base, type: 'system/message' }, { ...base, type: 'assistant/message' }, { ...base, type: 'tool/result' },
    { ...base, data: { ...base.data, source: { kind: 'runtime-context', form: 'snapshot' } } },
    { ...base, data: { ...base.data, source: { kind: 'goal-round' } } },
    { ...base, data: { ...base.data, content: [{ type: 'image', source: { type: 'url', url: 'file://private' } }] } },
    { ...base, surfaceOp: { op: 'replace', startSeq: 1, endSeq: 2 } },
    { ...base, sourceEventSeqs: [1] },
  ]) f.extractor.accept(event)
  await f.extractor.drain()
  assert.equal(f.requests.length, 0)
  f.extractor.accept(userEvent(20, 'fresh'))
  f.extractor.accept(userEvent(20, 'fresh'))
  await f.extractor.drain()
  assert.equal(f.requests.length, 1)
})

test('disable during credential resolution drops the in-flight event and queued text without a write', async t => {
  const started = Promise.withResolvers(); const held = Promise.withResolvers()
  const f = fixture(t, { resolveCredential: async () => { started.resolve(); return held.promise } }); f.enabled = true
  f.extractor.accept(userEvent(1)); await started.promise
  f.extractor.accept(userEvent(2, 'queued'))
  f.enabled = false; f.extractor.invalidate('disabled')
  await f.extractor.drain()
  held.resolve({ value: 'late-private-secret' })
  assert.equal(f.requests.length, 0)
  f.enabled = true
  f.extractor.accept(userEvent(3, 'fresh-after-enable'))
  await f.extractor.drain()
  assert.equal(f.requests.length, 1)
  assert.equal(f.requests[0].payload.text, 'fresh-after-enable')
})

test('scope changes, oversized input and network errors are bounded and observable', async t => {
  const f = fixture(t); f.enabled = true; f.directory = path.resolve('unconfigured')
  f.extractor.accept(userEvent(1)); await f.extractor.drain()
  assert.equal(f.requests.length, 0)
  f.directory = workspace
  f.extractor.accept(userEvent(2, 'a'.repeat(50001))); await f.extractor.drain()
  assert.equal(f.extractor.status().reason, 'input_too_large')
  assert.equal(f.requests.length, 0)
  const bad = fixture(t, { writeDialogue: async () => { throw new Error('private-remote-body') } }); bad.enabled = true
  bad.extractor.accept(userEvent(1)); await bad.extractor.drain()
  assert.equal(bad.extractor.status().state, 'unavailable')
  assert.equal(bad.extractor.status().reason, 'transport_error')
  assert.doesNotMatch(JSON.stringify(bad.extractor.status()), /private-remote-body/u)
})

test('an authenticated identity outside the binding never receives dialogue text', async t => {
  const requests = []
  const server = http.createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    requests.push({ method: request.method, url: request.url, body: Buffer.concat(chunks).toString('utf8') })
    const body = request.url === '/dialogue/identity'
      ? { user_id: 'other-owner', tenant_id: 'fixture-tenant' }
      : { task_id: 'task_fixture', status: 'queued' }
    response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(body))
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const settings = { ...config, baseUrl: `http://127.0.0.1:${server.address().port}/` }
  const scope = { ...binding, tenantId: 'fixture-tenant' }
  const payload = { text: '必须留在会话内的合成文字', user_id: binding.userId }
  const mismatch = { code: 'ownership_mismatch' }
  await assert.rejects(submitDialogue(settings, scope, 'fixture-token', payload, AbortSignal.timeout(1000)), mismatch)
  assert.deepEqual(requests, [{ method: 'GET', url: '/dialogue/identity', body: '' }])
  await assert.rejects(submitDialogue({ ...settings, authMode: 'api-key' }, scope, 'fixture-token', payload, AbortSignal.timeout(1000)), mismatch)
  assert.equal(requests.length, 1, 'an API key with a mismatched configured owner must make no request')
})

test('an unverified environment API key never sends text to a development server', async t => {
  const requests = []
  const server = http.createServer((request, response) => {
    requests.push(`${request.method} ${request.url}`)
    response.writeHead(request.url === '/admin/api/sysinfo' ? 403 : 200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ task_id: 'task_fixture', status: 'queued' }))
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const settings = { ...config, authMode: 'api-key', baseUrl: `http://127.0.0.1:${server.address().port}/` }
  const scope = { ...binding, userId: 'api_key' }
  const payload = { text: 'synthetic user text', user_id: 'api_key' }
  await assert.rejects(submitDialogue(settings, scope, 'fixture-token', payload, AbortSignal.timeout(1000)), { code: 'authentication_failed' })
  assert.deepEqual(requests, ['GET /admin/api/sysinfo'])
})

test('real HTTP submission checks identity metadata once before submitting UTF-8 dialogue', async t => {
  const requests = []
  const server = http.createServer(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk)
    requests.push({ method: request.method, url: request.url, headers: request.headers, body: Buffer.concat(chunks).toString('utf8') })
    const body = request.url === '/dialogue/identity'
      ? { user_id: binding.userId, tenant_id: 'fixture-tenant' }
      : { task_id: 'task_fixture', status: 'queued' }
    response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(body))
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const payload = { text: '偏好中文 🍵', user_id: 'fixture-user', session_id: 'fixture-session', idempotency_key: 'dsh:fixture' }
  const scoped = { ...binding, tenantId: 'fixture-tenant' }
  const result = await submitDialogue({ ...config, baseUrl: `http://127.0.0.1:${server.address().port}/` }, scoped, 'fixture-token', payload, AbortSignal.timeout(1000))
  assert.deepEqual(result, { task_id: 'task_fixture', status: 'queued' })
  assert.deepEqual(requests.map(({ method, url }) => [method, url]), [
    ['GET', '/dialogue/identity'], ['POST', '/dialogue/async'],
  ])
  assert.equal(requests[0].body, '')
  assert.equal(requests[1].body, JSON.stringify(payload))
  for (const request of requests) {
    assert.equal(request.headers.authorization, 'Bearer fixture-token')
    assert.equal(request.headers['x-tenant-id'], 'fixture-tenant')
  }
  assert.deepEqual(JSON.parse(requests[0].body || '{}'), {})
})

test('cancelling a pending identity check never sends dialogue text', async t => {
  const requests = []
  const identityReceived = Promise.withResolvers()
  const server = http.createServer(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk)
    const body = Buffer.concat(chunks).toString('utf8')
    requests.push({ method: request.method, url: request.url, body })
    if (request.url === '/dialogue/identity') {
      identityReceived.resolve()
      return
    }
    response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ task_id: 'task_fixture', status: 'queued' }))
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const controller = new AbortController()
  const pending = submitDialogue({ ...config, baseUrl: `http://127.0.0.1:${server.address().port}/` },
    { ...binding, tenantId: 'fixture-tenant' }, 'fixture-token',
    { text: '不应发送', user_id: binding.userId }, controller.signal)
  await identityReceived.promise
  controller.abort()
  await assert.rejects(pending, { code: 'aborted' })
  assert.deepEqual(requests, [{ method: 'GET', url: '/dialogue/identity', body: '' }])
})

test('a legacy server without identity metadata rejects safely before dialogue text', async t => {
  const requests = []
  const server = http.createServer(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk)
    requests.push({ method: request.method, url: request.url, body: Buffer.concat(chunks).toString('utf8') })
    response.writeHead(request.url === '/dialogue/identity' ? 404 : 200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ task_id: 'task_fixture', status: 'queued' }))
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  await assert.rejects(submitDialogue({ ...config, baseUrl: `http://127.0.0.1:${server.address().port}/` },
    { ...binding, tenantId: 'fixture-tenant' }, 'fixture-token', { text: '不应发送', user_id: binding.userId }, AbortSignal.timeout(1000)), { code: 'identity_unavailable' })
  assert.deepEqual(requests, [{ method: 'GET', url: '/dialogue/identity', body: '' }])
})
