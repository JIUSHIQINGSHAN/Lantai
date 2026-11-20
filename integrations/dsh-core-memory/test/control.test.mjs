import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test } from 'node:test'
const { createControlContribution } = createRequire(import.meta.url)('../lib/control-contract.cjs')
const metadata = () => ({ enabled: false, writable: true, reason: 'ready', folderCount: 2, includesTask: false })

test('shared control contract has only global status and boolean setEnabled', () => {
  const contribution = createControlContribution()
  assert.equal(contribution.package, '@lantai/dsh-core-memory')
  assert.deepEqual(contribution.descriptors.map(d => d.method), ['status', 'setEnabled'])
  assert.deepEqual(contribution.descriptors[0].parameters, [])
  for (const descriptor of contribution.descriptors) {
    assert.equal(descriptor.service, 'lantaiCoreMemory')
    assert.equal(descriptor.namespace, 'lantaiCoreMemory')
    assert.deepEqual(descriptor.invocation, { kind: 'direct' })
    assert.deepEqual(descriptor.cancellation, { parameter: 'signal' })
    assert.deepEqual(descriptor.result.create().parse(metadata()), metadata())
  }
  const [parameter] = contribution.descriptors[1].parameters
  assert.equal(parameter.name, 'enabled')
  assert.equal(parameter.wire, 'enabled')
  assert.equal(parameter.source, 'json')
  for (const value of [true, false]) assert.equal(parameter.codec.create().parse(value), value)
  for (const value of [0, 1, 'true', null, undefined, {}, []]) assert.throws(() => parameter.codec.create().parse(value))
})

test('metadata parser rejects extra, missing, accessor and sensitive fields', () => {
  const parse = createControlContribution().descriptors[0].result.create().parse
  const value = metadata()
  assert.notEqual(parse(value), value)
  for (const reason of ['ready', 'scope_changed', 'unavailable']) assert.equal(parse({ ...value, reason }).reason, reason)
  const invalid = [null, [], '', { ...value, enabled: 'true' }, { ...value, writable: 1 }, { ...value, reason: 'secret' },
    { ...value, folderCount: -1 }, { ...value, folderCount: 0.5 }, { ...value, folderCount: Infinity }, { ...value, includesTask: null },
    { ...value, token: 'secret' }, { ...value, sessionId: 'session' }, { ...value, cwd: 'C:/private' },
    Object.assign(Object.create({ secret: true }), value), Object.assign({}, value, { [Symbol('secret')]: true }),
    Object.defineProperty({ ...value }, 'secret', { value: true }), { ...value, toJSON() { return { token: 'secret' } } }]
  for (const field of Object.keys(value)) { const missing = { ...value }; delete missing[field]; invalid.push(missing) }
  let touched = false
  invalid.push(Object.defineProperty({ ...value }, 'enabled', { get() { touched = true; return true }, enumerable: true }))
  for (const input of invalid) assert.throws(() => parse(input), { message: 'Invalid preference status' })
  assert.equal(touched, false)
})
