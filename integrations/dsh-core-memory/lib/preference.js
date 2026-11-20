import { createHash } from 'node:crypto'
import path from 'node:path'
import { z } from 'zod'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { CoreMemoryError } from './core.js'

const hashPattern = /^[a-f0-9]{64}$/u
const fail = code => { throw new CoreMemoryError(code) }

/** One profile owns a durable choice for one exact configured memory scope. */
export function createProfilePreference({ storageDomain, profileDirectory, scopeKey, onChange = () => {} }) {
  const valid = typeof storageDomain?.open === 'function' && typeof profileDirectory === 'string' &&
    path.isAbsolute(profileDirectory) && !/[\u0000-\u001f\u007f]/u.test(profileDirectory) && hashPattern.test(scopeKey) && typeof onChange === 'function'
  let selected = false
  let reason = valid ? 'ready' : 'unavailable'
  let disposed = false
  let domain
  let opening
  let chain = Promise.resolve()
  let epoch = 0
  let stopGeneration = 0
  let disposal
  const profile = valid ? path.resolve(profileDirectory) : ''
  const identity = createHash('sha256').update(process.platform === 'win32' ? profile.toLowerCase() : profile).digest('hex')
  const spec = defineDomain({
    name: `lantai_core_memory_${identity}`,
    version: 1,
    tables: { preferences: domainTable(z.object({ scopeKey: z.string().regex(hashPattern), enabled: z.boolean() }).strict()) },
  })

  function publish(enabled) {
    const changed = selected !== enabled
    selected = enabled
    if (changed || !enabled) {
      try { onChange(enabled) } catch {
        selected = false
        reason = 'unavailable'
      }
    }
  }
  const status = () => ({ enabled: !disposed && selected, writable: valid && !disposed, reason: disposed ? 'unavailable' : reason })
  const check = signal => {
    if (disposed) fail('disposed')
    if (!valid) fail('preference_unavailable')
    if (signal?.aborted) fail('aborted')
  }

  async function open() {
    if (domain) return domain
    if (opening) return opening
    opening = Promise.resolve().then(() => storageDomain.open(spec)).then(value => {
      domain = value
      return value
    }).finally(() => { opening = undefined })
    return opening
  }
  async function quarantine() {
    publish(false)
    reason = 'unavailable'
    const previous = domain
    domain = undefined
    try { await previous?.close() } catch { /* This instance remains off. */ }
  }
  const ready = (async () => {
    if (!valid) return
    try {
      const opened = await open()
      if (disposed) return
      const saved = opened.table('preferences').get('current')
      reason = saved && saved.scopeKey !== scopeKey ? 'scope_changed' : 'ready'
      // A choice made while storage opens takes precedence over the snapshot.
      publish(epoch === 0 && Boolean(saved?.scopeKey === scopeKey && saved.enabled))
    } catch { await quarantine() }
  })()

  function setEnabled(enabled, signal) {
    if (typeof enabled !== 'boolean') return Promise.reject(new CoreMemoryError('invalid_config'))
    try { check(signal) } catch (error) { return Promise.reject(error) }
    epoch++
    // An accepted stop is durable intent even if its caller disconnects or a
    // later enable is queued. Only enables use the cancellation barrier.
    if (!enabled) {
      stopGeneration++
      publish(false)
    }
    const acceptedStop = stopGeneration
    const checkEnable = () => {
      check(signal)
      if (acceptedStop !== stopGeneration) fail('preference_superseded')
    }
    const operation = chain.then(async () => {
      await ready
      if (enabled) checkEnable()
      try {
        const opened = await open()
        if (enabled) checkEnable()
        await opened.table('preferences').put('current', { scopeKey, enabled })
        if (enabled && (disposed || signal?.aborted || acceptedStop !== stopGeneration)) {
          // Domain writes cannot be cancelled once begun. Persist the rollback
          // before reporting an interrupted enable, so restart also remains off.
          await opened.table('preferences').put('current', { scopeKey, enabled: false })
          publish(false)
          reason = 'ready'
          if (signal?.aborted) fail('aborted')
          if (disposed) fail('disposed')
          fail('preference_superseded')
        }
        if (enabled) checkEnable()
        reason = 'ready'
        publish(enabled)
        return status()
      } catch (error) {
        if (error instanceof CoreMemoryError && ['aborted', 'disposed', 'preference_superseded'].includes(error.code)) throw error
        await quarantine()
        fail('preference_unavailable')
      }
    })
    chain = operation.then(() => undefined, () => undefined)
    return operation
  }

  function dispose() {
    if (disposal) return disposal
    disposed = true
    epoch++
    publish(false)
    disposal = (async () => {
      await ready
      await chain
      const owned = domain
      domain = undefined
      await owned?.close()
    })()
    return disposal
  }
  return Object.freeze({ ready, enabled: () => !disposed && selected, status, setEnabled, dispose })
}
