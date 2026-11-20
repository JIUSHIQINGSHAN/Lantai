'use strict'
// Real installed Gateway and Client transport contract, isolated from daily DSH.
const assert = require('node:assert/strict')
const path = require('node:path')
const os = require('node:os')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const { test } = require('node:test')
const { createControlContribution } = require('../lib/control-contract.cjs')
const directory = path.resolve(__dirname, '..')
const status = () => ({ enabled: true, writable: true, reason: 'ready', folderCount: 2, includesTask: false })
const ownerError = error => error?.isDSHRemoteError === true && error.code === 'lantai-memory/owner-required'
const argumentsError = error => /^gateway\/(?:arguments-invalid|input-invalid)$/.test(error.code)

async function runtime(t) {
  assert.ok(process.versions.electron, 'use installed Electron with ELECTRON_RUN_AS_NODE=1')
  const requireInstalled = createRequire(path.join(process.env.DSH_INSTALL_DIR, 'package.json'))
  assert.equal(requireInstalled('./package.json').version, '0.2.2-rc.5')
  const load = name => import(pathToFileURL(requireInstalled.resolve(`@deepseek-ai/${name}`)).href)
  const { Context, Service } = await load('cordis')
  const { PluginPackages, createRuntimeResolution } = await load('dsh-app-boot')
  const root = new Context()
  t.after(() => root.fiber.dispose())
  const resolution = await createRuntimeResolution({ installAnchor: path.join(process.env.DSH_INSTALL_DIR, 'package.json'), home: path.join(os.tmpdir(), `lantai-control-${process.pid}`) })
  await root.plugin(PluginPackages, { resolution: {
    profilesDir: resolution.profilesDir, profileDir: path.join(resolution.profilesDir, 'isolated-control'), entries: resolution.entries,
    localPackageNames: ['@lantai/dsh-core-memory'], linkedRoots: [{ name: '@lantai/dsh-core-memory', realPath: directory }],
  } })
  await root.plugin((await load('dsh-typert-registry')).default)
  await root.plugin((await load('dsh-api-remote-access')).default)
  const { HostConnectionService } = await load('dsh-client-connection')
  await root.plugin(ctx => { new HostConnectionService(ctx, [], undefined) })
  await root.plugin((await load('dsh-api-gateway')).TypertGatewayService, {})
  const control = await import(pathToFileURL(path.join(directory, 'lib/control.js')).href)
  return { root, load, Context, Service, control }
}
async function install(root, control, operations) {
  let dispose
  const fiber = root.plugin(Object.assign(ctx => { dispose = control.registerOwnerControl(ctx, operations) }, { inject: ['typert'] }))
  await fiber
  return { fiber, dispose }
}
const invoke = (root, peer, method = 'setEnabled', args = { enabled: true }, signal) => root.typertGateway.invoke({ namespace: 'lantaiCoreMemory', method, args, peer, signal })
async function within(promise, message) {
  let timer
  try { return await Promise.race([promise, new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error(message)), 1000) })]) }
  finally { clearTimeout(timer) }
}

test('real owner Gateway dispatch uses only global boolean settings and parsed metadata', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  const calls = []
  await install(root, control, { status() { calls.push('status'); return status() }, setEnabled(enabled, signal) { calls.push({ enabled, signal }); return status() } })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  assert.deepEqual(await invoke(root, peer, 'status', {}), status())
  assert.deepEqual(await invoke(root, peer), status())
  assert.equal(calls[0], 'status')
  assert.equal(calls[1].enabled, true)
  assert.equal(calls[1].signal.aborted, false)
})

test('direct calls without Host invocation reject before operation lookup', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  const operations = Object.defineProperty({}, 'setEnabled', { get() { calls++; throw Error('private') } })
  await install(root, control, operations)
  await assert.rejects(root.lantaiCoreMemory.setEnabled(true), ownerError)
  await assert.rejects(root.lantaiCoreMemory.status(), ownerError)
  assert.equal(calls, 0)
})

test('nonowner is denied even when RemoteAccess grants every global endpoint', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  const actions = ['status', 'setEnabled'].map(method => `lantaiCoreMemory/${method}`)
  root.remoteAccess.registerPolicy('fixture-viewer', { bindings: () => [{ role: { name: 'allowed', resource: 'global', actions }, resource: { kind: 'global' } }] })
  await install(root, control, { status() { calls++; return status() }, setEnabled() { calls++; return status() } })
  const peer = root.connection.createPeer({ principal: { kind: 'fixture-viewer' } })
  await assert.rejects(invoke(root, peer), ownerError)
  await assert.rejects(invoke(root, peer, 'status', {}), ownerError)
  assert.equal(calls, 0)
})

