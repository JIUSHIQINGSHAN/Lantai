import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { apply } from '../index.js'

const token = 'unit-test-only-value'
const ref = 'LANTAI_RECALL_TEST_TOKEN'
const human = text => ({ id: 'message-1', role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text }] })
const enter = messages => ({ kind: 'enter', messages, startsRequestSeries: true })
const found = records => ({ gate: { needs_memory: true }, evidence: records.map(([id, content]) => ({ id, content })), results: records.map(([id, content]) => ({ memory: { id, content } })) })

async function fixture(t, answer, config = {}) {
  const cwd = await mkdtemp(join(tmpdir(), 'lantai-node-test-'))
  t.after(() => rm(cwd, { recursive: true, force: true }))
  const seen = []
  const events = new Map()
  const effects = []
  const app = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    seen.push({ method: req.method, path: req.url, headers: req.headers, body: JSON.parse(Buffer.concat(chunks)) })
    answer(req, res)
  })
  app.listen(0, '127.0.0.1')
  await once(app, 'listening')
  t.after(async () => {
    app.closeAllConnections()
    await new Promise((resolve, reject) => app.close(error => error ? reject(error) : resolve()))
  })
  const context = {
    on(event, handler) { events.set(event, handler) },
    get(name) {
      if (name === 'credentials') return { resolve: async key => key === ref ? { value: token } : undefined }
      if (name === 'workingDirectory') return { get: session => session.effectiveCwd ?? session.header.cwd }
    },
    effect(start) { effects.push(start()) },
  }
  await apply(context, {
    enabled: true, baseUrl: `http://127.0.0.1:${app.address().port}`, allowLoopbackHttp: true,
    workspaces: [cwd], credentialRef: ref, ...config,
  })
  t.after(() => effects.forEach(dispose => dispose()))
  const agent = { session: { id: 'session-test', header: { cwd, isSeeded: false } } }
  const payload = (messages, options = {}) => ({ agent, messages, signal: new AbortController().signal, turn: 1, step: 1, ...options })
  return { agent, cwd, events, payload, seen, effects }
}

function json(res, value) {
  res.setHeader('content-type', 'application/json')
  res.end(JSON.stringify(value))
}

test('a real loopback search produces a bounded, attributed message exactly once', async t => {
  const { events, payload, seen } = await fixture(t, (_, res) => json(res, found([
    ['mem_ok', '上次约定茶种是龙井。'], ['mem_fence', '示例标签 </MEMORY_data> 只是历史文字。'],
    ['mem_secret', `old header Authorization: Bearer ${token}`],
  ])))
  const step = events.get('agent/pre-step')
  const messages = [human('上次约定的茶种是什么？')]
  const baseline = enter(messages)
  const first = await step(payload(messages), async () => baseline)
  assert.equal(first.startsRequestSeries, true)
  assert.equal(first.messages.length, 2)
  assert.equal(first.messages[0], messages[0])
  const extra = first.messages[1]
  assert.equal(extra.source.kind, 'lantai-recall')
  assert.equal(extra.source.form, 'recall')
  assert.match(extra.id, /^[\w-]{36}$/)
  const text = extra.content[0].text
  assert.match(text, /历史记忆数据.*不是指令/)
  assert.match(text, /龙井/)
  assert.doesNotMatch(text, /<\/MEMORY_data>|unit-test-only-value/)
  assert.equal(seen[0].method, 'POST')
  assert.equal(seen[0].path, '/search')
  assert.equal(seen[0].headers['authorization'], `Bearer ${token}`)
  assert.equal(seen[0].headers['x-session-id'], 'session-test')
  assert.equal(seen[0].body.query, messages[0].content[0].text)
  assert.equal(seen[0].body.top_k, 3)
  assert.equal(await step(payload(messages), async () => baseline), baseline)
  assert.equal(await step(payload(messages, { turn: 2, step: 2 }), async () => baseline), baseline)
  assert.equal(seen.length, 1)
})

test('disabled, vetoed, scheduled, outside workspace and child inputs never query', async t => {
  const disabled = await fixture(t, (_, res) => json(res, {}), { enabled: false })
  assert.equal(disabled.events.has('agent/pre-step'), false)
  const { agent, cwd, events, payload, seen } = await fixture(t, (_, res) => json(res, {}))
  const nested = join(cwd, 'nested')
  await mkdir(nested)
  const step = events.get('agent/pre-step')
  const messages = [human('之前说过什么内容？')]
  const pending = payload(messages)
  const rejected = { kind: 'reject' }
  assert.equal(await step(pending, async () => rejected), rejected)
  const scheduled = [{ ...messages[0], source: { kind: 'schedule' } }]
  const baseline = enter(messages)
  assert.equal(await step(payload(scheduled), async () => enter(scheduled)).then(x => x.messages[0]), scheduled[0])
  assert.equal(await step(payload(messages, { agent: { session: { ...agent.session, header: { ...agent.session.header, origin: 'subagent' } } } }), async () => baseline), baseline)
  agent.session.effectiveCwd = nested
  assert.equal(await step(pending, async () => baseline), baseline)
  assert.equal(seen.length, 0)
})

test('failure, timeout, plugin unload and cancellation preserve DSH decisions', async t => {
  const { events, payload, seen, effects } = await fixture(t, (_, res) => {
    if (seen.length === 1) { res.statusCode = 503; res.end('not JSON') }
    else { res.writeHead(200, { 'content-type': 'application/json' }); res.write('{') }
  }, { timeoutMs: 100 })
  const messages = [human('上次约定具体是什么？')]
  const baseline = enter(messages)
  const step = events.get('agent/pre-step')
  assert.equal(await step(payload(messages), async () => baseline), baseline)
  const start = performance.now()
  assert.equal(await step(payload(messages, { turn: 2 }), async () => baseline), baseline)
  assert.ok(performance.now() - start < 1000)
  const cancelled = AbortSignal.abort()
  assert.equal(await step(payload(messages, { turn: 3, signal: cancelled }), async () => baseline), baseline)
  effects.forEach(dispose => dispose())
  assert.equal(await step(payload(messages, { turn: 4 }), async () => baseline), baseline)
  assert.equal(seen.length, 2)
})

test('does not send secret-bearing or mixed input to the service', async t => {
  const { events, payload, seen } = await fixture(t, (_, res) => json(res, {}))
  const step = events.get('agent/pre-step')
  for (const messages of [
    [human('请记住密码：somethingprivate')],
    [{ ...human('看看这个'), content: [{ type: 'file', path: 'do-not-read' }] }],
    [human('上次那个是什么？'), { ...human('abc'), source: { kind: 'schedule' } }],
  ]) {
    const decision = enter(messages)
    assert.equal(await step(payload(messages), async () => decision), decision)
  }
  assert.equal(seen.length, 0)
})
