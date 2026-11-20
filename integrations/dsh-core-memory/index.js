import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { normalizeConfig, workspaceKey, fetchCoreMemory, CoreMemoryError } from './lib/core.js'
import { createSessionMemory } from './lib/session.js'
import { createProfilePreference } from './lib/preference.js'
import { createSshCoreReader } from './lib/ssh.js'
import { readAuthorizedCredentialSource } from './lib/source.js'
import { registerOwnerControl } from './lib/control.js'

export const name = 'lantai-core-memory'
export const inject = ['agents', 'systemPrompt', 'workingDirectory', 'credentials', 'tools', 'typert', 'storageDomain', 'profileContext']

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
const wrongAgent = () => ({ state: 'not_bound', reason: 'agent_mismatch', blockCount: 0, contextBytes: 0, versions: [] })
const toolOutput = { schema: statusSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }

export function apply(ctx, input = {}) {
  const config = normalizeConfig(input)
  if (!config.enabled) return
  const records = new Map()
  let unloaded = false
  const sshReader = config.transport === 'ssh'
    ? createSshCoreReader({ ...config.ssh, timeoutMs: config.timeoutMs, maxResponseBytes: config.maxResponseBytes, authMode: config.authMode })
    : null
  const preference = createProfilePreference({
    storageDomain: ctx.storageDomain,
    profileDirectory: ctx.profileContext.dir,
    scopeKey: config.scopeKey,
    onChange(enabled) {
      if (enabled) return
      for (const record of records.values()) {
        record.attemptedTurn = undefined
        record.memory.invalidate('authorization_required')
      }
    },
  })
  const bindings = new Map(config.bindings.map(binding => [binding.workspaceKey, binding]))
  const folderCount = bindings.size
  const includesTask = config.bindings.some(binding => binding.includeTask)
  const summarize = value => ({ ...value, folderCount, includesTask })

  function currentBinding(agent) {
    try { return bindings.get(workspaceKey(ctx.workingDirectory.get(agent.session))) }
    catch { return undefined }
  }

  function makeMemory(record, binding) {
    const { agent } = record
    const permitted = () => !unloaded && !record.ended && binding !== undefined &&
      record.memory === memory && preference.enabled() && ctx.agents.get(agent.id) === agent && currentBinding(agent) === binding
    function check(signal) {
      signal.throwIfAborted()
      if (!permitted()) throw new CoreMemoryError('authorization_required')
    }
    const memory = createSessionMemory({
      config, binding, getWorkspace: () => ctx.workingDirectory.get(agent.session), authorize: permitted,
      async resolveCredential(ref, signal) {
        check(signal)
        const existing = await ctx.credentials.resolve(ref)
        check(signal)
        if (existing?.value) return existing
        if (!sshReader || !config.ssh.credentialFile) throw new CoreMemoryError('missing_credentials')
        const value = await readAuthorizedCredentialSource({
          file: config.ssh.credentialFile, expectedHost: config.ssh.expectedHost, remotePort: config.ssh.remotePort,
          authorize: permitted, signal,
        })
        check(signal)
        return { value }
      },
      readCore: (options, scope, credential, signal) => sshReader ? sshReader.read(scope, credential, signal) : fetchCoreMemory(options, scope, credential, signal),
    })
    return memory
  }

  function synchronizeBinding(record) {
    if (unloaded || record.ended) return
    const binding = currentBinding(record.agent)
    if (record.binding === binding) return
    // dispose() invalidates the old snapshot and aborts its operation synchronously.
    void record.memory.dispose()
    record.binding = binding
    record.attemptedTurn = undefined
    record.memory = makeMemory(record, binding)
  }

  function attach(agent) {
    if (unloaded || records.has(agent) || agent.session.header.origin !== undefined) return
    if (ctx.agents.get(agent.id) !== agent) return
    try { agent.ctx.fiber.assertActive() } catch { return }
    const binding = currentBinding(agent)
    const record = { agent, binding, attemptedTurn: undefined, ended: false }
    record.memory = makeMemory(record, binding)
    records.set(agent, record)
    record.dispose = agent.ctx.effect(() => {
      const removeContext = agent.ctx.systemPrompt.context({
        name: 'lantai:core-memory', order: 500, interpolate: false, text: () => record.memory.text(),
      })
      const removeStep = agent.ctx.on('agent/pre-step', async (request, next) => {
        const decision = await next()
        if (decision.kind !== 'enter' || request.agent !== agent || request.signal.aborted || unloaded || record.ended) return decision
        await preference.ready
        synchronizeBinding(record)
        if (request.signal.aborted || unloaded || record.ended || !record.binding || !preference.enabled()) return decision
        const { state } = record.memory.status()
        if (state === 'ready' || state === 'empty' || state === 'loading' || record.attemptedTurn === request.turn) return decision
        // Only a requesting conversation waits for a read. Prepare at most once per turn.
        record.attemptedTurn = request.turn
        await record.memory.load(request.signal)
        return decision
      })
      const removeStatus = agent.ctx.tools.register(defineTool({
        name: 'lantai_core_status', description: 'Read this conversation’s Lantai core-memory status and versions. Returns metadata only.', parameters: {}, output: toolOutput,
        execute: async (_args, exec) => {
          if (exec.agent !== agent) return wrongAgent()
          synchronizeBinding(record)
          return record.memory.status()
        },
      }))
      const removeRefresh = agent.ctx.tools.register(defineTool({
        name: 'lantai_core_refresh', description: 'Refresh Lantai core memory in this conversation when the user has enabled it in Settings. Returns metadata only.', parameters: {}, output: toolOutput,
        execute: async (_args, exec) => {
          if (exec.agent !== agent) return { ...wrongAgent(), outcome: 'not_bound' }
          synchronizeBinding(record)
          return record.memory.load(exec.signal)
        },
      }))
      return async () => {
        record.ended = true
        records.delete(agent)
        const draining = record.memory.dispose()
        await Promise.allSettled([draining, ...[removeRefresh, removeStatus, removeStep, removeContext].map(async remove => remove())])
      }
    })
  }

  const disposeControl = registerOwnerControl(ctx, {
    async status() { await preference.ready; return summarize(preference.status()) },
    async setEnabled(enabled, signal) { return summarize(await preference.setEnabled(enabled, signal)) },
  })
  ctx.on('agent/created', ({ agent }) => { attach(agent) })
  ctx.on('working-directory/access', async (request, next) => {
    const directory = await next()
    const record = records.get(request.agent)
    if (record) synchronizeBinding(record)
    return directory
  })
  ctx.effect(() => async () => {
    unloaded = true
    const owned = [...records.values()]
    for (const record of owned) record.memory.invalidate('disposed')
    await Promise.allSettled([disposeControl(), preference.dispose(), ...owned.map(record => record.dispose())])
    if (sshReader) await sshReader.dispose()
    records.clear()
  })
  for (const agent of ctx.agents.list()) attach(agent)
  return preference.ready
}