test('wire principal, session and path cannot impersonate authority or widen scope', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { setEnabled() { calls++; return status() }, status })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  for (const args of [{ enabled: true, principal: { kind: 'owner' } }, { enabled: true, sessionId: 's' }, { enabled: true, cwd: 'C:/private' }, { enabled: true, signal: {} }]) {
    await assert.rejects(invoke(root, peer, 'setEnabled', args), argumentsError)
  }
  await assert.rejects(invoke(root, peer, 'status', { enabled: true }), argumentsError)
  assert.equal(calls, 0)
})

test('real Gateway refuses all nonboolean choices before operation lookup', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { setEnabled() { calls++; return status() }, status })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  for (const args of [{}, { enabled: 'true' }, { enabled: 1 }, { enabled: null }, { enabled: {} }, { enabled: [] }]) await assert.rejects(invoke(root, peer, 'setEnabled', args), argumentsError)
  assert.equal(calls, 0)
})

test('unsafe results and exceptions produce fixed errors without private detail', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  await install(root, control, { setEnabled() { return { ...status(), key: 'private-secret' } }, status() { throw Error('private-secret') } })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  for (const [method, args, code] of [['setEnabled', { enabled: true }, 'lantai-memory/invalid-result'], ['status', {}, 'lantai-memory/control-failed']]) {
    await assert.rejects(invoke(root, peer, method, args), error => {
      assert.equal(error.code, code)
      assert.deepEqual(error.details, {})
      assert.equal(error.cause, undefined)
      assert.ok(!JSON.stringify(error).includes('private-secret'))
      return true
    })
  }
})

test('preaborted requests and disposed peers perform zero settings writes', { timeout: 20000 }, async t => {
  const { root, control } = await runtime(t)
  let calls = 0
  await install(root, control, { setEnabled() { calls++; return status() }, status })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  await assert.rejects(invoke(root, peer, 'setEnabled', { enabled: true }, AbortSignal.abort()), error => error.code === 'gateway/cancelled')
  await peer.dispose()
  await assert.rejects(invoke(root, peer), error => error.code === 'gateway/peer-disposed')
  assert.equal(calls, 0)
})

test('explicit dispose and parent unload withdraw global settings descriptors', { timeout: 20000 }, async t => {
  for (const mode of ['disposer', 'parent']) {
    const { root, control } = await runtime(t)
    const mounted = await install(root, control, { status, setEnabled: status })
    const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
    if (mode === 'disposer') { await mounted.dispose(); await mounted.dispose() } else await mounted.fiber.dispose()
    await assert.rejects(invoke(root, peer), error => error.code === 'gateway/definition-unavailable')
  }
})

test('request peer and unload cancellation abort in-flight writes and suppress late results', { timeout: 20000 }, async t => {
  for (const mode of ['request', 'peer', 'disposer', 'parent']) {
    const { root, control } = await runtime(t)
    const started = Promise.withResolvers()
    const held = Promise.withResolvers()
    t.after(() => held.resolve(status()))
    let callbackSignal
    const mounted = await install(root, control, { status, setEnabled(enabled, signal) { callbackSignal = signal; started.resolve(); return held.promise } })
    const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
    const request = new AbortController()
    const pending = invoke(root, peer, 'setEnabled', { enabled: true }, request.signal).then(value => ({ value }), error => ({ error }))
    await within(started.promise, 'write callback not reached')
    if (mode === 'request') request.abort()
    else if (mode === 'peer') await peer.dispose()
    else if (mode === 'disposer') await mounted.dispose()
    else await mounted.fiber.dispose()
    assert.equal(callbackSignal.aborted, true)
    const result = await within(pending, 'cancelled callback did not settle')
    assert.equal(result.error?.code, 'gateway/cancelled')
    held.resolve({ ...status(), secret: 'late-private' })
    assert.equal((await pending).value, undefined)
  }
})

test('real Client mount calls the global Host contract through the actual Gateway', { timeout: 20000 }, async t => {
  const { root, control, Context, Service, load } = await runtime(t)
  const client = new Context()
  t.after(() => client.fiber.dispose())
  await install(root, control, { status, setEnabled: status })
  const peer = root.connection.createPeer({ principal: { kind: 'owner' } })
  const transmissions = []
  class FixtureTransport extends Service {
    constructor(ctx) {
      super(ctx, 'connection')
      this.interactions = 'none'
      this.rpc = {
        open() { throw Error('no streams') },
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
  assert.equal(client.typert.remotes.list().length, 2)
  assert.deepEqual(await client.remote.lantaiCoreMemory.status(), { ok: true, value: status() })
  assert.deepEqual(await client.remote.lantaiCoreMemory.setEnabled(true), { ok: true, value: status() })
  assert.deepEqual({ ...transmissions[0].payload.args }, {})
  assert.deepEqual({ ...transmissions[1].payload.args }, { enabled: true })
  const invalid = await client.remote.lantaiCoreMemory.setEnabled('true')
  assert.equal(invalid.ok, false)
  assert.equal(invalid.error.code, 'gateway/input-invalid')
  await unmount()
  assert.equal(client.typert.remotes.list().length, 0)
})
