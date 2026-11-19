import { CoreMemoryError, fetchCoreMemory, selectSnapshot, workspaceKey } from './core.js'

/** Await an external resolver without letting its failure to settle block cancellation. */
function abortable(value, signal) {
  if (signal.aborted) return Promise.reject(new CoreMemoryError('aborted'))
  return new Promise((resolve, reject) => {
    const abort = () => reject(new CoreMemoryError('aborted'))
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(value).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/** One agent owns one immutable snapshot; no global or on-disk content cache. */
export function createSessionMemory({ config, binding, getWorkspace, resolveCredential }) {
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
    state,
    reason,
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

  function ensureScope() {
    if (!disposed && binding && !inScope() && reason !== 'scope_changed') invalidate('scope_changed')
  }

  function status() {
    ensureScope()
    return metadata()
  }

  function text() {
    ensureScope()
    if (disposed || !binding) return ''
    if (snapshot) return snapshot.text
    // A fixed diagnostic prevents a missing startup from being presented as loaded memory.
    return state === 'idle' ? '' : `Lantai core memory is not loaded (${reason || state}). Earlier Lantai core-memory data is not current.`
  }

  async function execute(signal) {
    if (disposed) return { ...metadata(), outcome: 'disposed' }
    if (!binding) return { ...metadata(), outcome: 'not_bound' }
    if (!inScope()) {
      invalidate('scope_changed')
      return { ...metadata(), outcome: 'failed' }
    }
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
      let credential
      try {
        credential = await abortable(resolveCredential(config.apiKeyEnv), controller.signal)
      } catch (error) {
        if (controller.signal.aborted) throw error
        throw new CoreMemoryError('credential_error')
      }
      controller.signal.throwIfAborted()
      if (current !== generation || disposed) return { ...metadata(), outcome: 'superseded' }
      if (!inScope()) {
        invalidate('scope_changed')
        return { ...metadata(), outcome: 'failed' }
      }
      const payload = await fetchCoreMemory(config, binding, credential?.value, controller.signal)
      const candidate = selectSnapshot(payload, binding, config.maxContextBytes)
      controller.signal.throwIfAborted()
      if (current !== generation || disposed) return { ...metadata(), outcome: 'superseded' }
      if (!inScope()) {
        invalidate('scope_changed')
        return { ...metadata(), outcome: 'failed' }
      }
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
      } else {
        outcome = disposed ? 'disposed' : 'superseded'
      }
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
