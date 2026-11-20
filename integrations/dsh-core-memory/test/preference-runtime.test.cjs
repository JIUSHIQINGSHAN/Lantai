'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const path = require('node:path')
const os = require('node:os')
const { mkdtemp, rm, rmdir, readdir, readFile, rename, mkdir } = require('node:fs/promises')

const directory = path.resolve(__dirname, '..')
const scopeA = 'a'.repeat(64)
const scopeB = 'b'.repeat(64)
async function fixture(t) {
  assert.ok(process.versions.electron, 'use the installed Electron runtime')
  const installed = createRequire(path.join(process.env.DSH_INSTALL_DIR, 'package.json'))
  const load = name => import(pathToFileURL(installed.resolve(`@deepseek-ai/${name}`)).href)
  const { Context } = await load('cordis')
  const root = new Context()
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'lantai-preference-'))
  const storage = path.join(temporary, 'storage')
  t.after(async () => {
    await root.fiber.dispose()
    assert.equal(path.dirname(temporary), os.tmpdir())
    assert.ok(path.basename(temporary).startsWith('lantai-preference-'))
    await rm(temporary, { recursive: true, force: true })
  })
  const { PluginPackages, createRuntimeResolution } = await load('dsh-app-boot')
  const resolution = await createRuntimeResolution({ installAnchor: path.join(process.env.DSH_INSTALL_DIR, 'package.json'), home: path.join(temporary, 'synthetic-home') })
  await root.plugin(PluginPackages, { resolution: {
    profilesDir: resolution.profilesDir, profileDir: path.join(temporary, 'profile'), entries: resolution.entries,
    localPackageNames: ['@lantai/dsh-core-memory'], linkedRoots: [{ name: '@lantai/dsh-core-memory', realPath: directory }],
  } })
  for (const [name, config] of [['dsh-storage', {}], ['dsh-storage-json', { root: storage }], ['dsh-storage-domain', { backend: 'json' }]]) {
    const plugin = await load(name)
    await root.plugin(plugin.default ?? plugin, config)
  }
  const { createProfilePreference } = await import(pathToFileURL(path.join(directory, 'lib/preference.js')).href)
  const choices = new Set()
  function create(extra = {}) {
    const preference = createProfilePreference({ storageDomain: root.storageDomain, profileDirectory: path.join(temporary, 'profile-a'), scopeKey: scopeA, ...extra })
    choices.add(preference)
    return preference
  }
  t.after(async () => { for (const choice of choices) await choice.dispose() })
  return { root, create, storage, temporary }
}

test('the real DSH storage defaults off and persists an explicit choice across reopen', async t => {
  const f = await fixture(t)
  const first = f.create()
  await first.ready
  assert.deepEqual(first.status(), { enabled: false, writable: true, reason: 'ready' })
  assert.equal(first.enabled(), false)
  assert.equal((await first.setEnabled(true)).enabled, true)
  await first.dispose()
  const second = f.create()
  await second.ready
  assert.equal(second.enabled(), true)
  assert.equal((await second.setEnabled(false)).enabled, false)
  await second.dispose()
  const third = f.create()
  await third.ready
  assert.equal(third.enabled(), false)
  const files = await readdir(f.storage)
  assert.equal(files.length, 1)
  const stored = await readFile(path.join(f.storage, files[0]), 'utf8')
  assert.ok(!stored.includes(f.temporary), 'stored preference contains no profile path')
  assert.ok(!stored.includes('credential'))
})

test('profiles are separate domains and changed scopes require a fresh explicit choice', async t => {
  const f = await fixture(t)
  const a = f.create()
  await a.ready
  await a.setEnabled(true)
  const b = f.create({ profileDirectory: path.join(f.temporary, 'profile-b') })
  await b.ready
  assert.equal(b.enabled(), false)
  assert.equal(a.enabled(), true)
  await a.dispose()
  const changed = f.create({ scopeKey: scopeB })
  await changed.ready
  assert.deepEqual(changed.status(), { enabled: false, writable: true, reason: 'scope_changed' })
  assert.equal((await changed.setEnabled(true)).reason, 'ready')
  await changed.dispose()
  const original = f.create()
  await original.ready
  assert.equal(original.enabled(), false)
  assert.equal(original.status().reason, 'scope_changed')
})

test('invalid configuration and cancelled choices make zero durable writes', async t => {
  const f = await fixture(t)
  const p = f.create()
  await p.ready
  let changes = 0
  f.root.on('domain/changed', () => { changes++ })
  await assert.rejects(p.setEnabled(true, AbortSignal.abort()), error => error.code === 'aborted')
  await assert.rejects(p.setEnabled('true'), error => error.code === 'invalid_config')
  assert.equal(changes, 0)
  const invalid = f.create({ profileDirectory: 'relative-path' })
  await invalid.ready
  assert.deepEqual(invalid.status(), { enabled: false, writable: false, reason: 'unavailable' })
  await assert.rejects(invalid.setEnabled(true), error => error.code === 'preference_unavailable')
})

