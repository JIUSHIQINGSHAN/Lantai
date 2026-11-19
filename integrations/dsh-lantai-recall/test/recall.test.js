import assert from 'node:assert/strict'
import fs from 'node:fs'
import fsPromises from 'node:fs/promises'
import { syncBuiltinESMExports } from 'node:module'
import { test } from 'node:test'
import { apply } from '../index.js'

const disabledMessage = 'Lantai automatic recall is unavailable: the host does not provide trusted final-admission, input-source, and workspace data boundaries.'

async function withoutSideEffects(t, exercise) {
  const calls = []
  const reject = name => () => {
    calls.push(name)
    throw new Error(`Unexpected access: ${name}`)
  }
  const ctx = new Proxy({}, {
    get: (_, name) => reject(`context.${String(name)}`)(),
  })
  const env = process.env
  try {
    process.env = new Proxy(env, {
      get: (target, key) => typeof key === 'string' && key.startsWith('LANTAI_RECALL_')
        ? reject('credential environment')()
        : Reflect.get(target, key),
    })
    t.mock.method(globalThis, 'fetch', reject('network'))
    for (const method of ['realpath', 'stat', 'lstat', 'open', 'readFile', 'readdir']) {
      t.mock.method(fsPromises, method, reject(`fs/promises.${method}`))
      t.mock.method(fs, method, reject(`fs.${method}`))
      t.mock.method(fs, `${method}Sync`, reject(`fs.${method}Sync`))
    }
    syncBuiltinESMExports()
    await exercise(ctx, reject)
  } finally {
    t.mock.restoreAll()
    syncBuiltinESMExports()
    process.env = env
    assert.deepEqual(calls, [])
  }
}

function configWithTripwires(enabled, reject) {
  return new Proxy({ enabled }, {
    get: (target, key) => key === 'enabled' ? target.enabled : reject(`config.${String(key)}`)(),
  })
}

test('omitted, empty, or non-true configuration is a no-op', async t => {
  await withoutSideEffects(t, async (ctx, reject) => {
    assert.equal(await apply(ctx), undefined)
    assert.equal(await apply(ctx, {}), undefined)
    for (const enabled of [false, 'true', 1]) {
      assert.equal(await apply(ctx, configWithTripwires(enabled, reject)), undefined)
    }
  })
})

test('enabled: true rejects with the fixed diagnostic before any side effects', async t => {
  await withoutSideEffects(t, async (ctx, reject) => {
    await assert.rejects(async () => apply(ctx, configWithTripwires(true, reject)), {
      name: 'Error',
      message: disabledMessage,
    })
    await assert.rejects(async () => apply(ctx, {
      enabled: true,
      baseUrl: 'https://example.invalid',
      workspaces: [process.cwd()],
    }), { name: 'Error', message: disabledMessage })
  })
})
