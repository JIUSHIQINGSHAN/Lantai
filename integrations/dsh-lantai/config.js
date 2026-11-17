import z from '@deepseek-ai/schemastery';

const MAX_DELAY = 2_147_483_647;
const serverPattern = /^[A-Za-z0-9_-]{1,32}$/;
const reconnectDefaults = Object.freeze({ enabled: true, initialDelayMs: 500, maxDelayMs: 30000, maxAttempts: 10 });
const delay = (value, name) => {
  if (!Number.isSafeInteger(value) || value < 1 || value > MAX_DELAY) throw new Error(`${name} must be an integer between 1 and ${MAX_DELAY}`);
  return value;
};
const string = (value, name, required = false) => {
  if (typeof value !== 'string' || (required && !value.trim())) throw new Error(`${name} must be ${required ? 'a nonempty' : 'a'} string`);
  if (value.includes('${')) throw new Error(`${name} must be static; template expressions are unsupported`);
  return value;
};
const record = (value, name) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${name} must be an object`);
  return value;
};
const strings = (value, name) => Object.fromEntries(Object.entries(record(value, name)).map(([key, item]) => [string(key, name, true), string(item, name)]));
const boolean = (value, name) => {
  if (typeof value !== 'boolean') throw new Error(`${name} must be a boolean`);
  return value;
};

/** Validate even programmatic callers, snapshot transport config, and reject silent template use. */
export function normalizeConfig(input) {
  record(input, 'config');
  const allowed = new Set(['transport', 'serverName', 'command', 'args', 'env', 'cwd', 'url', 'headers', 'expandTemplates', 'toolCallTimeoutMs', 'startupTimeoutMs', 'closeTimeoutMs', 'reconnect', 'failOnStartupError']);
  for (const key of Object.keys(input)) if (!allowed.has(key)) throw new Error(`Unsupported config option: ${key}`);
  const serverName = string(input.serverName, 'serverName', true);
  if (!serverPattern.test(serverName)) throw new Error('serverName must match [A-Za-z0-9_-]{1,32}');
  if (input.expandTemplates !== undefined && input.expandTemplates !== false) throw new Error('Only static config is supported; expandTemplates must be false');
  const reconnectInput = record(input.reconnect ?? {}, 'reconnect');
  for (const key of Object.keys(reconnectInput)) if (!Object.hasOwn(reconnectDefaults, key)) throw new Error(`Unsupported reconnect option: ${key}`);
  const reconnect = { ...reconnectDefaults, ...reconnectInput };
  boolean(reconnect.enabled, 'reconnect.enabled');
  delay(reconnect.initialDelayMs, 'reconnect.initialDelayMs');
  delay(reconnect.maxDelayMs, 'reconnect.maxDelayMs');
  if (reconnect.initialDelayMs > reconnect.maxDelayMs) throw new Error('reconnect.initialDelayMs must be <= reconnect.maxDelayMs');
  if (!Number.isSafeInteger(reconnect.maxAttempts) || reconnect.maxAttempts < 1) throw new Error('reconnect.maxAttempts must be a positive safe integer');
  const config = {
    transport: input.transport, serverName, expandTemplates: false,
    toolCallTimeoutMs: delay(input.toolCallTimeoutMs ?? 60000, 'toolCallTimeoutMs'),
    startupTimeoutMs: delay(input.startupTimeoutMs ?? 15000, 'startupTimeoutMs'),
    closeTimeoutMs: delay(input.closeTimeoutMs ?? 5000, 'closeTimeoutMs'),
    failOnStartupError: boolean(input.failOnStartupError ?? true, 'failOnStartupError'),
    reconnect: Object.freeze(reconnect),
  };
  if (input.transport === 'stdio') {
    if (input.url !== undefined || input.headers !== undefined) throw new Error('stdio config does not accept url/headers');
    const args = input.args ?? [];
    if (!Array.isArray(args)) throw new Error('args must be an array');
    Object.assign(config, {
      command: string(input.command, 'command', true),
      args: Object.freeze(args.map(arg => string(arg, 'args'))),
      env: Object.freeze(strings(input.env ?? {}, 'env')),
      cwd: string(input.cwd ?? '', 'cwd'),
    });
  } else if (input.transport === 'streamable-http') {
    if (['command', 'args', 'env', 'cwd'].some(key => input[key] !== undefined)) throw new Error('HTTP config does not accept command/args/env/cwd');
    const url = string(input.url, 'url', true);
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error('url must be an absolute HTTP(S) URL'); }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) throw new Error('url must use HTTP(S), without userinfo or fragment');
    Object.assign(config, { url, headers: Object.freeze(strings(input.headers ?? {}, 'headers')) });
  } else {
    throw new Error('transport must be stdio or streamable-http');
  }
  return Object.freeze(config);
}

const milliseconds = defaultValue => z.number().step(1).min(1).max(MAX_DELAY).default(defaultValue);
const common = () => ({
  serverName: z.string().required().pattern(serverPattern),
  expandTemplates: z.const(false).default(false),
  toolCallTimeoutMs: milliseconds(60000),
  startupTimeoutMs: milliseconds(15000),
  closeTimeoutMs: milliseconds(5000),
  failOnStartupError: z.boolean().default(true),
  reconnect: z.object({
    enabled: z.boolean().default(true),
    initialDelayMs: milliseconds(500),
    maxDelayMs: milliseconds(30000),
    maxAttempts: z.number().step(1).min(1).max(Number.MAX_SAFE_INTEGER).default(10),
  }).default({}),
});

/** A live Config schema for the plugin manager; normalizeConfig checks cross-field invariants. */
export const Config = z.union([
  z.object({ ...common(), transport: z.const('stdio').required(), command: z.string().required(), args: z.array(String).default([]), env: z.dict(String).default({}), cwd: z.string().default('') }),
  z.object({ ...common(), transport: z.const('streamable-http').required(), url: z.string().required(), headers: z.dict(String).default({}) }),
]);
