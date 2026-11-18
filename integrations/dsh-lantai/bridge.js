import { createHash } from 'node:crypto';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { createMcpToolDefinition } from '@deepseek-ai/dsh-mcp-client';
import { scrubbedParentEnv } from '@deepseek-ai/dsh-subprocess';
import { normalizeConfig } from './config.js';
export { normalizeConfig } from './config.js';

/** No ambient/current-session cache: each envelope is derived from this exact invocation. */
export function attributionMeta(execution) {
  return { 'io.lantai/attribution': {
    version: 1,
    ...(execution.agent?.id === undefined ? {} : { request_session_id: execution.agent.id }),
    ...(execution.callId === undefined ? {} : { request_id: execution.callId }),
  } };
}

/** DSH 0.2.2-rc.3's pinned naming contract; wire calls always retain the raw name. */
export function publicToolName(serverName, rawName) {
  const joined = `mcp__${serverName}__${rawName}`;
  const normalized = joined.replace(/[^A-Za-z0-9_-]/g, '_');
  if (normalized === joined && normalized.length <= 64) return normalized;
  const hash = createHash('sha256').update(`${serverName}\0${rawName}`).digest('hex').slice(0, 12);
  return `${normalized.slice(0, 51)}_${hash}`;
}

function redactor(config) {
  const values = [...Object.values(config.headers ?? {}), ...Object.values(config.env ?? {}), ...(config.args ?? [])];
  // Include tokens after common authorization schemes and HTTP query values.
  for (const value of [...values]) {
    const token = /^(?:Bearer|Basic)\s+(.+)$/i.exec(value)?.[1];
    if (token) values.push(token);
  }
  if (config.url) values.push(config.url, ...new URL(config.url).searchParams.values());
  const secrets = [...new Set(values.filter(Boolean).flatMap(value => [value, encodeURIComponent(value)]))].sort((a, b) => b.length - a.length);
  return text => secrets.reduce((result, secret) => result.split(secret).join('[redacted]'), String(text));
}

function errorText(error) {
  const parts = [];
  const seen = new Set();
  for (let current = error; current !== undefined && !seen.has(current); current = current?.cause) {
    seen.add(current);
    parts.push(current instanceof Error ? current.message : String(current));
  }
  return parts.join(': ');
}

function deadline(promise, ms, message) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function transportFor(config) {
  if (config.transport === 'stdio') {
    return new StdioClientTransport({ command: config.command, args: [...config.args], env: { ...scrubbedParentEnv(), ...config.env }, ...(config.cwd ? { cwd: config.cwd } : {}) });
  }
  return new StreamableHTTPClientTransport(new URL(config.url), { requestInit: { headers: config.headers } });
}

class RegistrationError extends Error {}

/** Preserve SDK pagination, cache and schema handling, but reject incomplete lists.
 * SDK 2.2.0 silently ends an aggregate on an identical page/repeated cursor.
 * Observing its public request boundary catches that before it publishes a list.
 * Discovery is serialized per connection by syncChain; tool calls use no state here.
 */
class DiscoveryClient extends Client {
  async request(request, options) {
    const result = await super.request(request, options);
    if (request.method === 'tools/list' && this.discoveryCursors && result.nextCursor !== undefined) {
      if (this.discoveryCursors.has(result.nextCursor)) throw new Error('MCP tool discovery repeated a pagination cursor');
      this.discoveryCursors.add(result.nextCursor);
    }
    return result;
  }

  async discoverTools(options) {
    this.discoveryCursors = new Set();
    try { return await this.listTools(undefined, options); }
    finally { this.discoveryCursors = undefined; }
  }
}

/**
 * Own one MCP connection supervisor and its registry effects. No profile, DSH event
 * mutation, agent state, or real bridge config is read. Tests can explicitly sync
 * discovery; production uses SDK list-changed notifications.
 */
