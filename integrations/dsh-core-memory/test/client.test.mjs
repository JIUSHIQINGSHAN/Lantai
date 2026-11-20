import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { renderClient } from '../../../scripts/build_dsh_core_memory_client.mjs'
import clientState from '../lib/client-state.cjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const directory = path.join(root, 'integrations', 'dsh-core-memory')
const read = file => readFile(path.join(directory, file), 'utf8')
const generatedPath = path.join(directory, 'client.js')
const buildScript = path.join(root, 'scripts', 'build_dsh_core_memory_client.mjs')
const good = (enabled, extra = {}) => ({ ok: true, value: { enabled, writable: true, reason: 'ready', folderCount: 2, includesTask: false, ...extra } })
const lost = Object.freeze({ ok: false })
const { settleControlResult } = clientState

test('Client rendering is identical with Windows and Unix checkout line endings', () => {
  const template = 'before\n/*__DSH_CORE_MEMORY_INLINE_SOURCES__*/\nafter\n'
  const contract = 'exports.createControlContribution = () => {}\n'
  const state = 'exports.settleControlResult = () => {}\n'
  const dictionaries = { zh: {}, en: {} }
  const toWindows = text => text.replace(/\n/gu, '\r\n')
  const expected = renderClient(template, contract, dictionaries, state)
  assert.equal(renderClient(toWindows(template), toWindows(contract), dictionaries, toWindows(state)), expected)
  assert.doesNotMatch(expected, /\r/u)
})

test('generated Client matches the shared contract and both locale dictionaries', async () => {
  const [template, contract, en, zh, state] = await Promise.all([
    read('client.template.js'), read('lib/control-contract.cjs'), read('locale/en.json'), read('locale/zh.json'), read('lib/client-state.cjs'),
  ])
  const dictionaries = { zh: JSON.parse(zh).client, en: JSON.parse(en).client }
  const generated = await readFile(generatedPath, 'utf8')
  assert.equal(generated.replace(/\r\n/gu, '\n'), renderClient(template, contract, dictionaries, state))
  execFileSync(process.execPath, [buildScript, '--check'], { cwd: root, stdio: 'pipe' })
  assert.equal(await readFile(generatedPath, 'utf8'), generated)
})

test('global switch belongs to General Settings and changes only on an explicit action', async () => {
  const source = await readFile(generatedPath, 'utf8')
  const artifact = await read('package.json')
  assert.match(source, /await ctx\.remote\.\$mount\(createControlContribution\(\)\)/u)
  assert.match(source, /whenAccess\(writeAction/u)
  assert.match(source, /writeAction = 'lantaiCoreMemory\/setEnabled'/u)
  assert.match(source, /'settings\.general\.item'/u)
  assert.match(source, /type: 'button', role: 'switch'/u)
  assert.match(source, /'aria-checked': view\.snapshot\?\.enabled === true/u)
  assert.match(source, /'aria-labelledby': titleId, 'aria-describedby': detailsId/u)
  assert.match(source, /role: 'status', 'aria-atomic': 'true' \}, feedback && t\(feedback\)/u)
  assert.match(source, /:focus-visible/u)
  assert.match(source, /onClick: \(\) => \{ if \(!blocked\) void run\(!view\.snapshot\?\.enabled\) \}/u)
  assert.match(source, /onClick: \(\) => \{ if \(!busy\) void run\(view\.retry\) \}/u)
  assert.match(source, /remote\.setEnabled\(action, signal\)/u)
  assert.match(source, /remote\.status\(signal\)/u)
  assert.match(source, /request\.current\?\.abort\(\)/u)
  assert.doesNotMatch(source, /conversation\.input\.right|sessionId|onKeyDown|onKeyUp|defineTool|@deepseek-ai\/dsh-client-ui-primitives/u)
  assert.ok(JSON.parse(artifact).dsh.client.inject.includes('@deepseek-ai/dsh-client-ui-settings-general'))
})

test('only an acknowledged change can display saved, never a matching status reread', () => {
  const enabled = good(true)
  const disabled = good(false)
  assert.deepEqual(settleControlResult('status', enabled), { snapshot: enabled.value, feedback: null, retry: null })
  assert.deepEqual(settleControlResult(true, enabled), { snapshot: enabled.value, feedback: 'savedOn', retry: null })
  assert.deepEqual(settleControlResult(false, disabled), { snapshot: disabled.value, feedback: 'savedOff', retry: null })
  assert.deepEqual(settleControlResult(false, lost, disabled), { snapshot: disabled.value, feedback: 'stopUnconfirmed', retry: false })
  assert.deepEqual(settleControlResult(true, lost, enabled), { snapshot: enabled.value, feedback: 'enableUnconfirmed', retry: 'status' })
})

test('unavailable and scope-changed settings cannot be mistaken for a successful save', () => {
  const changed = good(false, { reason: 'scope_changed' })
  const unavailable = good(false, { reason: 'unavailable' })
  assert.deepEqual(settleControlResult('status', lost), { snapshot: null, feedback: 'readFailed', retry: 'status' })
  assert.deepEqual(settleControlResult('status', changed), { snapshot: changed.value, feedback: null, retry: null })
  assert.deepEqual(settleControlResult(false, unavailable), { snapshot: unavailable.value, feedback: 'stopUnconfirmed', retry: false })
  assert.deepEqual(settleControlResult(true, changed), { snapshot: changed.value, feedback: 'enableUnconfirmed', retry: 'status' })
})

test('both Client locales own all switch labels and feedback', async () => {
  const en = JSON.parse(await read('locale/en.json')).client
  const zh = JSON.parse(await read('locale/zh.json')).client
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
  for (const key of Object.keys(en)) {
    assert.equal(typeof en[key], 'string')
    assert.ok(en[key].trim())
    assert.equal(typeof zh[key], 'string')
    assert.ok(zh[key].trim())
  }
})
