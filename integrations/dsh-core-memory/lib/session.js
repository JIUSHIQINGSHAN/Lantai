import { CoreMemoryError, fetchCoreMemory, selectSnapshot, workspaceKey } from './core.js'

/** Await an external adapter without letting it defeat operation cancellation. */
function abortable(value, signal) {
  if (signal.aborted) return Promise.reject(new CoreMemoryError('aborted'))
  return new Promise((resolve, reject) => {
    const abort = () => reject(new CoreMemoryError('aborted'))
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(value).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/** One Agent owns one authorized snapshot. Missing policy is denial, not owner inference. */
export function createSessionMemory({ config, binding, getWorkspace, resolveCredential, authorize = () => false, readCore = fetchCoreMemory }) {
  let state = binding ? 'idle' : 'not_bound'
  let reason = binding ? '' : 'not_bound'
  let snapshot = null
  let disposed = false
  let generation = 0
  let active
  const operations = new Set()

  const inScope = () => {
    try { return binding !== undefined && binding !== null && workspaceKey(getWorkspace()) === binding.workspaceKey }
    catch { return false }
  }
  const metadata = () => ({
    state, reason,
    blockCount: snapshot?.blockCount ?? 0,
    contextBytes: snapshot?.bytes ?? 0,
    versions: snapshot?.versions.map(item => ({ ...item })) ?? [],
  })

  function invalidate(code = 'scope_changed') {
    if (disposed) return
    generation++
    active?.abort()
    active = undefined
    snapshot = null
    state = 'unavailable'
    reason = code
  }

  function ensureAuthorization() {
    if (disposed || !binding) return false
    let permitted = false
    try { permitted = typeof authorize === 'function' && authorize() === true } catch { /* Unknown authority is denial. */ }
    if (!permitted && (reason !== 'authorization_required' || active || snapshot)) invalidate('authorization_required')
    return permitted
  }

  function ensureScope() {
    if (disposed || !binding || !inScope()) {
      if (!disposed && binding && reason !== 'scope_changed') invalidate('scope_changed')
      return false
    }
    return true
  }

  function status() {
    if (ensureAuthorization()) ensureScope()
    return metadata()
  }

  function text() {
    if (!ensureAuthorization() || !ensureScope()) return ''
    // Operational failures remain in metadata, not in an unauthorized model context.
    return snapshot?.text ?? ''
  }

  async function execute(signal) {
    if (disposed) return { ...metadata(), outcome: 'disposed' }
    if (!binding) return { ...metadata(), outcome: 'not_bound' }
    if (!ensureAuthorization()) return { ...metadata(), outcome: 'denied' }
    if (!ensureScope()) return { ...metadata(), outcome: 'failed' }
    const current = ++generation
    active?.abort()
    const controller = new AbortController()
    active = controller
    let timedOut = false
    const cancel = () => controller.abort()
    if (signal?.aborted) controller.abort()
    else signal?.addEventListener('abort', cancel, { once: true })
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, config.timeoutMs)
    snapshot = null
    state = 'loading'
    reason = ''
    let outcome = 'failed'
    try {
      controller.signal.throwIfAborted()
      // Recheck immediately before any credential adapter can be invoked.
      if (!ensureAuthorization()) return { ...metadata(), outcome: 'denied' }
      let credential
      try { credential = await abortable(resolveCredential(config.apiKeyEnv, controller.signal), controller.signal) }
      catch (error) {
        if (controller.signal.aborted || error instanceof CoreMemoryError) throw error
        throw new CoreMemoryError('credential_error')
      }
      controller.signal.throwIfAborted()
      if (current !== generation || disposed) return { ...metadata(), outcome: 'superseded' }
      if (!ensureAuthorization()) return { ...metadata(), outcome: 'denied' }
      if (!ensureScope()) return { ...metadata(), outcome: 'failed' }
      const payload = await abortable(readCore(config, binding, credential?.value, controller.signal), controller.signal)
      controller.signal.throwIfAborted()
      if (current !== generation || disposed) return { ...metadata(), outcome: 'superseded' }
      if (!ensureAuthorization()) return { ...metadata(), outcome: 'denied' }
      if (!ensureScope()) return { ...metadata(), outcome: 'failed' }
      const candidate = selectSnapshot(payload, binding, config.maxContextBytes)
      if (!ensureAuthorization()) return { ...metadata(), outcome: 'denied' }
      snapshot = candidate
      state = candidate.blockCount === 0 ? 'empty' : 'ready'
      reason = ''
      outcome = candidate.blockCount === 0 ? 'empty' : 'loaded'
    } catch (error) {
      if (current === generation && !disposed) {
        snapshot = null
        state = 'unavailable'
        reason = controller.signal.aborted ? (timedOut ? 'timeout' : 'cancelled') : (error instanceof CoreMemoryError ? error.code : 'internal_error')
        outcome = controller.signal.aborted && !timedOut ? 'cancelled' : 'failed'
      } else outcome = disposed ? 'disposed' : 'superseded'
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', cancel)
      if (current === generation) active = undefined
    }
    return { ...metadata(), outcome }
  }

  function load(signal) {
    const operation = execute(signal)
    operations.add(operation)
    operation.finally(() => operations.delete(operation))
    return operation
  }

  async function dispose() {
    if (!disposed) {
      disposed = true
      generation++
      active?.abort()
      active = undefined
      snapshot = null
      state = 'disposed'
      reason = 'disposed'
    }
    await Promise.allSettled([...operations])
  }

  return Object.freeze({ load, status, text, invalidate, dispose })
}
