'use strict'

// Run only with the installed Electron runtime. No profile, credentials, disk
// memory, real Agent, SSH process or model is opened by this composition.
const assert = require('node:assert/strict')
const path = require('node:path')
const os = require('node:os')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const { test } = require('node:test')
const { createControlContribution } = require('../lib/control-contract.cjs')

const directory = path.resolve(__dirname, '..')
const status = () => ({ state: 'ready', reason: 'authorized', authorized: true, blockCount: 1, contextBytes: 12, versions: [{ block: 'policy', version: 2 }] })
const ownerError = error => error?.isDSHRemoteError === true && error.code === 'lantai-memory/owner-required'

async function runtime(t) {
  assert.ok(process.versions.electron, 'use installed Electron with ELECTRON_RUN_AS_NODE=1')
  const requireInstalled = createRequire(path.join(process.env.DSH_INSTALL_DIR, 'package.json'))
  assert.equal(requireInstalled('./package.json').version, '0.2.2-rc.5')
  const load = name => import(pathToFileURL(requireInstalled.resolve(`@deepseek-ai/${name}`)).href)
  const { Context } = await load('cordis')
  const { PluginPackages, createRuntimeResolution } = await load('dsh-app-boot')
  const root = new Context()
  t.after(() => root.fiber.dispose())
  // An intentionally nonexistent synthetic home prevents real-profile lookup.
  // Only the installed package graph is read; this function creates no files.
  const resolution = await createRuntimeResolution({
    installAnchor: path.join(process.env.DSH_INSTALL_DIR, 'package.json'),
    home: path.join(os.tmpdir(), `lantai-control-in-memory-${process.pid}`),
  })
  await root.plugin(PluginPackages, { resolution: {
    profilesDir: resolution.profilesDir,
    profileDir: path.join(resolution.profilesDir, 'isolated-control'),
    entries: resolution.entries,
    localPackageNames: ['@lantai/dsh-core-memory'],
    linkedRoots: [{ name: '@lantai/dsh-core-memory', realPath: directory }],
  } })
  await root.plugin((await load('dsh-typert-registry')).default)
  await root.plugin((await load('dsh-api-remote-access')).default)
  const { HostConnectionService } = await load('dsh-client-connection')
  await root.plugin(ctx => { new HostConnectionService(ctx, [], undefined) })
  const { TypertGatewayService } = await load('dsh-api-gateway')
  await root.plugin(TypertGatewayService, {})
  const control = await import(pathToFileURL(path.join(directory, 'lib/control.js')).href)
  return { root, load, Context, control, Service: (await load('cordis')).Service }
}

test('real Gateway owner dispatch supplies trusted invocation and strictly parsed metadata', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  const calls = []
  let dispose
  await root.plugin(Object.assign(ctx => {
    dispose = control.registerOwnerControl(ctx, Object.fromEntries(['authorize', 'revoke', 'status'].map(method => [method, (id, invocation, signal) => {
      calls.push({ method, id, invocation, signal })
      return status()
    }])))
  }, { inject: ['typert'] }))
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  for (const method of ['authorize', 'revoke', 'status']) {
    assert.deepEqual(await root.typertGateway.invoke({ namespace: 'lantaiCoreMemory', method, args: { sessionId: 'session-1' }, peer }), status())
  }
  assert.equal(calls.length, 3)
  for (const call of calls) {
    assert.equal(call.id, 'session-1')
    assert.equal(call.invocation.peer, peer)
    assert.equal(call.invocation.peer.principal.kind, 'owner')
    assert.equal(call.invocation.signal.aborted, false)
    assert.equal(call.signal.aborted, false)
    assert.equal(typeof call.signal.throwIfAborted, 'function')
  }
  assert.equal(typeof dispose, 'function')
  await dispose()
})

async function install(root, control, operations) {
  let dispose
  const fiber = root.plugin(Object.assign(ctx => {
    dispose = control.registerOwnerControl(ctx, operations)
  }, { inject: ['typert'] }))
  await fiber
  return { fiber, dispose }
}

function invoke(root, peer, method = 'authorize', args = { sessionId: 'session-1' }, signal) {
  return root.typertGateway.invoke({ namespace: 'lantaiCoreMemory', method, args, peer, signal })
}