export function startBridge(ctx, input) {
  const config = normalizeConfig(input);
  const redact = redactor(config);
  const failure = error => new Error(redact(errorText(error)));
  const report = (operation, error) => ctx.logger.warn(`lantai-attribution(${config.serverName}): ${operation}: ${redact(errorText(error))}`);
  let disposed = false;
  let cleanupFailure;
  let current;
  let reconnectTimer;
  let reconnectAttempts = 0;
  let attempt = Promise.resolve();
  let syncChain = Promise.resolve();
  let lossChain = Promise.resolve();
  let stopping;
  let registrations = new Map();
  const generations = new Set();
  const active = record => !disposed && current === record && !record.lifetime.signal.aborted;

  function clearTools() {
    for (const { dispose } of registrations.values()) dispose();
    registrations = new Map();
  }

  function track(record, operation) {
    const promise = Promise.resolve().then(() => {
      record.lifetime.signal.throwIfAborted();
      return operation(record.lifetime.signal);
    });
    record.pending.add(promise);
    void promise.then(() => record.pending.delete(promise), () => record.pending.delete(promise));
    return promise;
  }

  function close(record) {
    if (record.closing) return record.closing;
    record.lifetime.abort(new Error('MCP connection closed'));
    record.closing = deadline((async () => {
      // The transport is retained even when negotiation has not attached it to Client.
      await (record.client.transport === undefined ? record.transport.close() : record.client.close());
      await Promise.allSettled([...record.pending]);
      if (record.started && !record.closeObserved) await record.closed;
    })(), config.closeTimeoutMs, 'MCP cleanup did not settle').catch(error => {
      cleanupFailure ??= failure(error);
      clearTools();
      throw cleanupFailure;
    }).finally(() => {
      if (!cleanupFailure) generations.delete(record);
    });
    return record.closing;
  }

  function enqueueSync(record) {
    const run = syncChain.then(async () => {
      if (!active(record)) throw new Error('MCP connection is unavailable');
      const response = await track(record, signal => record.client.getServerCapabilities()?.tools === undefined
        ? { tools: [] }
        : record.client.discoverTools({ cacheMode: 'refresh', signal: AbortSignal.any([signal, AbortSignal.timeout(config.toolCallTimeoutMs)]), timeout: config.toolCallTimeoutMs }));
      const definitions = new Map();
      for (const tool of response.tools) {
        const name = publicToolName(config.serverName, tool.name);
        if (definitions.has(name)) throw new Error('MCP server listed a duplicate tool more than once');
        const definition = createMcpToolDefinition(ctx, {
          name, rawName: tool.name, description: tool.description ?? '', inputSchema: tool.inputSchema,
          outputSchema: tool.outputSchema, taskRequired: tool.execution?.taskSupport === 'required',
          call: async (args, execution) => {
            try {
              if (!active(record)) throw new Error('MCP connection is unavailable; reload or wait for reconnect');
              const result = await track(record, signal => record.client.callTool(
                { name: tool.name, arguments: args, _meta: attributionMeta(execution) },
                { signal: AbortSignal.any([execution.signal, signal]), timeout: config.toolCallTimeoutMs, toolDefinition: tool },
              ));
              if (result.isError === true) {
                const text = result.content.filter(block => block.type === 'text').map(block => block.text).join('\n');
                return { isError: true, content: [{ type: 'text', text: redact(text || 'MCP tool returned an error') }] };
              }
              return result;
            } catch (error) { throw failure(error); }
          },
        });
        // SDK is the deadline authority; leave a small allowance for its cancellation to settle.
        definition.timeoutMs = Math.min(2_147_483_647, config.toolCallTimeoutMs + 1000);
        definitions.set(name, definition);
      }
      if (!active(record)) throw new Error('MCP connection was closed during discovery');
      // Preflight before disposing the prior generation; collisions never shadow another plugin.
      for (const [name] of definitions) {
        const existing = ctx.tools.get(name);
        if (existing && existing !== registrations.get(name)?.definition) throw new RegistrationError(`Duplicate registered tool: ${name}; disable include:mcp-lantai before enabling this bundle`);
      }
      const previous = registrations;
      clearTools();
      const next = new Map();
      try {
        for (const [name, definition] of definitions) next.set(name, { definition, dispose: ctx.tools.register(definition) });
      } catch (error) {
        for (const { dispose } of next.values()) dispose();
        // A synchronous registry rejection publishes no partial new generation.
        try {
          for (const [name, item] of previous) registrations.set(name, { definition: item.definition, dispose: ctx.tools.register(item.definition) });
        } catch { clearTools(); }
        throw new RegistrationError('MCP tool registration failed', { cause: error });
      }
      registrations = next;
    });
    syncChain = run.catch(() => {});
    return run;
  }

  function scheduleReconnect(connectedAt) {
    if (disposed || cleanupFailure || !config.reconnect.enabled) return;
    if (connectedAt !== undefined && Date.now() - connectedAt >= config.reconnect.maxDelayMs) reconnectAttempts = 0;
    if (reconnectAttempts >= config.reconnect.maxAttempts) {
      clearTools();
      report('reconnect stopped', 'attempt budget exhausted; reload the plugin to retry');
      return;
    }
    const wait = Math.min(config.reconnect.maxDelayMs, config.reconnect.initialDelayMs * 2 ** Math.min(reconnectAttempts++, 31));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      attempt = connect(false);
    }, wait);
    reconnectTimer.unref();
  }

  function lost(record, error) {
    if (!active(record)) return;
    current = undefined;
    report('connection lost', error);
    lossChain = close(record).then(() => scheduleReconnect(record.connectedAt)).catch(error => report('cleanup failed; restart Host before retry', error));
  }

  async function connect(initial) {
    if (disposed || cleanupFailure) return;
    let record;
    try {
      const client = new DiscoveryClient({ name: 'dsh-lantai-attribution', version: '0.1.0' }, {
        capabilities: {}, versionNegotiation: { mode: 'auto' },
        listChanged: { tools: { autoRefresh: false, debounceMs: 0, onChanged: () => {
          if (!active(record)) return;
          void enqueueSync(record).catch(error => {
            if (error instanceof RegistrationError) lost(record, error);
            else if (active(record)) report('tool list refresh failed', error);
          });
        } } },
      });
      const transport = transportFor(config);
      let observedClose;
      record = { client, transport, lifetime: new AbortController(), pending: new Set(), started: false, closeObserved: false,
        closed: new Promise(resolve => { observedClose = resolve; }) };
      generations.add(record);
      current = record;
      const startTransport = transport.start.bind(transport);
      transport.start = () => { record.lifetime.signal.throwIfAborted(); record.started = true; return startTransport(); };
      const closeTransport = transport.close.bind(transport);
      let transportClosing;
      transport.close = () => transportClosing ??= closeTransport();
      transport.onclose = () => { record.closeObserved = true; observedClose(); };
      client.onclose = () => { if (record.connectedAt !== undefined) lost(record, new Error('transport closed')); };
      client.onerror = error => { if (active(record)) report('transport error', error); };
      await deadline((async () => {
        await track(record, signal => client.connect(transport, { signal, timeout: config.startupTimeoutMs }));
        if (!active(record) || record.closeObserved) throw new Error('MCP connection closed during startup');
        await enqueueSync(record);
        if (!active(record) || record.closeObserved) throw new Error('MCP connection closed during discovery');
        record.connectedAt = Date.now();
      })(), config.startupTimeoutMs, 'MCP startup timed out');
    } catch (error) {
      if (current === record) current = undefined;
      if (record) {
        try { await close(record); } catch (closeError) { report('cleanup failed; restart Host before retry', closeError); }
      }
      const publicError = failure(error);
      if (initial && config.failOnStartupError) { clearTools(); throw publicError; }
      if (!disposed) { report('connection failed', publicError); scheduleReconnect(); }
    }
  }

  function dispose() {
    if (stopping) return stopping;
    disposed = true;
    current = undefined;
    clearTimeout(reconnectTimer);
    clearTools();
    stopping = deadline((async () => {
      await Promise.allSettled([...generations].map(close));
      await Promise.allSettled([attempt, lossChain, syncChain]);
      if (cleanupFailure) throw cleanupFailure;
    })(), config.closeTimeoutMs, 'MCP disposal did not settle').catch(error => { throw failure(error); });
    return stopping;
  }

  // Start only after the caller has had a chance to register its teardown effect.
  attempt = Promise.resolve().then(() => connect(true));
  return { ready: attempt, sync: () => current ? enqueueSync(current) : Promise.reject(new Error('MCP connection is unavailable')), dispose };
}
