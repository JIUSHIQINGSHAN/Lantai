'use strict'

// Shared by Host ESM and the Client lazy factory. Keep this file dependency-free.
function createControlContribution() {
  const sessionId = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.control.sessionId.string',
    create: () => ({ parse(value) {
      if (typeof value !== 'string' || value.length < 1 || value.length > 1024 || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) {
        throw new TypeError('Invalid sessionId')
      }
      return value
    } }),
  }
  const metadata = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.control.metadata',
    create: () => ({ parse(value) {
      const invalid = () => { throw new TypeError('Invalid control metadata') }
      const object = (input, required, optional = []) => {
        if (input === null || typeof input !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(input))) invalid()
        const fields = Object.getOwnPropertyDescriptors(input)
        if (Reflect.ownKeys(fields).some(key => typeof key !== 'string' || ![...required, ...optional].includes(key) || !Object.hasOwn(fields[key], 'value') || !fields[key].enumerable)) invalid()
        if (required.some(key => !Object.hasOwn(fields, key))) invalid()
        return Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value]))
      }
      const result = object(value, ['state', 'reason', 'authorized', 'blockCount', 'contextBytes', 'versions'], ['expiresAt', 'outcome'])
      if (typeof result.state !== 'string' || typeof result.reason !== 'string' || typeof result.authorized !== 'boolean') invalid()
      if (![result.blockCount, result.contextBytes].every(count => Number.isSafeInteger(count) && count >= 0)) invalid()
      if (Object.hasOwn(result, 'expiresAt') && !Number.isSafeInteger(result.expiresAt)) invalid()
      if (Object.hasOwn(result, 'outcome') && typeof result.outcome !== 'string') invalid()
      if (!Array.isArray(result.versions)) invalid()
      const entries = Object.getOwnPropertyDescriptors(result.versions)
      if (Reflect.ownKeys(entries).length !== result.versions.length + 1) invalid()
      result.versions = Array.from({ length: result.versions.length }, (_, index) => {
        const entry = entries[index]
        if (!entry || !Object.hasOwn(entry, 'value') || !entry.enumerable) invalid()
        const version = object(entry.value, ['block', 'version'])
        if (!['policy', 'identity', 'task'].includes(version.block) || !Number.isSafeInteger(version.version) || version.version < 1) invalid()
        return version
      })
      return result
    } }),
  }
  return {
    package: '@lantai/dsh-core-memory',
    descriptors: ['authorize', 'revoke', 'status'].map(method => ({
      id: `@lantai/dsh-core-memory#lantaiCoreMemory/${method}`,
      service: 'lantaiCoreMemory', namespace: 'lantaiCoreMemory', method,
      invocation: { kind: 'direct' },
      parameters: [{ name: 'sessionId', wire: 'sessionId', source: 'json', codec: sessionId }],
      cancellation: { parameter: 'signal' }, result: metadata,
    })),
  }
}

exports.createControlContribution = createControlContribution
