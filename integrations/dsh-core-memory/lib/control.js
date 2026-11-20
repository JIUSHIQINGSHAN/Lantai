import { RemoteError, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import contract from './control-contract.cjs'
import { requireOwnerInvocation } from './access.js'

const ownerRequired = () => new RemoteError('lantai-memory/owner-required', 'Owner invocation required', {})
const controlFailed = () => new RemoteError('lantai-memory/control-failed', 'Owner control failed', {})
const invalidResult = () => new RemoteError('lantai-memory/invalid-result', 'Invalid control metadata', {})
const cancelled = () => new RemoteError('gateway/cancelled', 'Owner control cancelled', {})
const checkOwner = invocation => { try { return requireOwnerInvocation(invocation) } catch { throw ownerRequired() } }

/** The owner-only settings surface, returning no connection or memory body. */
export function registerOwnerControl(ctx, operations) {
  const contribution = contract.createControlContribution()
  const parseResult = contribution.descriptors[0].result.create().parse
  const parseEnabled = contribution.descriptors[1].parameters[0].codec.create().parse
  const lifetime = new AbortController()
  let disposed = false

  async function dispatch(method, value, invocation) {
    if (disposed) throw cancelled()
    const signal = AbortSignal.any([invocation.signal, invocation.peer.signal, lifetime.signal])
    if (signal.aborted) throw cancelled()
    let onAbort
    let result
    try {
      const interrupted = new Promise((resolve, reject) => {
        onAbort = () => reject(cancelled())
        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) onAbort()
      })
      const operation = Promise.resolve().then(() => {
        if (signal.aborted) throw cancelled()
        checkOwner(invocation)
        return method === 'status' ? operations.status() : operations.setEnabled(parseEnabled(value), signal)
      })
      result = await Promise.race([operation, interrupted])
    } catch {
      if (signal.aborted) throw cancelled()
      throw controlFailed()
    } finally {
      if (onAbort) signal.removeEventListener('abort', onAbort)
    }
    if (signal.aborted || disposed) throw cancelled()
    checkOwner(invocation)
    try { return parseResult(result) } catch { throw invalidResult() }
  }

  class OwnerControl extends TypertRemoteService {
    constructor(context) { super(context, 'lantaiCoreMemory', { namespace: 'lantaiCoreMemory' }) }
    async status(signal) { return dispatch('status', undefined, checkOwner(this.ctx.invocation)) }
    async setEnabled(enabled, signal) { return dispatch('setEnabled', enabled, checkOwner(this.ctx.invocation)) }
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