test('direct calls without Host invocation reject before operation lookup, even with forged arguments', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let lookups = 0
  const operations = Object.fromEntries(['authorize', 'revoke', 'status'].map(method => [method, () => { throw new Error('must not run') }]))
  Object.defineProperty(operations, 'authorize', { get() { lookups++; throw new Error('operation lookup must not run') } })
  await install(root, control, operations)
  for (const method of ['authorize', 'revoke', 'status']) {
    await assert.rejects(root.lantaiCoreMemory[method]('session-1', {
      peer: { principal: { kind: 'owner' } }, signal: new AbortController().signal,
    }), ownerError)
  }
  assert.equal(lookups, 0)
})

test('nonowner remains refused by handler even when real RemoteAccess policy allows every endpoint', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  const actions = ['authorize', 'revoke', 'status'].map(method => `lantaiCoreMemory/${method}`)
  root.remoteAccess.registerPolicy('fixture-viewer', {
    bindings: () => [{ role: { name: 'fixture-allowed', resource: 'global', actions }, resource: { kind: 'global' } }],
  })
  await install(root, control, Object.fromEntries(['authorize', 'revoke', 'status'].map(method => [method, () => { calls++; return status() }])))
  const peer = root.connection.createPeer({ principal: { kind: 'fixture-viewer' } })
  assert.deepEqual(root.remoteAccess.decide({ principal: peer.principal, action: actions[0], resources: [{ kind: 'global' }] }), { kind: 'allow' })
  for (const method of ['authorize', 'revoke', 'status']) await assert.rejects(invoke(root, peer, method), ownerError)
  assert.equal(calls, 0)
})

test('wire principal or invocation fields cannot promote a nonowner or reach callback', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { authorize() { calls++; return status() } })
  const peer = root.connection.createPeer({ principal: { kind: 'fixture-viewer' } })
  for (const args of [
    { sessionId: 'session-1', principal: { kind: 'owner' } },
    { sessionId: 'session-1', invocation: { peer: { principal: { kind: 'owner' } } } },
    { sessionId: { principal: { kind: 'owner' } } },
    { sessionId: 'session-1', signal: { aborted: false } },
  ]) await assert.rejects(invoke(root, peer, 'authorize', args), error => error.code === 'gateway/arguments-invalid' || error.code === 'gateway/input-invalid')
  assert.equal(calls, 0)
  assert.equal(peer.principal.kind, 'fixture-viewer')
})

test('real Gateway validates exact input before any operation callback', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { authorize() { calls++; return status() } })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  for (const args of [{}, { sessionId: '' }, { sessionId: 7 }, { sessionId: 's\n' }, { sessionId: 'x'.repeat(1025) }]) {
    await assert.rejects(invoke(root, peer, 'authorize', args), error => /^gateway\/(?:arguments-invalid|input-invalid)$/.test(error.code))
  }
  assert.equal(calls, 0)
})

test('unsafe callback output and callback exceptions become fixed RemoteErrors without secrets', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let output = { ...status(), content: 'fixture-secret-content', key: 'fixture-secret-key' }
  await install(root, control, {
    authorize() { return output }, revoke() { throw new Error('fixture-secret-key') },
    status() { return Promise.reject(new Error('fixture-secret-content')) },
  })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  const invalid = error => {
    assert.equal(error.code, 'lantai-memory/invalid-result')
    assert.equal(error.message, 'Invalid control metadata')
    assert.deepEqual(error.details, {})
    assert.equal(error.cause, undefined)
    return true
  }
  await assert.rejects(invoke(root, peer), invalid)
  output = { ...status(), versions: [{ block: 'policy', version: 1, content: 'fixture-secret-content' }] }
  await assert.rejects(invoke(root, peer), invalid)
  for (const method of ['revoke', 'status']) await assert.rejects(invoke(root, peer, method), error => {
    assert.equal(error.code, 'lantai-memory/control-failed')
    assert.equal(error.message, 'Owner control failed')
    assert.deepEqual(error.details, {})
    assert.equal(error.cause, undefined)
    return true
  })
})

test('preaborted request and disposed Host Peer cannot reach operation callbacks', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { authorize() { calls++; return status() } })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  await assert.rejects(invoke(root, peer, 'authorize', { sessionId: 's' }, AbortSignal.abort()), error => error.code === 'gateway/cancelled')
  await peer.dispose()
  await assert.rejects(invoke(root, peer), error => error.code === 'gateway/peer-disposed')
  assert.equal(calls, 0)
})

