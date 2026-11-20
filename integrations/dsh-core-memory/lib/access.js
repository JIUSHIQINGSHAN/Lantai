import { CoreMemoryError } from './core.js'

const required = () => { throw new CoreMemoryError('authorization_required') }
const validSignal = signal => signal !== null && typeof signal === 'object' && typeof signal.aborted === 'boolean' && typeof signal.addEventListener === 'function' && typeof signal.throwIfAborted === 'function' && !signal.aborted

/** Only the Host-supplied invocation is authority; never accept wire flags. */
export function requireOwnerInvocation(invocation) {
  try {
    if (invocation?.peer?.principal?.kind !== 'owner' || !validSignal(invocation.signal) || !validSignal(invocation.peer.signal)) required()
  } catch { required() }
  return invocation
}
