import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test } from 'node:test'

const require = createRequire(import.meta.url)
const { createControlContribution } = require('../lib/control-contract.cjs')

const metadata = () => ({
  state: 'ready', reason: 'authorized', authorized: true, blockCount: 1,
  contextBytes: 23, versions: [{ block: 'policy', version: 1 }],
})

test('shared pure CommonJS contribution has the exact owner control wire interface', () => {
  const contribution = createControlContribution()
  assert.deepEqual(Object.keys(contribution).sort(), ['descriptors', 'package'])
  assert.equal(contribution.package, '@lantai/dsh-core-memory')
  assert.deepEqual(contribution.descriptors.map(value => value.method), ['authorize', 'revoke', 'status'])
  for (const descriptor of contribution.descriptors) {
    assert.equal(descriptor.id, `@lantai/dsh-core-memory#lantaiCoreMemory/${descriptor.method}`)
    assert.equal(descriptor.service, 'lantaiCoreMemory')
    assert.equal(descriptor.namespace, 'lantaiCoreMemory')
    assert.deepEqual(descriptor.invocation, { kind: 'direct' })
    assert.deepEqual(descriptor.cancellation, { parameter: 'signal' })
    assert.equal(descriptor.parameters.length, 1)
    const [parameter] = descriptor.parameters
    assert.equal(parameter.name, 'sessionId')
    assert.equal(parameter.wire, 'sessionId')
    assert.equal(parameter.source, 'json')
    assert.equal(parameter.codec.mode, 'strict')
    assert.equal(typeof parameter.codec.typeSymbol, 'string')
    assert.equal(parameter.codec.create().parse('session-1'), 'session-1')
    assert.equal(descriptor.result.mode, 'strict')
    assert.deepEqual(descriptor.result.create().parse(metadata()), metadata())
  }
})

test('strict sessionId parser refuses coercion, empty/oversize strings and control characters', () => {
  const parse = createControlContribution().descriptors[0].parameters[0].codec.create().parse
  assert.equal(parse('x'.repeat(1024)), 'x'.repeat(1024))
  for (const value of ['', 'x'.repeat(1025), 7, null, undefined, {}, ['s'], 's\n', 's\u0000', 's\u007f', 's\u0085']) {
    assert.throws(() => parse(value), { message: 'Invalid sessionId' })
  }
})

test('metadata parser accepts only specified scalar metadata and detaches returned values', () => {
  const parse = createControlContribution().descriptors[0].result.create().parse
  const input = { ...metadata(), expiresAt: 123456, outcome: 'loaded' }
  const result = parse(input)
  assert.deepEqual(result, input)
  assert.notEqual(result, input)
  assert.notEqual(result.versions, input.versions)
  assert.notEqual(result.versions[0], input.versions[0])
  for (const block of ['policy', 'identity', 'task']) {
    assert.deepEqual(parse({ ...metadata(), versions: [{ block, version: 3 }] }).versions, [{ block, version: 3 }])
  }
  const invalid = [
    null, [], 'secret', { ...metadata(), content: 'secret' }, { ...metadata(), key: 'secret' },
    { ...metadata(), state: 1 }, { ...metadata(), reason: null }, { ...metadata(), authorized: 'true' },
    { ...metadata(), blockCount: -1 }, { ...metadata(), blockCount: 0.1 }, { ...metadata(), contextBytes: NaN },
    { ...metadata(), contextBytes: Infinity }, { ...metadata(), expiresAt: 1.5 },
    { ...metadata(), expiresAt: Number.MAX_SAFE_INTEGER + 1 }, { ...metadata(), outcome: {} },
    { ...metadata(), versions: {} }, { ...metadata(), versions: [{ block: 'unknown', version: 1 }] },
    { ...metadata(), versions: [{ block: 'policy', version: 0 }] },
    { ...metadata(), versions: [{ block: 'policy', version: 1.2 }] },
    { ...metadata(), versions: [{ block: 'policy', version: 1, content: 'secret' }] },
    { ...metadata(), versions: [null] }, { ...metadata(), versions: Array(1) },
    Object.assign(Object.create({ content: 'inherited-secret' }), metadata()),
    { ...metadata(), toJSON: () => ({ key: 'secret' }) },
    Object.defineProperty(metadata(), 'key', { value: 'nonenumerable-secret' }),
    Object.assign(metadata(), { [Symbol('key')]: 'symbol-secret' }),
  ]
  for (const field of ['state', 'reason', 'authorized', 'blockCount', 'contextBytes', 'versions']) {
    const missing = metadata()
    delete missing[field]
    invalid.push(missing)
  }
  let getterReads = 0
  invalid.push(Object.defineProperty(metadata(), 'state', { get() { getterReads++; return 'secret' } }))
  for (const value of invalid) assert.throws(() => parse(value), { message: 'Invalid control metadata' })
  assert.equal(getterReads, 0)
})
