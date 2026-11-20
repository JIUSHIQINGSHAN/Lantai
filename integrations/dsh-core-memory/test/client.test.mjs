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
const contractPath = path.join(directory, 'lib', 'control-contract.cjs')
const buildScript = path.join(root, 'scripts', 'build_dsh_core_memory_client.mjs')

test('generated Client stays synchronized with the shared contract and both locale dictionaries', async () => {
  const [template, contract, enText, zhText, state] = await Promise.all([
    read('client.template.js'), readFile(contractPath, 'utf8'),
    read('locale/en.json'), read('locale/zh.json'), read('lib/client-state.cjs'),
  ])
  const dictionaries = { zh: JSON.parse(zhText).client, en: JSON.parse(enText).client }
  const generated = await readFile(generatedPath, 'utf8')
  assert.equal(generated, renderClient(template, contract, dictionaries, state))

  execFileSync(process.execPath, [buildScript, '--check'], { cwd: root, stdio: 'pipe' })
  assert.equal(await readFile(generatedPath, 'utf8'), generated)
})

test('lazy Client mounts the shared owner-control descriptors and only installs for owner access', async () => {
  const generated = await readFile(generatedPath, 'utf8')
  const contract = await readFile(contractPath, 'utf8')
  assert.match(generated, /window\.__ModuleLoader__\.load\(/u)
  assert.match(generated, /id: '@lantai\/dsh-core-memory'/u)
  assert.match(generated, /require\('react'\)/u)
  assert.doesNotMatch(generated, /@deepseek-ai\/dsh-client-ui-primitives/u)
  assert.match(generated, /await ctx\.remote\.\$mount\(createControlContribution\(\)\)/u)
  assert.match(generated, /whenAccess\(authorizeAction/u)
  assert.match(generated, /authorizeAction = 'lantaiCoreMemory\/authorize'/u)
  assert.match(generated, /'conversation\.input\.right'/u)
  assert.match(generated, /sessionId: props\.sessionId/u)
  assert.match(contract, /service: 'lantaiCoreMemory', namespace: 'lantaiCoreMemory'/u)
  assert.match(contract, /\['authorize', 'revoke', 'status'\]/u)
})

test('Client authority wiring remains click-only and session cleanup cancels the latest request', async () => {
  const generated = await readFile(generatedPath, 'utf8')
  assert.match(generated, /onClick: \(\) => \{ void run\('authorize'\) \}/u)
  assert.match(generated, /onClick: \(\) => \{ void run\('revoke'\) \}/u)
  const initialStatusEffect = generated.match(/React\.useEffect\(\(\) => \{([\s\S]*?)\}, \[sessionId, remote\]\)/u)?.[1]
  assert(initialStatusEffect, 'session lifecycle effect must exist')
  assert.match(initialStatusEffect, /remote\.status\(sessionId, controller\.signal\)/u)
  assert.doesNotMatch(initialStatusEffect, /run\('authorize'\)|remote\.authorize/u)
  assert.match(initialStatusEffect, /pending\?\.abort\(\)/u)
  assert.match(initialStatusEffect, /mounted\.current = false/u)
  assert.match(generated, /validId\(props\.sessionId\) \? h\(ConsentControl/u)
  assert.match(generated, /remote\[method\]\(sessionId, controller\.signal\)/u)
  assert.match(generated, /request\.current !== controller/u)
  assert.match(generated, /'alert'/u)
  assert.doesNotMatch(generated, /defineTool|\.tools\.register|\.registerTool/u)
})

test('lost stop replies preserve a retryable stop state instead of claiming authority is gone', () => {
  const known = { sessionId: 'session-a', snapshot: { authorized: true }, uncertain: false }
  const failed = clientState.settleControlResult(known, 'session-a', 'revoke', { ok: false })
  assert.equal(failed.snapshot.authorized, true)
  assert.equal(failed.uncertain, true)
  assert.equal(failed.error, 'stopUnconfirmed')
  const lostApproval = clientState.settleControlResult({ sessionId: 'session-a', snapshot: null }, 'session-a', 'authorize', { ok: false })
  assert.equal(lostApproval.uncertain, true)
  const confirmed = clientState.settleControlResult(failed, 'session-a', 'revoke', { ok: true, value: { authorized: false } })
  assert.equal(confirmed.snapshot.authorized, false)
  assert.equal(confirmed.uncertain, false)
  const other = clientState.settleControlResult(known, 'session-b', 'status', { ok: false })
  assert.equal(other.snapshot, null)
  assert.equal(other.uncertain, false)
})

test('Client connects retryable uncertainty to the visible stop action', async () => {
  const generated = await readFile(generatedPath, 'utf8')
  assert.match(generated, /settleControlResult\(previous, sessionId, method, result\)/u)
  assert.match(generated, /snapshot\?\.authorized \|\| view\.uncertain/u)
})

test('both shipped Client locales have the same nonempty message keys', async () => {
  const en = JSON.parse(await read('locale/en.json')).client
  const zh = JSON.parse(await read('locale/zh.json')).client
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
  for (const [key, value] of Object.entries(en)) {
    assert.equal(typeof value, 'string', `en.${key}`)
    assert(value.length > 0, `en.${key}`)
    assert.equal(typeof zh[key], 'string', `zh.${key}`)
    assert(zh[key].length > 0, `zh.${key}`)
  }
})