test('an aborted queued choice never writes after an earlier operation settles', async t => {
  const f = await fixture(t)
  const p = f.create()
  await p.ready
  const changes = []
  f.root.on('domain/changed', event => { changes.push(event.value.enabled) })
  const first = p.setEnabled(false)
  const cancel = new AbortController()
  const queued = p.setEnabled(true, cancel.signal)
  cancel.abort()
  await first
  await assert.rejects(queued, error => error.code === 'aborted')
  assert.equal(p.enabled(), false)
  assert.deepEqual(changes, [false])
})

test('cancellation after a real commit compensates off before restart', async t => {
  const f = await fixture(t)
  const events = []
  const p = f.create({ onChange: value => events.push(value) })
  await p.ready
  const cancel = new AbortController()
  const stop = f.root.on('domain/changed', event => { if (event.value?.enabled === true) cancel.abort() })
  await assert.rejects(p.setEnabled(true, cancel.signal), error => error.code === 'aborted')
  stop()
  assert.equal(p.enabled(), false)
  assert.ok(!events.includes(true), 'cancelled enable was never published')
  await p.dispose()
  const reopened = f.create()
  await reopened.ready
  assert.equal(reopened.enabled(), false)
})

test('disable stops immediately and a failed real disk write stays off with retry', async t => {
  const f = await fixture(t)
  const events = []
  const p = f.create({ onChange: value => events.push(value) })
  await p.ready
  await p.setEnabled(true)
  const [filename] = await readdir(f.storage)
  const target = path.join(f.storage, filename)
  const backup = path.join(f.storage, `${filename}.owned-backup`)
  await rename(target, backup)
  await mkdir(target)
  const pending = p.setEnabled(false)
  assert.equal(p.enabled(), false)
  assert.equal(events.at(-1), false)
  await assert.rejects(pending, error => error.code === 'preference_unavailable')
  assert.equal(p.status().reason, 'unavailable')
  assert.equal(p.enabled(), false)
  await rmdir(target)
  await rename(backup, target)
  assert.equal((await p.setEnabled(false)).enabled, false)
  assert.equal(p.status().reason, 'ready')
})

test('a committed enable superseded by a stop rejects and never publishes on', async t => {
  const f = await fixture(t)
  const notifications = []
  const p = f.create({ onChange: value => notifications.push(value) })
  await p.ready
  let stopping
  const stopListener = f.root.on('domain/changed', event => {
    if (event.value?.enabled === true) stopping = p.setEnabled(false)
  })
  await assert.rejects(p.setEnabled(true), error => error.code === 'preference_superseded')
  await stopping
  stopListener()
  assert.equal(p.enabled(), false)
  assert.ok(!notifications.includes(true))
  await p.dispose()
  const reopened = f.create()
  await reopened.ready
  assert.equal(reopened.enabled(), false)
})

test('an accepted stop persists even when a later queued enable is cancelled', async t => {
  const f = await fixture(t)
  const p = f.create()
  await p.ready
  await p.setEnabled(true)
  const stop = p.setEnabled(false)
  const cancel = new AbortController()
  const enable = p.setEnabled(true, cancel.signal)
  cancel.abort()
  await stop
  await assert.rejects(enable, error => error.code === 'aborted')
  assert.equal(p.enabled(), false)
  await p.dispose()
  const reopened = f.create()
  await reopened.ready
  assert.equal(reopened.enabled(), false)
})

test('an accepted stop remains durable after its caller disconnects', async t => {
  const f = await fixture(t)
  const p = f.create()
  await p.ready
  await p.setEnabled(true)
  const cancel = new AbortController()
  const stop = p.setEnabled(false, cancel.signal)
  cancel.abort()
  await stop
  await p.dispose()
  const reopened = f.create()
  await reopened.ready
  assert.equal(reopened.enabled(), false)
})

test('a stop requested during startup never republishes the saved enabled choice', async t => {
  const f = await fixture(t)
  const first = f.create()
  await first.ready
  await first.setEnabled(true)
  await first.dispose()
  const events = []
  const second = f.create({ onChange: value => events.push(value) })
  await second.setEnabled(false)
  assert.equal(second.enabled(), false)
  assert.ok(!events.includes(true))
})

test('dispose is idempotent, drains a committed enable and preserves normal saved state', async t => {
  const f = await fixture(t)
  const p = f.create()
  await p.ready
  const stop = f.root.on('domain/changed', event => { if (event.value?.enabled === true) void p.dispose() })
  await assert.rejects(p.setEnabled(true), error => error.code === 'disposed')
  await p.dispose()
  stop()
  assert.equal(p.status().writable, false)
  const reopened = f.create()
  await reopened.ready
  assert.equal(reopened.enabled(), false)
  await reopened.setEnabled(true)
  await reopened.dispose()
  await reopened.dispose()
  const final = f.create()
  await final.ready
  assert.equal(final.enabled(), true)
})
