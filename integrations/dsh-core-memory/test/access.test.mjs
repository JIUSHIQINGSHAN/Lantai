import test from 'node:test'
import assert from 'node:assert/strict'
import { createSessionGrants } from '../lib/access.js'

const binding = () => ({ workspaceKey: 'workspace-a', namespace: 'fixture', userId: 'fixture-user', tenantId: null, includeTask: false })
const actor = id => ({ id, session: { header: {} } })
function invocation(kind = 'owner') {
  const peer = new AbortController()
  const request = new AbortController()
  return { value: { peer: { principal: { kind }, signal: peer.signal }, signal: request.signal }, peer, request }
}
function environment(options = {}) {
  const agent = actor('session-a')
  const live = new Map([[agent.id, agent]])
  let time = 1000
  const revoked = []
  const access = createSessionGrants({ getAgent: id => live.get(id), now: () => time, ttlMs: 100, onRevoke: (who, reason) => revoked.push({ who, reason }), ...options })
  return { agent, live, access, revoked, time: value => { time = value } }
}
const denied = result => result.authorized === false && result.reason === 'authorization_required'

test('a session starts without authority and with no grant metadata', () => {
  const e = environment()
  assert(denied(e.access.check(e.agent, binding(), 'source-a')))
})

for (const proof of [undefined, null, {}, { owner: true }, { source: { kind: 'user' } }, { peer: { principal: { kind: 'owner' } } }]) {
  test('missing or fabricated ordinary flags cannot issue authority', () => {
    let lookedUp = false
    const e = environment({ getAgent: () => { lookedUp = true; throw Error('must not reach agent lookup') } })
    assert.throws(() => e.access.grant(proof, e.agent, binding(), 'source-a'), error => error.code === 'authorization_required')
    assert.equal(lookedUp, false)
    assert(denied(e.access.check(e.agent, binding(), 'source-a')))
  })
}

test('non-owner Remote identity is rejected before scope or Agent lookup', () => {
  let touched = false
  const scope = new Proxy({}, { get() { touched = true; throw Error('scope must not be read') } })
  const e = environment({ getAgent: () => { touched = true } })
  assert.throws(() => e.access.grant(invocation('viewer').value, e.agent, scope, 'source-a'), error => error.code === 'authorization_required')
  assert.equal(touched, false)
})

test('an aborted Peer or request cannot issue a grant', () => {
  for (const field of ['peer', 'request']) {
    const e = environment()
    const proof = invocation()
    proof[field].abort()
    assert.throws(() => e.access.grant(proof.value, e.agent, binding(), 'source-a'), error => error.code === 'authorization_required')
  }
})

test('a trusted owner invocation binds one exact live root Agent and source scope', () => {
  const e = environment()
  const granted = e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  assert.deepEqual(granted, { authorized: true, expiresAt: 1100 })
  assert.deepEqual(e.access.check(e.agent, binding(), 'source-a'), granted)
  assert(denied(e.access.check(actor('other-session'), binding(), 'source-a')))
  assert(!JSON.stringify(granted).includes('fixture-user'))
})

test('a child Agent cannot receive its own core-memory grant', () => {
  const e = environment()
  e.agent.session.header.origin = 'subagent'
  assert.throws(() => e.access.grant(invocation().value, e.agent, binding(), 'source-a'), error => error.code === 'authorization_required')
})

for (const change of [{ workspaceKey: 'workspace-b' }, { namespace: 'another' }, { userId: 'another-user' }, { tenantId: 'another-tenant' }, { includeTask: true }]) {
  test('a change to any authorized data scope revokes rather than broadens authority', () => {
    const e = environment()
    const original = binding()
    e.access.grant(invocation().value, e.agent, original, 'source-a')
    assert.equal(e.access.check(e.agent, { ...original, ...change }, 'source-a').authorized, false)
    assert.equal(e.revoked.length, 1)
    assert.equal(e.access.check(e.agent, original, 'source-a').authorized, false)
  })
}

test('a connection identity change cannot reuse a previous grant', () => {
  const e = environment()
  e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  assert.equal(e.access.check(e.agent, binding(), 'source-b').authorized, false)
  assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
})

test('reusing a session id for a replacement Agent does not reuse its grant', () => {
  const e = environment()
  e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  const replacement = actor(e.agent.id)
  e.live.set(replacement.id, replacement)
  assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
  assert.equal(e.access.check(replacement, binding(), 'source-a').authorized, false)
  assert.equal(e.revoked.length, 1)
})

test('expiry and backwards or invalid clocks are fail-closed', () => {
  for (const time of [1100, 999, NaN]) {
    const e = environment()
    e.access.grant(invocation().value, e.agent, binding(), 'source-a')
    e.time(time)
    assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
    e.time(1001)
    assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
  }
})

test('revocation is idempotent and callback failure cannot preserve a grant', () => {
  const e = environment({ onRevoke: () => { throw Error('a consumer failed') } })
  e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  assert.doesNotThrow(() => e.access.revoke(e.agent, 'operator_revoked'))
  assert.doesNotThrow(() => e.access.revoke(e.agent, 'operator_revoked'))
  assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
})

test('renewal cancels the old authorization before publishing a fresh one', () => {
  const e = environment()
  e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  e.time(1020)
  assert.deepEqual(e.access.grant(invocation().value, e.agent, binding(), 'source-a'), { authorized: true, expiresAt: 1120 })
  assert.equal(e.revoked.length, 1)
})

test('dispose revokes all grants and refuses new grants', () => {
  const e = environment()
  e.access.grant(invocation().value, e.agent, binding(), 'source-a')
  e.access.dispose()
  e.access.dispose()
  assert.equal(e.revoked.length, 1)
  assert.equal(e.access.check(e.agent, binding(), 'source-a').authorized, false)
  assert.throws(() => e.access.grant(invocation().value, e.agent, binding(), 'source-a'), error => error.code === 'authorization_required')
})
