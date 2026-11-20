import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, stat, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = name => readFile(path.join(root, name), 'utf8')

test('manifest exports runtime, locales and original icon with a complete distribution list', async () => {
  const manifest = JSON.parse(await read('package.json'))
  assert.equal(manifest.name, '@lantai/dsh-core-memory')
  assert.equal(manifest.type, 'module')
  assert.equal(manifest.engines.dsh, '0.2.2-rc.5')
  assert.equal(manifest.peerDependencies['@deepseek-ai/schemastery'], '~3.19.0')
  for (const peer of ['@deepseek-ai/dsh-tools', '@deepseek-ai/dsh-typert-protocol', '@deepseek-ai/dsh-storage-domain', '@deepseek-ai/dsh-client-ui-settings-general', '@deepseek-ai/dsh-api-gateway', '@deepseek-ai/dsh-api-remote-access', '@deepseek-ai/dsh-client-locale']) assert.equal(manifest.peerDependencies[peer], '0.2.2-rc.5', peer)
  assert.equal(manifest.peerDependencies.zod, '^4.6.6')
  assert.equal(manifest.exports['.'], './index.js')
  assert.equal(manifest.exports['./locale/*.json'], './locale/*.json')
  assert.equal(manifest.exports['./icon'], './icon.svg')
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml')
  assert.equal(manifest.scripts?.preinstall, undefined)
  assert.equal(manifest.scripts?.install, undefined)
  assert.equal(manifest.scripts?.postinstall, undefined)
  for (const entry of manifest.files) assert((await stat(path.join(root, entry))).isFile() || (await stat(path.join(root, entry))).isDirectory())
  for (const required of ['index.js', 'lib', 'cordis.patch.yml', 'locale', 'icon.svg', 'README.md']) assert(manifest.files.includes(required))
  for (const module of ['index.js', ...(await readdir(path.join(root, 'lib'))).map(name => `lib/${name}`)]) {
    const text = await read(module)
    for (const match of text.matchAll(/from\s+['"](\.[^'"]+)['"]/gu)) {
      assert((await stat(path.resolve(root, path.dirname(module), match[1]))).isFile())
    }
  }
})

test('bundle is disabled by default and has one uniquely named entry', async () => {
  const patch = await read('cordis.patch.yml')
  assert.equal((patch.match(/\bid:/gu) ?? []).length, 1)
  assert.match(patch, /id: lantai-core-memory/u)
  assert.match(patch, /name: ['"]@lantai\/dsh-core-memory['"]/u)
  assert.match(patch, /enabled: false/u)
  assert(!/token:|apiKey:/iu.test(patch))
})

test('both locales explain activation and history retention', async () => {
  for (const locale of ['en', 'zh']) {
    const value = JSON.parse(await read(`locale/${locale}.json`))
    assert(value.meta.title.length > 3)
    assert(value.meta.description.length > 80)
    assert.match(value.meta.description, /policy/u)
    assert.match(value.meta.description, /identity/u)
    assert.match(value.meta.description, /task/u)
  }
})

test('icon is small self-contained SVG geometry with fixed optical viewport', async () => {
  const svg = await read('icon.svg')
  assert.match(svg, /<svg[^>]+width="36"[^>]+height="36"[^>]+viewBox="0 0 36 36"/u)
  assert.match(svg, /<\/svg>\s*$/u)
  assert(Buffer.byteLength(svg) < 262144)
  assert(!/<(?:script|style|image|foreignObject|text|animate)\b|\bon\w+=|https?:\/\/(?!www\.w3\.org)/iu.test(svg))
})
