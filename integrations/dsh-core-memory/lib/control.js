import { RemoteError, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import contract from './control-contract.cjs'
import { requireOwnerInvocation } from './access.js'

const ownerRequired = () => new RemoteError('lantai-memory/owner-required', 'Owner invocation required', {})
const controlFailed = () => new RemoteError('lantai-memory/control-failed', 'Owner control failed', {})
const invalidResult = () => new RemoteError('lantai-memory/invalid-result', 'Invalid control metadata', {})
const cancelled = () => new RemoteError('gateway/cancelled', 'Owner control cancelled', {})

function ownerInvocation(invocation) {
  try { return requireOwnerInvocation(invocation) } catch { throw ownerRequired() }
}

/** Host-only control interface. Authority always comes from the bound Context;
 * operations own Agent selection, scope, grants and credential handling.
 */
export function registerOwnerControl(ctx, operations) {
  const contribution = contract.createControlContribution()
  const descriptor = contribution.descriptors[0]
  const parseSessionId = descriptor.parameters[0].codec.create().parse
  const parseMetadata = descriptor.result.create().parse
  const lifetime = new AbortController()
  let disposed = false

  async function dispatch(method, sessionId, invocation) {
    if (disposed) throw cancelled()
    const signal = AbortSignal.any([invocation.signal, invocation.peer.signal, lifetime.signal])
    if (signal.aborted) throw cancelled()
    let id
    try { id = parseSessionId(sessionId) } catch { throw controlFailed() }
    let result
    let onAbort
    try {
      const interrupted = new Promise((resolve, reject) => {
        onAbort = () => reject(cancelled())
        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) onAbort()
      })
      const operation = Promise.resolve().then(() => {
        // Recheck after the microtask handoff and before operation lookup.
        if (signal.aborted) throw cancelled()
        return operations[method](id, invocation, signal)
      })
      result = await Promise.race([operation, interrupted])
    } catch {
      if (signal.aborted) throw cancelled()
      throw controlFailed()
    } finally {
      if (onAbort) signal.removeEventListener('abort', onAbort)
    }
    if (signal.aborted || disposed) throw cancelled()
    ownerInvocation(invocation)
    try { return parseMetadata(result) } catch { throw invalidResult() }
  }

  class OwnerControl extends TypertRemoteService {
    constructor(context) { super(context, 'lantaiCoreMemory', { namespace: 'lantaiCoreMemory' }) }
    async authorize(sessionId, signal) {
      let invocation
      try { invocation = requireOwnerInvocation(this.ctx.invocation) } catch { throw ownerRequired() }
      return dispatch('authorize', sessionId, invocation)
    }
    async revoke(sessionId, signal) {
      let invocation
      try { invocation = requireOwnerInvocation(this.ctx.invocation) } catch { throw ownerRequired() }
      return dispatch('revoke', sessionId, invocation)
    }
    async status(sessionId, signal) {
      let invocation
      try { invocation = requireOwnerInvocation(this.ctx.invocation) } catch { throw ownerRequired() }
      return dispatch('status', sessionId, invocation)
    }
  }

  new OwnerControl(ctx)
  const unregister = ctx.typert.register({
    package: contribution.package, face: 'host', schemas: [],
    model: { services: [], events: [], objects: [] }, invocations: contribution.descriptors,
  })
  let disposal
  const dispose = async () => {
    if (disposal) return disposal
    disposed = true
    lifetime.abort()
    disposal = Promise.resolve().then(() => unregister())
    return disposal
  }
  ctx.effect(() => dispose, 'lantai-core-memory.owner-control')
  return dispose
}
