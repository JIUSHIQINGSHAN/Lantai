import { CoreMemoryError } from './core.js'

const required = () => { throw new CoreMemoryError('authorization_required') }
const text = (value, maximum = 256) => typeof value === 'string' && value.length > 0 && value.length <= maximum && !/[\u0000-\u001f\u007f]/u.test(value)
const validSignal = signal => signal !== null && typeof signal === 'object' && typeof signal.aborted === 'boolean' && typeof signal.addEventListener === 'function' && typeof signal.throwIfAborted === 'function' && !signal.aborted

/** Accept only the Host-supplied invocation of an active owner Remote call.
 * The caller must take this capability from its bound Context, never JSON/tool arguments.
 */
export function requireOwnerInvocation(invocation) {
  try {
    if (invocation?.peer?.principal?.kind !== 'owner' || !validSignal(invocation.signal) || !validSignal(invocation.peer.signal)) required()
  } catch { required() }
  return invocation
}

function scopeOf(binding, sourceKey) {
  if (!binding || typeof binding !== 'object' || !text(sourceKey, 4096)) return null
  if (!text(binding.workspaceKey, 4096) || !text(binding.namespace) || !text(binding.userId)) return null
  if (!Object.hasOwn(binding, 'tenantId') || (binding.tenantId !== null && !text(binding.tenantId))) return null
  if (typeof binding.includeTask !== 'boolean') return null
  return JSON.stringify([binding.workspaceKey, binding.namespace, binding.userId, binding.tenantId, binding.includeTask, sourceKey])
}

/** Short interface for exact-Agent grants. It is not a wire-facing authority issuer. */
export function createSessionGrants({ getAgent, now = Date.now, ttlMs = 28800000, onRevoke = () => {} }) {
  if (typeof getAgent !== 'function' || typeof now !== 'function' || !Number.isSafeInteger(ttlMs) || ttlMs < 1 || ttlMs > 86400000 || typeof onRevoke !== 'function') throw new CoreMemoryError('invalid_config')
  const records = new Map()
  let disposed = false

  function liveRoot(agent) {
    try {
      return agent !== null && typeof agent === 'object' && text(agent.id, 1024) && agent.session?.header !== undefined && agent.session.header.origin === undefined && getAgent(agent.id) === agent
    } catch { return false }
  }

  function revoke(agent, reason = 'authorization_revoked') {
    const previous = records.get(agent)
    if (!previous || !records.delete(agent)) return
    clearTimeout(previous.timer)
    // Reduce authority before notifying a consumer; its failure cannot restore a grant.
    try { onRevoke(agent, reason) } catch { /* The permission is already gone. */ }
  }

  function check(agent, binding, sourceKey) {
    const record = records.get(agent)
    if (disposed || !record) return { authorized: false, reason: 'authorization_required' }
    try {
      const time = now()
      const scope = scopeOf(binding, sourceKey)
      if (!liveRoot(agent) || scope === null || scope !== record.scope || !Number.isSafeInteger(time) || time < record.issuedAt || time >= record.expiresAt) {
        revoke(agent, 'authorization_expired_or_changed')
        return { authorized: false, reason: 'authorization_required' }
      }
      return { authorized: true, expiresAt: record.expiresAt }
    } catch {
      revoke(agent, 'authorization_invalid')
      return { authorized: false, reason: 'authorization_required' }
    }
  }

  function grant(invocation, agent, binding, sourceKey) {
    // This is deliberately first: rejected callers cannot observe Agent/scope lookups.
    requireOwnerInvocation(invocation)
    if (disposed || !liveRoot(agent)) required()
    let scope
    let time
    try { scope = scopeOf(binding, sourceKey); time = now() } catch { required() }
    if (scope === null || !Number.isSafeInteger(time) || time < 0 || !Number.isSafeInteger(time + ttlMs)) required()
    requireOwnerInvocation(invocation)
    revoke(agent, 'authorization_replaced')
    const record = { scope, issuedAt: time, expiresAt: time + ttlMs, timer: null }
    record.timer = setTimeout(() => {
      if (records.get(agent) === record) revoke(agent, 'authorization_expired')
    }, ttlMs)
    record.timer.unref?.()
    Object.freeze(record)
    records.set(agent, record)
    return { authorized: true, expiresAt: record.expiresAt }
  }

  function dispose() {
    if (disposed) return
    disposed = true
    for (const agent of [...records.keys()]) revoke(agent, 'authorization_disposed')
  }

  return Object.freeze({ grant, check, revoke, dispose })
}
