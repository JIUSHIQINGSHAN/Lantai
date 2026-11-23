'use strict'

const REASONS = ['ready', 'scope_changed', 'unavailable']

function plainObject(value) {
  if (value === null || typeof value !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false
  const descriptors = Object.getOwnPropertyDescriptors(value)
  return Reflect.ownKeys(descriptors).every(key => typeof key === 'string' && Object.hasOwn(descriptors[key], 'value') && descriptors[key].enumerable)
}

function createControlContribution() {
  const enabledInput = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.profilePreference.enabled',
    create: () => ({ parse(value) {
      if (typeof value !== 'boolean') throw new TypeError('Invalid enabled value')
      return value
    } }),
  }
  const metadata = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.profilePreference.status',
    create: () => ({ parse(value) {
      if (!plainObject(value)) throw new TypeError('Invalid preference status')
      const fields = Object.getOwnPropertyDescriptors(value)
      const allowed = ['enabled', 'writable', 'reason', 'folderCount', 'includesTask']
      if (Reflect.ownKeys(fields).some(key => !allowed.includes(key)) || allowed.some(key => !Object.hasOwn(fields, key))) throw new TypeError('Invalid preference status')
      const result = Object.fromEntries(Object.entries(fields).map(([key, descriptor]) => [key, descriptor.value]))
      if (typeof result.enabled !== 'boolean' || typeof result.writable !== 'boolean' || !REASONS.includes(result.reason) || !Number.isSafeInteger(result.folderCount) || result.folderCount < 0 || typeof result.includesTask !== 'boolean') throw new TypeError('Invalid preference status')
      return { enabled: result.enabled, writable: result.writable, reason: result.reason, folderCount: result.folderCount, includesTask: result.includesTask }
    } }),
  }
  const makeDescriptor = (method, parameters) => ({
    id: `@lantai/dsh-core-memory#lantaiCoreMemory/${method}`,
    service: 'lantaiCoreMemory', namespace: 'lantaiCoreMemory', method,
    invocation: { kind: 'direct' }, parameters,
    cancellation: { parameter: 'signal' }, result: metadata,
  })
  return {
    package: '@lantai/dsh-core-memory',
    descriptors: [
      makeDescriptor('status', []),
      makeDescriptor('setEnabled', [{ name: 'enabled', wire: 'enabled', source: 'json', codec: enabledInput }]),
      makeDescriptor('extractionStatus', []),
      makeDescriptor('setExtractionEnabled', [{ name: 'enabled', wire: 'enabled', source: 'json', codec: enabledInput }]),
    ],
  }
}

exports.REASONS = Object.freeze(REASONS.slice())
exports.createControlContribution = createControlContribution
exports.parsePreferenceStatus = value => createControlContribution().descriptors[0].result.create().parse(value)