test('explicit disposer and parent unload withdraw descriptors and reject retained entry handles', { timeout: 20000 }, async t => {
  for (const teardown of ['disposer', 'parent']) {
    const { root, control } = await runtime(t)
    let calls = 0
    const mounted = await install(root, control, { authorize() { calls++; return status() } })
    const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
    const retained = root.lantaiCoreMemory
    if (teardown === 'disposer') { await mounted.dispose(); await mounted.dispose() } else await mounted.fiber.dispose()
    await assert.rejects(invoke(root, peer), error => error.code === 'gateway/definition-unavailable')
    await assert.rejects(retained.authorize('session-1'), ownerError)
    assert.equal(calls, 0)
  }
})

async function within(promise, message) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), 500) })])
  } finally { clearTimeout(timer) }
}

test('request/Peer cancellation and either unload path abort pending callbacks and suppress late results', { timeout: 20000 }, async t => {
  for (const teardown of ['request', 'peer', 'disposer', 'parent']) {
    const { root, control } = await runtime(t)
    const request = new AbortController()
    let entered
    let release
    const started = new Promise(resolve => { entered = resolve })
    const held = new Promise(resolve => { release = resolve })
    t.after(() => release(status()))
    let callbackSignal
    let calls = 0
    const mounted = await install(root, control, { authorize(id, invocation, signal) {
      calls++
      callbackSignal = signal
      entered()
      return held
    } })
    const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
    const outcome = invoke(root, peer, 'authorize', { sessionId: 's' }, request.signal).then(value => ({ value }), error => ({ error }))
    await within(started, 'callback did not begin')
    if (teardown === 'request') request.abort()
    else if (teardown === 'peer') await peer.dispose()
    else if (teardown === 'disposer') await mounted.dispose()
    else await mounted.fiber.dispose()
    assert.equal(callbackSignal.aborted, true, `${teardown} must abort the operation signal`)
    const result = await within(outcome, `${teardown} did not reject an uncooperative pending operation`)
    assert.equal(result.error?.code, 'gateway/cancelled')
    assert.equal(calls, 1)
    release({ ...status(), key: 'late-secret-must-not-escape' })
    assert.equal((await outcome).value, undefined)
  }
})

test('real Client $mount accepts pure parser descriptors and invokes real Host Gateway through transport fixture', { timeout: 20000 }, async t => {
  const { root, control, Context, Service, load } = await runtime(t)
  const client = new Context()
  t.after(() => client.fiber.dispose())
  let calls = 0
  await install(root, control, Object.fromEntries(['authorize', 'revoke', 'status'].map(method => [method, () => { calls++; return status() }])))
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  const transmissions = []
  class FixtureTransport extends Service {
    constructor(ctx) {
      super(ctx, 'connection')
      this.interactions = 'none'
      // Only the external carrier is replaced. Registry, namespace mounting,
      // argument construction, cancellation, Gateway and Host Peer are real.
      this.rpc = {
        open() { throw new Error('control has no streams') },
        async call(channel, endpoint, payload, signal) {
          transmissions.push({ channel, endpoint, payload })
          const [namespace, method] = endpoint.split('/')
          try { return { ok: true, value: await root.typertGateway.invoke({ namespace, method, args: payload.args, signal, peer }) } }
          catch (error) { return { ok: false, error: { code: error.code, message: error.message, details: error.details } } }
        },
      }
    }
  }
  await client.plugin((await load('dsh-typert-registry')).default)
  await client.plugin(FixtureTransport)
  await client.plugin(await load('dsh-api-gateway/client'))
  const unmount = await client.remote.$mount(createControlContribution())
  assert.equal(client.typert.remotes.list().length, 3)
  for (const method of ['authorize', 'revoke', 'status']) {
    assert.deepEqual(await client.remote.lantaiCoreMemory[method]('session-client'), { ok: true, value: status() })
  }
  assert.equal(calls, 3)
  assert.equal(transmissions.length, 3)
  for (const transmission of transmissions) {
    assert.equal(transmission.channel, '/api')
    assert.deepEqual(Object.keys(transmission.payload), ['args'])
    assert.deepEqual({ ...transmission.payload.args }, { sessionId: 'session-client' })
  }
  const invalid = await client.remote.lantaiCoreMemory.authorize({ peer: { principal: { kind: 'owner' } } })
  assert.equal(invalid.ok, false)
  assert.equal(invalid.error.code, 'gateway/input-invalid')
  assert.equal(calls, 3)
  const retained = client.remote.lantaiCoreMemory.authorize
  await unmount()
  assert.equal(client.typert.remotes.list().length, 0)
  const withdrawn = await retained('session-client')
  assert.equal(withdrawn.ok, false)
  assert.equal(calls, 3)
})
