import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { normalizeConfig, workspaceKey, fetchCoreMemory, CoreMemoryError } from './lib/core.js'
import { createSessionMemory } from './lib/session.js'
import { createSessionGrants } from './lib/access.js'
import { createSshCoreReader } from './lib/ssh.js'
import { readAuthorizedCredentialSource } from './lib/source.js'
import { registerOwnerControl } from './lib/control.js'

export const name = 'lantai-core-memory'
export const inject = ['agents', 'systemPrompt', 'workingDirectory', 'credentials', 'tools', 'typert']

export const Config = z.object({
  enabled: z.boolean().default(false),
  transport: z.string().default('direct'),
  baseUrl: z.string().default(''),
  authMode: z.string().default('bearer'),
  apiKeyEnv: z.string().default('LANTAI_DSH_CORE_TOKEN'),
  allowLoopbackHttp: z.boolean().default(false),
  timeoutMs: z.number().default(1000),
  maxResponseBytes: z.number().default(131072),
  maxContextBytes: z.number().default(16384),
  grantTtlMs: z.number().default(28800000),
  ssh: z.object({
    executable: z.string().default(''), alias: z.string().default(''), expectedHost: z.string().default(''), user: z.string().default('root'),
    sshPort: z.number().default(22), remotePort: z.number().default(18767), credentialFile: z.union([z.string(), z.const(null)]).default(null),
  }).default({}),
  bindings: z.array(z.object({ workspace: z.string(), namespace: z.string().default('default'), userId: z.string(), tenantId: z.union([z.string(), z.const(null)]).default(null), includeTask: z.boolean().default(false) })).default([]),
})

const statusSchema = {
  type: 'object', additionalProperties: false,
  properties: {
    state: { type: 'string', required: true }, reason: { type: 'string', required: true },
    blockCount: { type: 'integer', required: true }, contextBytes: { type: 'integer', required: true },
    versions: { type: 'array', required: true, items: { type: 'object', additionalProperties: false, properties: { block: { type: 'string', required: true }, version: { type: 'integer', required: true } } } },
    outcome: { type: 'string' },
  },
}
const empty = (state, reason) => ({ state, reason, authorized: false, blockCount: 0, contextBytes: 0, versions: [] })
const identityFailures = new Set(['authentication_failed', 'ownership_mismatch', 'ambiguous_blocks', 'invalid_response'])

