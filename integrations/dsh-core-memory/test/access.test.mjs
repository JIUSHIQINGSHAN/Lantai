import test from 'node:test'
import assert from 'node:assert/strict'
import { requireOwnerInvocation } from '../lib/access.js'

const actor = kind => ({ peer: { principal: { kind }, signal: new AbortController().signal }, signal: new AbortController().signal })
const denied = error => error.code === 'authorization_required' && error.message === 'authorization_required'

test('only a live Host owner invocation passes the authority boundary', () => {
  const invocation = actor('owner')
  assert.equal(requireOwnerInvocation(invocation), invocation)
  for (const value of [undefined, null, {}, { owner: true }, { source: { kind: 'user' } }, actor('viewer'), { peer: { principal: { kind: 'owner' } } }]) {
    assert.throws(() => requireOwnerInvocation(value), denied)
  }
})

test('malformed invocation data is a fixed denial without diagnostic leakage', () => {
  for (const value of [new Proxy({}, { get() { throw Error('private-source') } }), { peer: { get principal() { throw Error('private-source') } } }]) {
    assert.throws(() => requireOwnerInvocation(value), denied)
  }
})
