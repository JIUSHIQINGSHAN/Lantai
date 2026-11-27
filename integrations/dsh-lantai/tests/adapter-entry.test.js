// Real installed registry/SDK -> generated stdio entry -> isolated SQLite.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { Context } from '@deepseek-ai/cordis';
import promptPlugin from '@deepseek-ai/dsh-system-prompt';
import toolsPlugin from '@deepseek-ai/dsh-tools';
import { startBridge } from '../bridge.js';

const config = JSON.parse(await fs.readFile(process.argv[3], 'utf8'));
const ctx = new Context();
let bridge;
try {
  await ctx.plugin(promptPlugin);
  await ctx.plugin(toolsPlugin);
  bridge = startBridge(ctx, config);
  await bridge.ready;
  const core = ['add', 'core_memory_get', 'feedback', 'mem_recent', 'offload_read', 'search'];
  for (const name of core) assert.ok(ctx.tools.get(`mcp__lantai__${name}`), name);
  assert.equal(ctx.tools.get('mcp__lantai__core_memory_put'), undefined);
  const args = Object.freeze({ limit: 1 });
  const execution = Object.freeze({
    name: 'mcp__lantai__mem_recent', callId: 'adapter-call', rootCallId: 'adapter-call',
    token: Symbol('adapter-call'), signal: new AbortController().signal,
    agent: Object.freeze({ id: 'adapter-session' }),
  });
  const result = await ctx.tools.get(execution.name).execute(args, execution);
  const payload = JSON.parse(result.content.find(block => block.type === 'text').text);
  assert.deepEqual(payload.memories, []);
  assert.equal(payload.total, 0);
  assert.deepEqual(args, { limit: 1 });
} finally {
  await bridge?.dispose();
  try {
    for (const name of ['search', 'mem_recent']) assert.equal(ctx.tools.get(`mcp__lantai__${name}`), undefined);
  } finally {
    await ctx.fiber.dispose();
  }
}
console.log('adapter-entry: verified');
