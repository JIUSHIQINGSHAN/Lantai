import assert from 'node:assert/strict';
import test from 'node:test';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Context } from '@deepseek-ai/cordis';
import promptPlugin from '@deepseek-ai/dsh-system-prompt';
import toolsPlugin from '@deepseek-ai/dsh-tools';
import { startBridge, attributionMeta, normalizeConfig, publicToolName } from '../bridge.js';

// A real local JSON-RPC server. The tested client, HTTP transport, validators,
// public DSH adapter, registry, cancellation, and serialization are never mocked.
async function localServer() {
  const calls = [];
  const cancellations = [];
  const waiters = new Map();
  const sockets = new Set();
  let toolNames = ['echo', 'wait', 'fail', 'invalid'];
  let pages;
  const listRequests = [];
  const schema = { type: 'object', properties: { label: { type: 'string' } }, required: ['label'], additionalProperties: true };
  const server = http.createServer(async (req, res) => {
    if (req.method !== 'POST') { res.writeHead(405).end(); return; }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const message = JSON.parse(raw);
    if (message.method === 'notifications/cancelled') {
      cancellations.push(message.params);
      const pending = waiters.get(message.params.requestId);
      pending?.();
      res.writeHead(202).end();
      return;
    }
    if (message.id === undefined) { res.writeHead(202).end(); return; }
    const reply = result => {
      if (res.destroyed) return;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }));
    };
    if (message.method === 'initialize') {
      reply({ protocolVersion: '2025-03-26', capabilities: { tools: { listChanged: true } }, serverInfo: { name: 'local-attribution-smoke', version: '1' } });
    } else if (message.method === 'tools/list') {
      listRequests.push(message.params?.cursor);
      const pageIndex = Number(message.params?.cursor ?? 0);
      const page = pages?.[pageIndex];
      if (page?.delayMs) await new Promise(resolve => setTimeout(resolve, page.delayMs));
      if (page?.error) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: 'discovery failed' } }));
      } else {
        const names = page?.names ?? toolNames;
        const nextCursor = page?.nextCursor ?? (pages && pageIndex + 1 < pages.length ? String(pageIndex + 1) : undefined);
        reply({ tools: names.map(name => ({ name, description: name, inputSchema: schema,
          ...(name === 'invalid' ? { outputSchema: { type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'], additionalProperties: false } } : {}) })),
          ...(nextCursor === undefined ? {} : { nextCursor }) });
      }
    } else if (message.method === 'tools/call') {
      calls.push(message);
      const { name, arguments: args } = message.params;
      if (name === 'wait') {
        waiters.set(message.id, () => { waiters.delete(message.id); reply({ content: [{ type: 'text', text: 'cancelled' }] }); });
      } else if (name === 'fail') {
        reply({ isError: true, content: [{ type: 'text', text: 'server failed with smoke-secret-value' }] });
      } else if (name === 'invalid') {
        reply({ content: [{ type: 'text', text: 'invalid output' }], structuredContent: { ok: 'not a boolean' } });
      } else {
        // Delay A so the B request is on the wire simultaneously.
        setTimeout(() => reply({ content: [{ type: 'text', text: JSON.stringify(message.params) }], structuredContent: { label: args.label } }), args.label === 'A' ? 25 : 1);
      }
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'Method not found' } }));
    }
  });
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {
    calls, cancellations, waiters, listRequests,
    url: `http://127.0.0.1:${server.address().port}/mcp`,
    setTools(names) { toolNames = names; pages = undefined; },
    setPages(value) { pages = value; },
    async close() {
      for (const finish of waiters.values()) finish();
      for (const socket of sockets) socket.destroy();
      await new Promise(resolve => server.close(resolve));
    },
  };
}

async function registry() {
  const ctx = new Context();
  await ctx.plugin(promptPlugin);
  await ctx.plugin(toolsPlugin);
  return ctx;
}

function execution(agentId, callId, signal = new AbortController().signal) {
  return Object.freeze({ callId, rootCallId: callId, name: 'mcp__lantai__echo', token: Symbol(callId),
    ...(agentId === undefined ? {} : { agent: Object.freeze({ id: agentId }) }), signal });
}

