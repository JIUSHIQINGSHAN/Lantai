import test from 'node:test'
import assert from 'node:assert/strict'
import { setTimeout as delay } from 'node:timers/promises'
import { createSessionGrants } from '../lib/access.js'

const agent = () => ({ id: 'timer-session', session: { header: {} } })
const binding = { workspaceKey: 'fixture', namespace: 'fixture', userId: 'fixture-user', tenantId: null, includeTask: false }
const invocation = () => ({ peer: { principal: { kind: 'owner' }, signal: new AbortController().signal }, signal: new AbortController().signal })

test('grant expiry actively cancels a waiting read without another status or model step', async t => {
  const live = agent()
  const transport = new AbortController()
  let revoked = 0
  const grants = createSessionGrants({ getAgent: () => live, ttlMs: 25, onRevoke() { revoked++; transport.abort() } })
  t.after(() => grants.dispose())
  grants.grant(invocation(), live, binding, 'fixture-source')
  await delay(65)
  assert.equal(transport.signal.aborted, true)
  assert.equal(revoked, 1)
  assert.equal(grants.check(live, binding, 'fixture-source').authorized, false)
})

test('an old expiry callback cannot revoke a newly renewed record', async t => {
  const live = agent()
  let revoked = 0
  const grants = createSessionGrants({ getAgent: () => live, ttlMs: 100, onRevoke() { revoked++ } })
  t.after(() => grants.dispose())
  grants.grant(invocation(), live, binding, 'fixture-source')
  await delay(60)
  grants.grant(invocation(), live, binding, 'fixture-source')
  assert.equal(revoked, 1, 'renewal explicitly revokes the original authority')
  await delay(55)
  assert.equal(grants.check(live, binding, 'fixture-source').authorized, true)
  assert.equal(revoked, 1, 'the old deadline must not revoke the replacement')
})
