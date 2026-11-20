import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const directory = path.join(root, 'integrations', 'dsh-core-memory')
const marker = '/*__DSH_CORE_MEMORY_INLINE_SOURCES__*/'
const normalizeLineEndings = text => text.replace(/\r\n/gu, '\n')

export function renderClient(template, contract, dictionaries, state) {
  const positions = template.split(marker)
  if (positions.length !== 2) throw new Error('Client template must contain exactly one inline-source marker')
  const inlineSources = [
    'const contractModule = { exports: {} }',
    ';(function(module, exports) {',
    contract,
    '})(contractModule, contractModule.exports)',
    'const { createControlContribution } = contractModule.exports',
    'const stateModule = { exports: {} }',
    ';(function(module, exports) {',
    state,
    '})(stateModule, stateModule.exports)',
    'const { settleControlResult } = stateModule.exports',
    `const clientDictionaries = ${JSON.stringify(dictionaries, null, 2)}`,
  ].join('\n')
  return normalizeLineEndings(`${positions[0]}${inlineSources}${positions[1]}`)
}

async function build(check = false) {
  const [template, contract, enText, zhText, state] = await Promise.all([
    readFile(path.join(directory, 'client.template.js'), 'utf8'),
    readFile(path.join(directory, 'lib', 'control-contract.cjs'), 'utf8'),
    readFile(path.join(directory, 'locale', 'en.json'), 'utf8'),
    readFile(path.join(directory, 'locale', 'zh.json'), 'utf8'),
    readFile(path.join(directory, 'lib', 'client-state.cjs'), 'utf8'),
  ])
  const dictionaries = { zh: JSON.parse(zhText).client, en: JSON.parse(enText).client }
  const generated = renderClient(template, contract, dictionaries, state)
  const target = path.join(directory, 'client.js')
  if (check) {
    const current = await readFile(target, 'utf8')
    if (normalizeLineEndings(current) !== generated) throw new Error('Client artifact is stale; run the explicit client build before testing')
  } else await writeFile(target, generated, 'utf8')
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await build(process.argv.includes('--check'))
}