async function until(condition) {
  const deadline = Date.now() + 2000;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('local smoke condition timed out');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

test('review bundle is disabled by default and carries no installation path', async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const manifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
  const patch = await fs.readFile(path.join(root, 'cordis.patch.yml'), 'utf8');
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
  assert.equal(manifest.engines.dsh, '0.2.2-rc.3');
  assert.match(patch, /id: lantai-attribution/);
  assert.match(patch, /disabled: true/);
  assert.doesNotMatch(patch, /D:\\\\|app\\.asar|DSH_INSTALL_DIR/);
});

test('attribution v1 uses exact invocation fields and omits absent identity', () => {
  assert.deepEqual(attributionMeta(execution('session-A', 'call-A')), { 'io.lantai/attribution': { version: 1, request_session_id: 'session-A', request_id: 'call-A' } });
  assert.deepEqual(attributionMeta(execution(undefined, 'agentless')), { 'io.lantai/attribution': { version: 1, request_id: 'agentless' } });
});

test('static existing config shape, validation, and stable public names', () => {
  const config = normalizeConfig({ serverName: 'lantai', transport: 'stdio', command: 'python', args: ['bridge.py'], env: {}, cwd: '', expandTemplates: false });
  assert.equal(config.toolCallTimeoutMs, 60000);
  assert.equal(config.reconnect.maxAttempts, 10);
  assert.throws(() => normalizeConfig({ ...config, expandTemplates: true }), /static/i);
  assert.throws(() => normalizeConfig({ ...config, args: ['${credential:some-ref}'] }), /static/i);
  assert.throws(() => normalizeConfig({ ...config, reconnect: { initialDelayMs: 30, maxDelayMs: 1 } }), /initialDelayMs/);
  assert.equal(publicToolName('lantai', 'add_dialogue'), 'mcp__lantai__add_dialogue');
  assert.equal(publicToolName('lantai', 'bad.name'), 'mcp__lantai__bad_name_db0cb3a60c89');
});

test('paginated discovery registers the whole list and preserves it on incomplete refresh', { timeout: 10000 }, async () => {
  const server = await localServer();
  const ctx = await registry();
  let bridge;
  try {
    server.setPages([{ names: ['echo'] }, { names: ['wait'] }, { names: ['fail'] }]);
    bridge = startBridge(ctx, { serverName: 'lantai', transport: 'streamable-http', url: server.url,
      toolCallTimeoutMs: 1000, startupTimeoutMs: 2000, closeTimeoutMs: 2000, reconnect: { enabled: false } });
    await bridge.ready;
    assert.deepEqual(server.listRequests, [undefined, '1', '2']);
    const echo = ctx.tools.get('mcp__lantai__echo');
    const wait = ctx.tools.get('mcp__lantai__wait');
    assert.ok(echo);
    assert.ok(wait);
    assert.ok(ctx.tools.get('mcp__lantai__fail'));
    for (const [pages, expected] of [
      [[{ names: ['replacement'] }, { names: ['echo'], error: true }], /discovery failed/i],
      [[{ names: ['replacement'] }, { names: ['replacement'] }], /duplicate/i],
      [[{ names: ['replacement'], nextCursor: '0' }], /cursor|pagination/i],
      [Array.from({ length: 101 }, () => ({ names: [] })), /limit|pagination/i],
      [Array.from({ length: 10 }, (_, i) => ({ names: [`slow-${i}`], delayMs: 150 })), /timeout|timed out/i],
    ]) {
      server.setPages(pages);
      await assert.rejects(bridge.sync(), expected);
      assert.equal(ctx.tools.get('mcp__lantai__echo'), echo);
      assert.equal(ctx.tools.get('mcp__lantai__wait'), wait);
      assert.equal(ctx.tools.get('mcp__lantai__replacement'), undefined);
    }
    server.setPages([{ names: ['replacement'] }, { names: ['echo'] }]);
    await bridge.sync();
    assert.ok(ctx.tools.get('mcp__lantai__replacement'));
    assert.equal(ctx.tools.get('mcp__lantai__wait'), undefined);
  } finally {
    await bridge?.dispose();
    await ctx.fiber.dispose();
    await server.close();
  }
});

