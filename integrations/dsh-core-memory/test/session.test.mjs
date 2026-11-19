import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { once } from 'node:events'
import path from 'node:path'
import { normalizeConfig } from '../lib/core.js'
import { createSessionMemory } from '../lib/session.js'

async function fixture(t, { handler, credential, timeoutMs = 1000 } = {}) {
  let count = 0
  let version = 1
  const server = createServer((req, res) => {
    count++
    if (handler) return handler(req, res, count)
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ blocks: [{ id: 'core-policy', block: 'policy', namespace: 'fixture', user_id: 'fixture-user', tenant_id: null, content: `MEMORY_SENTINEL_${version}`, version, updated_at: '2026-11-19T00:00:00Z' }] }))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) })
  const config = normalizeConfig({ enabled: true, baseUrl: `http://127.0.0.1:${server.address().port}`, allowLoopbackHttp: true, apiKeyEnv: 'TEST_CORE_TOKEN', timeoutMs, bindings: [{ workspace: process.cwd(), namespace: 'fixture', userId: 'fixture-user' }] })
  let workspace = process.cwd()
  const memory = createSessionMemory({ config, binding: config.bindings[0], getWorkspace: () => workspace, resolveCredential: credential ?? (async () => ({ value: 'fixture-token' })) })
  t.after(() => memory.dispose())
  return { memory, config, count: () => count, version: value => { version = value }, workspace: value => { workspace = value } }
}

test('load obtains a stable bounded snapshot once until explicit refresh', async t => {
  const f = await fixture(t)
  assert.equal(f.memory.status().state, 'idle')
  const loaded = await f.memory.load()
  assert.equal(loaded.state, 'ready')
  assert.equal(loaded.outcome, 'loaded')
  assert(f.memory.text().includes('MEMORY_SENTINEL_1'))
  const text = f.memory.text()
  assert.equal(f.memory.text(), text)
  assert.equal(f.count(), 1)
  assert(!JSON.stringify(f.memory.status()).includes('MEMORY_SENTINEL'))
  assert(!JSON.stringify(loaded).includes('fixture-token'))
})

test('explicit refresh changes version and text only after a successful read', async t => {
  const f = await fixture(t)
  await f.memory.load()
  f.version(2)
  const result = await f.memory.load()
  assert.equal(result.outcome, 'loaded')
  assert.deepEqual(result.versions, [{ block: 'policy', version: 2 }])
  assert(f.memory.text().includes('MEMORY_SENTINEL_2'))
  assert.equal(f.count(), 2)
})

test('credentials are resolved afresh, never cached across operations', async t => {
  let resolves = 0
  const f = await fixture(t, { credential: async () => { resolves++; return { value: `token-${resolves}` } } })
  await f.memory.load()
  await f.memory.load()
  assert.equal(resolves, 2)
})

test('unresolved credentials are inside the same overall wait budget', async t => {
  const f = await fixture(t, { timeoutMs: 50, credential: () => new Promise(() => {}) })
  const result = await f.memory.load()
  assert.equal(result.reason, 'timeout')
  assert.equal(f.count(), 0)
  assert(!f.memory.text().includes('MEMORY_SENTINEL'))
})

test('HTTP stalls are cancelled within the operation budget', async t => {
  const f = await fixture(t, { timeoutMs: 50, handler: () => {} })
  const result = await f.memory.load()
  assert.equal(result.state, 'unavailable')
  assert.equal(result.reason, 'timeout')
})

test('missing credentials do not issue a request', async t => {
  const f = await fixture(t, { credential: async () => undefined })
  const result = await f.memory.load()
  assert.equal(result.reason, 'missing_credentials')
  assert.equal(f.count(), 0)
})

test('workspace change clears prior data and returning does not resurrect it', async t => {
  const f = await fixture(t)
  await f.memory.load()
  f.workspace(path.join(process.cwd(), 'another-project'))
  assert(!f.memory.text().includes('MEMORY_SENTINEL'))
  assert.equal(f.memory.status().reason, 'scope_changed')
  f.workspace(process.cwd())
  assert(!f.memory.text().includes('MEMORY_SENTINEL'))
  await f.memory.load()
  assert(f.memory.text().includes('MEMORY_SENTINEL'))
})

