import test from 'node:test'
import assert from 'node:assert/strict'
import { requireOwnerInvocation } from '../lib/access.js'

for (const who of ['request', 'peer']) {
  test(`a cancelled ${who} cannot change the global memory choice`, () => {
    const request = new AbortController()
    const peer = new AbortController()
    const invocation = { peer: { principal: { kind: 'owner' }, signal: peer.signal }, signal: request.signal }
    assert.equal(requireOwnerInvocation(invocation), invocation)
    ;({ request, peer })[who].abort()
    assert.throws(() => requireOwnerInvocation(invocation), error => error.code === 'authorization_required')
  })
}