test('real SDK HTTP + DSH registry: concurrent A/B, agentless, errors, schemas, cancellation, timeout and teardown', { timeout: 10000 }, async t => {
  const server = await localServer();
  t.after(() => server.close());
  const ctx = await registry();
  t.after(() => ctx.fiber.dispose());
  let bridge;
  try {
    bridge = startBridge(ctx, { serverName: 'lantai', transport: 'streamable-http', url: server.url,
      headers: { Authorization: 'Bearer smoke-secret-value' }, toolCallTimeoutMs: 100, startupTimeoutMs: 2000, closeTimeoutMs: 2000,
      reconnect: { enabled: false }, failOnStartupError: true });
    await bridge.ready;
    const echo = ctx.tools.get('mcp__lantai__echo');
    assert.ok(echo);
    const argsA = Object.freeze({ label: 'A', session_id: 'explicit-origin', _meta: Object.freeze({ 'io.lantai/attribution': 'untrusted-body-value' }) });
    const argsB = Object.freeze({ label: 'B' });
    const values = await Promise.all([
      echo.execute(argsA, execution('session-A', 'call-A')),
      echo.execute(argsB, execution('session-B', 'call-B')),
      echo.execute(Object.freeze({ label: 'none' }), execution(undefined, 'call-none')),
    ]);
    assert.equal(values[0].structuredContent.label, 'A');
    for (const [label, session, call] of [['A', 'session-A', 'call-A'], ['B', 'session-B', 'call-B'], ['none', undefined, 'call-none']]) {
      const wire = server.calls.find(message => message.params.arguments.label === label);
      assert.deepEqual(wire.params._meta['io.lantai/attribution'], { version: 1, ...(session === undefined ? {} : { request_session_id: session }), request_id: call });
    }
    const wireA = server.calls.find(message => message.params.arguments.label === 'A');
    assert.equal(wireA.params.arguments.session_id, 'explicit-origin');
    assert.deepEqual(wireA.params.arguments._meta, argsA._meta);
    assert.deepEqual(argsB, { label: 'B' });
    assert.deepEqual(echo.parameters, { type: 'object', properties: { label: { type: 'string' } }, required: ['label'], additionalProperties: true });
    const output = echo.output.render(argsA, values[0]);
    assert.match(output[0].text, /explicit-origin/);
    await assert.rejects(ctx.tools.get('mcp__lantai__fail').execute({ label: 'err' }, execution('session-A', 'call-error')), error => !error.message.includes('smoke-secret-value') && error.message.includes('[redacted]'));
    await assert.rejects(ctx.tools.get('mcp__lantai__invalid').execute({ label: 'schema' }, execution('session-A', 'call-schema')), /schema|structured|valid/i);
    const controller = new AbortController();
    const cancelled = ctx.tools.get('mcp__lantai__wait').execute({ label: 'cancel' }, execution('session-A', 'call-cancel', controller.signal));
    await until(() => server.calls.some(message => message.params.arguments.label === 'cancel'));
    controller.abort(new Error('caller cancelled'));
    await assert.rejects(cancelled, /cancel|abort/i);
    await until(() => server.cancellations.length >= 1);
    await assert.rejects(ctx.tools.get('mcp__lantai__wait').execute({ label: 'timeout' }, execution('session-B', 'call-timeout')), /timeout|timed out/i);
    await until(() => server.cancellations.length >= 2);
    // A failed list-generation build must retain the previously registered tools.
    server.setTools(['echo', 'echo']);
    await assert.rejects(bridge.sync(), /duplicate|more than once/i);
    assert.equal(ctx.tools.get('mcp__lantai__echo'), echo);
    server.setTools(['echo']);
    await bridge.sync();
    assert.equal(ctx.tools.get('mcp__lantai__wait'), undefined);
    await bridge.dispose();
    assert.equal(ctx.tools.get('mcp__lantai__echo'), undefined);
    await bridge.dispose();
  } finally {
    await bridge?.dispose();
    await ctx.fiber.dispose();
    await server.close();
  }
});