test('in-flight response cannot publish across a workspace change', async t => {
  let release
  const reached = new Promise(resolve => { release = resolve })
  let response
  const f = await fixture(t, { handler: (_req, res) => { response = res; release() } })
  const pending = f.memory.load()
  await reached
  f.workspace(path.join(process.cwd(), 'another-project'))
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify({ blocks: [] }))
  const result = await pending
  assert.equal(result.reason, 'scope_changed')
  assert(!f.memory.text().includes('MEMORY_SENTINEL'))
})

test('explicit invalidation aborts a pending read and blocks late publication', async t => {
  let reached
  const entered = new Promise(resolve => { reached = resolve })
  const f = await fixture(t, { handler: () => reached() })
  const pending = f.memory.load()
  await entered
  f.memory.invalidate('scope_changed')
  assert.equal((await pending).reason, 'scope_changed')
})

test('cancelled initialization resolves without making agent creation fail', async t => {
  const f = await fixture(t, { credential: () => new Promise(() => {}) })
  const controller = new AbortController()
  const pending = f.memory.load(controller.signal)
  controller.abort()
  assert.equal((await pending).reason, 'cancelled')
})

test('latest refresh wins and superseded callers receive an explicit outcome', async t => {
  let firstResolve
  let count = 0
  const f = await fixture(t, { credential: () => { count++; return count === 1 ? new Promise(resolve => { firstResolve = resolve }) : Promise.resolve({ value: 'new-token' }) } })
  const first = f.memory.load()
  const second = await f.memory.load()
  firstResolve({ value: 'old-token' })
  assert.equal(second.outcome, 'loaded')
  assert.equal((await first).outcome, 'superseded')
  assert.equal(f.count(), 1)
})

test('failed refresh invalidates the old snapshot and exposes no remote error body', async t => {
  let fail = false
  const f = await fixture(t, { credential: async () => { if (fail) throw Error('SECRET_TOKEN'); return { value: 'fixture-token' } } })
  await f.memory.load()
  fail = true
  const result = await f.memory.load()
  assert.equal(result.state, 'unavailable')
  assert.equal(result.reason, 'credential_error')
  assert(!JSON.stringify(result).includes('SECRET_TOKEN'))
  assert(!f.memory.text().includes('MEMORY_SENTINEL'))
})

test('dispose cancels unresolved work and removes memory immediately', async t => {
  const f = await fixture(t, { credential: () => new Promise(() => {}) })
  const pending = f.memory.load()
  await f.memory.dispose()
  assert.equal((await pending).state, 'disposed')
  assert.equal(f.memory.text(), '')
  assert.equal((await f.memory.load()).state, 'disposed')
})

test('already cancelled operations do not even resolve credentials', async t => {
  let called = false
  const f = await fixture(t, { credential: async () => { called = true; return { value: 'fixture-token' } } })
  const controller = new AbortController()
  controller.abort()
  assert.equal((await f.memory.load(controller.signal)).reason, 'cancelled')
  assert.equal(called, false)
  assert.equal(f.count(), 0)
})

test('workspace observation failure degrades instead of breaking context assembly', async t => {
  const f = await fixture(t)
  const memory = createSessionMemory({ config: f.config, binding: f.config.bindings[0], getWorkspace: () => { throw Error('PRIVATE_PATH') }, resolveCredential: async () => ({ value: 'fixture-token' }) })
  t.after(() => memory.dispose())
  assert.equal((await memory.load()).reason, 'scope_changed')
  assert.doesNotThrow(() => memory.text())
  assert(!JSON.stringify(memory.status()).includes('PRIVATE_PATH'))
})

test('session caches are independent even within the same workspace', async t => {
  const f = await fixture(t)
  const other = createSessionMemory({ config: f.config, binding: f.config.bindings[0], getWorkspace: () => process.cwd(), resolveCredential: async () => undefined })
  t.after(() => other.dispose())
  await f.memory.load()
  assert.equal(other.status().state, 'idle')
  assert(!other.text().includes('MEMORY_SENTINEL'))
})
