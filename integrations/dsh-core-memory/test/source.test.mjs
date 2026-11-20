import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { readAuthorizedCredentialSource } from '../lib/source.js'

function proof(kind = 'owner') {
  return { peer: { principal: { kind }, signal: new AbortController().signal }, signal: new AbortController().signal }
}
async function source(t, value) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'lantai-credential-source-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const file = path.join(dir, 'connection.conf')
  await writeFile(file, typeof value === 'string' ? value : JSON.stringify(value))
  return file
}
const options = file => ({ file, expectedHost: 'memory.example', remotePort: 18767, invocation: proof(), authorize: () => true })
const code = expected => error => error.code === expected && error.message === expected

test('credential source is unreadable without owner authority and an exact grant', async () => {
  for (const override of [{ invocation: undefined }, { invocation: proof('viewer') }, { authorize: () => false }, { authorize: () => { throw Error('unknown policy') } }]) {
    let reads = 0
    await assert.rejects(readAuthorizedCredentialSource({ ...options('not-even-a-file'), ...override }, { open: async () => { reads++; throw Error('filesystem must not be touched') } }), code('authorization_required'))
    assert.equal(reads, 0)
  }
})

test('approved local source returns only the intended key for the pinned server', async t => {
  const file = await source(t, { host: 'memory.example', port: 18767, api_key: 'fixture-api-key' })
  assert.equal(await readAuthorizedCredentialSource(options(file)), 'fixture-api-key')
})

for (const value of [
  { host: 'other.example', port: 18767, api_key: 'fixture-api-key' },
  { host: 'memory.example', port: 18768, api_key: 'fixture-api-key' },
  { host: 'memory.example', port: 18767, api_key: 'bad\nkey' },
  { host: 'memory.example', port: 18767 },
  '{bad-json',
]) {
  test('invalid source never leaks content or changes the pinned connection', async t => {
    const file = await source(t, value)
    await assert.rejects(readAuthorizedCredentialSource(options(file)), code('credential_source_invalid'))
  })
}

test('source reads have a hard byte budget', async t => {
  const file = await source(t, ' '.repeat(16385))
  await assert.rejects(readAuthorizedCredentialSource(options(file)), code('credential_source_invalid'))
})

test('an already cancelled request cannot open the source', async () => {
  const cancel = new AbortController()
  cancel.abort()
  let opened = false
  await assert.rejects(readAuthorizedCredentialSource({ ...options('missing'), signal: cancel.signal }, { open: async () => { opened = true } }), code('aborted'))
  assert.equal(opened, false)
})
