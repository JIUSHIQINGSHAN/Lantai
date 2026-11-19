import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { normalizeConfig, workspaceKey } from './lib/core.js'
import { createSessionMemory } from './lib/session.js'

export const name = 'lantai-core-memory'
export const inject = ['agents', 'systemPrompt', 'workingDirectory', 'credentials', 'tools']

export const Config = z.object({
  enabled: z.boolean().default(false),
  baseUrl: z.string().default(''),
  apiKeyEnv: z.string().default('LANTAI_DSH_CORE_TOKEN'),
  allowLoopbackHttp: z.boolean().default(false),
  timeoutMs: z.number().default(1000),
  maxResponseBytes: z.number().default(131072),
  maxContextBytes: z.number().default(16384),
  bindings: z.array(z.object({
    workspace: z.string(),
    namespace: z.string().default('default'),
    userId: z.string(),
    tenantId: z.union([z.string(), z.const(null)]).default(null),
    includeTask: z.boolean().default(false),
  })).default([]),
})

const statusSchema = {
  type: 'object',
  additionalProperties: false,
  properties: {
    state: { type: 'string', required: true },
    reason: { type: 'string', required: true },
    blockCount: { type: 'integer', required: true },
    contextBytes: { type: 'integer', required: true },
    versions: { type: 'array', required: true, items: { type: 'object', additionalProperties: false, properties: {
      block: { type: 'string', required: true }, version: { type: 'integer', required: true },
    } } },
    outcome: { type: 'string' },
  },
}

/** Independent opt-in Host bundle; all contributions are also owned by the agent scope. */
export function apply(ctx, input = {}) {
  const config = normalizeConfig(input)
  if (!config.enabled) return
  const agents = new Map()
  let unloaded = false

  async function attach({ agent, source, signal }) {
    if (unloaded || agent.session.header.origin === 'subagent') return
    // Compaction can announce an existing Agent; preserve its stable snapshot.
    if (agents.has(agent)) return
    const key = workspaceKey(ctx.workingDirectory.get(agent.session))
    const binding = config.bindings.find(item => item.workspaceKey === key)
    const memory = createSessionMemory({
      config, binding,
      getWorkspace: () => ctx.workingDirectory.get(agent.session),
      resolveCredential: ref => ctx.credentials.resolve(ref),
    })
    let localDispose
    let ended = false
    const contributions = []
    const cleanup = async () => {
      if (ended) return
      ended = true
      agents.delete(agent)
      const released = contributions.splice(0).reverse().map(dispose => {
        try { return Promise.resolve(dispose()) } catch { return Promise.resolve() }
      })
      await Promise.allSettled([memory.dispose(), ...released])
    }
    localDispose = agent.ctx.effect(() => {
      contributions.push(agent.ctx.systemPrompt.context({
        name: 'lantai:core-memory', order: 500,
        interpolate: false,
        text: () => memory.text(),
      }))
      contributions.push(agent.ctx.tools.register(defineTool({
        name: 'lantai_core_status',
        description: 'Read this session\'s Lantai core-memory loading status and versions. Returns metadata only; no memory content or credentials.',
        parameters: {},
        output: { schema: statusSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (_args, exec) => exec.agent === agent ? memory.status() : { state: 'not_bound', reason: 'agent_mismatch', blockCount: 0, contextBytes: 0, versions: [] },
      })))
      contributions.push(agent.ctx.tools.register(defineTool({
        name: 'lantai_core_refresh',
        description: 'Explicitly reload this session\'s configured Lantai core-memory blocks. Read-only remote operation. A successful snapshot appears in the next model step; failure clears the active snapshot. Returns metadata only.',
        parameters: {},
        output: { schema: statusSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (_args, exec) => exec.agent === agent ? memory.load(exec.signal) : { state: 'not_bound', reason: 'agent_mismatch', blockCount: 0, contextBytes: 0, versions: [], outcome: 'not_bound' },
      })))
      return cleanup
    })
    agents.set(agent, { memory, dispose: localDispose, cleanup })
    if (binding && !unloaded) await memory.load(signal)
    // Initialization remains bounded and failures are represented by memory.status().
    void source
  }

  ctx.on('agent/created', attach)
  ctx.on('working-directory/access', async (request, next) => {
    // A requested change invalidates before asynchronous validation. Even a failed change
    // requires explicit refresh; a transient leave-and-return never revives an old snapshot.
    if (request.path !== null) agents.get(request.agent)?.memory.invalidate('scope_changed')
    return next()
  })
  ctx.effect(() => async () => {
    unloaded = true
    const owned = [...agents.values()]
    await Promise.allSettled(owned.map(async entry => {
      await entry.cleanup()
      await entry.dispose()
    }))
    agents.clear()
  })
}