function abortable(value, signal) {
  if (signal.aborted) return Promise.reject(new CoreMemoryError('aborted'))
  return new Promise((resolve, reject) => {
    const abort = () => reject(new CoreMemoryError('aborted'))
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(value).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/** No automatic read occurs until an actual owner Remote call grants this exact Agent. */
export function apply(ctx, input = {}) {
  const config = normalizeConfig(input)
  if (!config.enabled) return
  const records = new Map()
  let unloaded = false
  const sshReader = config.transport === 'ssh' ? createSshCoreReader({ ...config.ssh, timeoutMs: config.timeoutMs, maxResponseBytes: config.maxResponseBytes, authMode: config.authMode }) : null
  const access = createSessionGrants({
    getAgent: id => ctx.agents.get(id), ttlMs: config.grantTtlMs,
    onRevoke: (agent, reason) => {
      const record = records.get(agent)
      if (!record) return
      record.authorizationAbort?.abort()
      record.transientCredential = null
      record.memory.invalidate(reason)
    },
  })
  const currentFolder = agent => {
    try { return workspaceKey(ctx.workingDirectory.get(agent.session)) } catch { return null }
  }
  const inScope = record => record.binding !== undefined && currentFolder(record.agent) === record.binding.workspaceKey
  const permitted = record => {
    if (!record?.binding || unloaded || record.ended) return false
    if (!inScope(record)) { access.revoke(record.agent, 'scope_changed'); return false }
    return access.check(record.agent, record.binding, config.sourceKey).authorized
  }
  function ownerStatus(record) {
    if (!record) return empty('authorization_required', 'authorization_required')
    const metadata = record.memory.status()
    const grant = record.binding ? access.check(record.agent, record.binding, config.sourceKey) : { authorized: false }
    const reason = record.uiReason || metadata.reason || (grant.authorized ? '' : 'authorization_required')
    return { ...metadata, reason, authorized: grant.authorized, ...(grant.authorized ? { expiresAt: grant.expiresAt } : {}) }
  }

  const bindingFor = folder => config.bindings.find(binding => binding.workspaceKey === folder)

  function createMemory(record, binding) {
    const ownsPermission = () => record.memory === memory && permitted(record)
    const memory = createSessionMemory({
      config, binding, getWorkspace: () => ctx.workingDirectory.get(record.agent.session),
      authorize: ownsPermission,
      resolveCredential: async ref => {
        if (!ownsPermission()) throw new CoreMemoryError('authorization_required')
        const existing = await ctx.credentials.resolve(ref)
        if (!ownsPermission()) throw new CoreMemoryError('authorization_required')
        return existing?.value ? existing : record.transientCredential
      },
      readCore: (options, scope, token, signal) => sshReader ? sshReader.read(scope, token, signal) : fetchCoreMemory(options, scope, token, signal),
    })
    return memory
  }

  function replaceBinding(record, binding) {
    if (unloaded || record.ended) return
    record.authorizationGeneration++
    access.revoke(record.agent, 'scope_changed')
    record.authorizationAbort?.abort()
    record.authorizationAbort = null
    record.transientCredential = null
    void record.memory.dispose()
    record.binding = binding
    record.memory = createMemory(record, binding)
    record.uiReason = 'scope_changed'
  }

  function attach({ agent }) {
    if (unloaded || agent.session.header.origin === 'subagent') return null
    if (records.has(agent)) return records.get(agent)
    const binding = bindingFor(currentFolder(agent))
    const record = { agent, binding, ended: false, uiReason: '', transientCredential: null, authorizationGeneration: 0, authorizationAbort: null, contributions: [] }
    record.memory = createMemory(record, binding)
    record.cleanup = async () => {
      if (record.ended) return
      record.ended = true
      access.revoke(agent, 'agent_disposed')
      record.authorizationAbort?.abort()
      records.delete(agent)
      const released = record.contributions.splice(0).reverse().map(dispose => {
        try { return Promise.resolve(dispose()) } catch { return Promise.resolve() }
      })
      await Promise.allSettled([record.memory.dispose(), ...released])
    }
    record.dispose = agent.ctx.effect(() => {
      record.contributions.push(agent.ctx.systemPrompt.context({ name: 'lantai:core-memory', order: 500, interpolate: false, text: () => record.memory.text() }))
      record.contributions.push(agent.ctx.tools.register(defineTool({
        name: 'lantai_core_status', description: 'Read this session\'s Lantai core-memory status and versions. This cannot grant access and returns no memory content or credentials.', parameters: {},
        output: { schema: statusSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (_args, exec) => exec.agent === agent ? record.memory.status() : { state: 'not_bound', reason: 'agent_mismatch', blockCount: 0, contextBytes: 0, versions: [] },
      })))
      record.contributions.push(agent.ctx.tools.register(defineTool({
        name: 'lantai_core_refresh', description: 'Refresh already-authorized Lantai core memory for this session. A user must first authorize this exact conversation through the owner interface; this tool cannot grant or extend permission. Returns metadata only.', parameters: {},
        output: { schema: statusSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (_args, exec) => {
          if (exec.agent !== agent) return { state: 'not_bound', reason: 'agent_mismatch', blockCount: 0, contextBytes: 0, versions: [], outcome: 'not_bound' }
          const memory = record.memory
          const generation = record.authorizationGeneration
          const result = await memory.load(exec.signal)
          if (record.memory === memory && record.authorizationGeneration === generation && identityFailures.has(result.reason)) {
            access.revoke(agent, 'memory_identity_rejected')
          }
          return result
        },
      })))
      return record.cleanup
    })
    records.set(agent, record)
    // Intentionally no credential lookup, process or fetch in the creation hook.
    return record
  }

  function findRecord(sessionId, create = false) {
    const agent = ctx.agents.get(sessionId)
    if (!agent || agent.session.header.origin === 'subagent') return null
    return records.get(agent) ?? (create ? attach({ agent }) : null)
  }

  async function authorize(sessionId, invocation, signal) {
    if (unloaded) return empty('unavailable', 'disposed')
    const record = findRecord(sessionId, true)
    if (!record) return empty('session_not_active', 'open_session_first')
    // Recheck the current committed folder before issuing the owner's grant.
    const binding = bindingFor(currentFolder(record.agent))
    if (binding !== record.binding) replaceBinding(record, binding)
    if (!binding) return empty('not_bound', 'workspace_not_configured')
    if (!inScope(record)) return empty('unavailable', 'scope_changed')
    const memory = record.memory
    const generation = ++record.authorizationGeneration
    record.authorizationAbort?.abort()
    record.authorizationAbort = null
    record.uiReason = ''
    const controller = new AbortController()
    let timedOut = false
    const cancel = () => {
      controller.abort()
      if (generation === record.authorizationGeneration) {
        access.revoke(record.agent, 'authorization_cancelled')
        record.memory.invalidate('authorization_required')
        record.uiReason = timedOut ? 'timeout' : 'cancelled'
      }
    }
    signal.addEventListener('abort', cancel, { once: true })
    if (signal.aborted) cancel()
    const timer = setTimeout(() => { timedOut = true; cancel() }, config.timeoutMs)
    try {
      controller.signal.throwIfAborted()
      access.grant(invocation, record.agent, binding, config.sourceKey)
      record.authorizationAbort = controller
      const stillAllowed = () => generation === record.authorizationGeneration && record.memory === memory && !controller.signal.aborted && permitted(record)
      const check = () => { if (!stillAllowed()) throw new CoreMemoryError('authorization_required') }
      const task = async () => {
        check()
        const existing = await abortable(ctx.credentials.resolve(config.apiKeyEnv), controller.signal)
        check()
        if (!existing?.value) {
          if (config.transport !== 'ssh' || !config.ssh.credentialFile) throw new CoreMemoryError('missing_credentials')
          const token = await readAuthorizedCredentialSource({ file: config.ssh.credentialFile, expectedHost: config.ssh.expectedHost, remotePort: config.ssh.remotePort, invocation, authorize: stillAllowed, signal: controller.signal })
          check()
          record.transientCredential = { value: token }
          check()
        }
        return memory.load(controller.signal)
      }
      const loaded = await abortable(task(), controller.signal)
      check()
      if (!['ready', 'empty'].includes(loaded.state)) {
        const reason = loaded.reason || 'load_failed'
        access.revoke(record.agent, 'authorization_load_failed')
        record.uiReason = reason
      }
      return ownerStatus(record)
    } catch (error) {
      const failure = timedOut ? 'timeout' : controller.signal.aborted ? 'cancelled' : error instanceof CoreMemoryError ? error.code : 'control_failed'
      if (generation === record.authorizationGeneration) {
        access.revoke(record.agent, 'authorization_cancelled')
        record.memory.invalidate('authorization_required')
        record.uiReason = failure
      }
      return ownerStatus(record)
    } finally {
      clearTimeout(timer)
      signal.removeEventListener('abort', cancel)
      if (generation === record.authorizationGeneration) record.authorizationAbort = null
    }
  }

  const disposeControl = registerOwnerControl(ctx, {
    authorize,
    revoke(sessionId) {
      const record = findRecord(sessionId)
      if (!record) return empty('authorization_required', 'authorization_required')
      access.revoke(record.agent, 'authorization_revoked')
      record.memory.invalidate('authorization_required')
      record.uiReason = 'authorization_revoked'
      return ownerStatus(record)
    },
    status(sessionId) {
      const record = findRecord(sessionId)
      if (!record) return empty(ctx.agents.get(sessionId) ? 'authorization_required' : 'session_not_active', ctx.agents.get(sessionId) ? 'authorization_required' : 'open_session_first')
      return ownerStatus(record)
    },
  })
  ctx.on('agent/created', payload => { attach(payload) })
  ctx.on('working-directory/access', async (request, next) => {
    const beforeKey = currentFolder(request.agent)
    const directory = await next()
    const afterKey = workspaceKey(directory)
    if (afterKey !== beforeKey) {
      const record = records.get(request.agent)
      if (record) replaceBinding(record, bindingFor(afterKey))
    }
    return directory
  })
  ctx.effect(() => async () => {
    unloaded = true
    const owned = [...records.values()]
    // Begin every Agent cleanup synchronously before awaiting Remote withdrawal.
    const cleanups = owned.map(record => record.cleanup())
    access.dispose()
    await Promise.allSettled([disposeControl(), ...cleanups])
    await Promise.allSettled(owned.map(record => record.dispose()))
    if (sshReader) await sshReader.dispose()
    records.clear()
  })
}
