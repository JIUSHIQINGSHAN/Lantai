import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";
import plugin, { validateConfig } from "../integrations/openclaw-lantai/index.mjs";
import { createBridge } from "../integrations/openclaw-lantai/bridge.mjs";

const fixtureKeys = ["LANTAI_TEST_PYTHON", "LANTAI_TEST_HOOK", "LANTAI_ADAPTER_TEST_LOG"];
const supplied = fixtureKeys.filter((key) => process.env[key] !== undefined);
assert.ok(supplied.length === 0 || supplied.length === 3, "Provide all three fixture overrides together");
if (supplied.length === 0) {
  const python = spawnSync("python", ["-c", "import sys; print(sys.executable)"], {
    encoding: "utf8", windowsHide: true, timeout: 10000,
  });
  assert.equal(python.status, 0, "Python is required for the real NDJSON peer");
  const scratch = fileURLToPath(new URL("../.scratch/", import.meta.url));
  mkdirSync(scratch, { recursive: true });
  const fixture = mkdtempSync(join(scratch, "openclaw-node-"));
  process.env.LANTAI_TEST_PYTHON = python.stdout.trim();
  process.env.LANTAI_TEST_HOOK = fileURLToPath(new URL("./support/openclaw_peer.py", import.meta.url));
  process.env.LANTAI_ADAPTER_TEST_LOG = join(fixture, "frames.jsonl");
  after(() => {
    assert.equal(dirname(fixture), resolve(scratch));
    rmSync(fixture, { recursive: true, force: true });
    for (const key of fixtureKeys) delete process.env[key];
  });
}

const config = (overrides = {}) => ({
  python: process.env.LANTAI_TEST_PYTHON,
  shellHook: process.env.LANTAI_TEST_HOOK,
  userId: "owner-test",
  agentId: "main",
  channels: ["webchat"],
  sessionKeys: ["agent:main:main"],
  ...overrides,
});
const ctx = (overrides = {}) => ({
  agentId: "main", channel: "webchat", sessionKey: "agent:main:main",
  sessionId: "session-1", runId: "run-1", ...overrides,
});
const frames = () => {
  try { return readFileSync(process.env.LANTAI_ADAPTER_TEST_LOG, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse); }
  catch (error) { if (error.code === "ENOENT") return []; throw error; }
};
function host(overrides = {}) {
  const hooks = new Map();
  const warnings = [];
  let service;
  plugin.register({
    pluginConfig: config(overrides),
    on(name, callback) { assert.equal(hooks.has(name), false); hooks.set(name, callback); },
    registerService(value) { service = value; },
    logger: { warn(message) { warnings.push(message); } },
  });
  return { hooks, warnings, close: () => service.stop() };
}

test("native entry registers prompt and terminal channels", async () => {
  const h = host();
  assert.deepEqual([...h.hooks.keys()], ["before_prompt_build", "agent_end"]);
  await h.close();
});

for (const overrides of [
  { python: "missing" }, { shellHook: "missing" }, { userId: "" },
  { userId: "a\nb" }, { channels: [] }, { sessionKeys: [] }, { agentId: "" },
  { captureDialogue: "false" }, { extra: true }, { channels: ["*"] },
  { captureDialogue: true, userId: "another-owner" },
]) {
  test(`invalid configuration rejected: ${JSON.stringify(overrides)}`, () => {
    assert.throws(() => validateConfig(config(overrides)), /configuration/i);
  });
}

test("native prompt injects fenced Unicode and defers receipt", async () => {
  const h = host();
  const before = frames().length;
  try {
    const result = await h.hooks.get("before_prompt_build")({ prompt: "部署方案" }, ctx());
    assert.deepEqual(result, { prependContext: "<memory>部署方案</memory>" });
    assert.deepEqual(frames().slice(before), [{ query: "部署方案", session_id: "session-1" }]);
    await h.hooks.get("agent_end")({ success: true }, ctx());
    await h.hooks.get("agent_end")({ success: true }, ctx());
    assert.deepEqual(frames().slice(before + 1), [
      { type: "backfill", event_id: "rev_session-1", used_ids: ["mem_1"] },
    ]);
  } finally { await h.close(); }
});

test("explicit dialogue capture writes only current original user text", async () => {
  const h = host({ captureDialogue: true, userId: "default" });
  const before = frames().length;
  try {
    await h.hooks.get("before_prompt_build")({ prompt: "history-plus-prompt", currentUserMessage: "记住原话" }, ctx());
    await h.hooks.get("agent_end")({ success: true, messages: [{ role: "user", content: "history" }] }, ctx());
    const sent = frames().slice(before);
    assert.equal(sent[0].query, "记住原话");
    assert.deepEqual(sent.filter((f) => f.type === "dialogue"), [
      { type: "dialogue", text: "记住原话", session_id: "session-1", turn: 1 },
    ]);
  } finally { await h.close(); }
});

test("failed run and legacy composite prompt never become dialogue", async () => {
  const h = host({ captureDialogue: true, userId: "default" });
  const before = frames().length;
  try {
    await h.hooks.get("before_prompt_build")({ prompt: "legacy-prompt" }, ctx());
    await h.hooks.get("agent_end")({ success: false }, ctx());
    await h.hooks.get("before_prompt_build")({ prompt: "legacy-prompt" }, ctx({ runId: "run-2" }));
    await h.hooks.get("agent_end")({ success: true }, ctx({ runId: "run-2" }));
    assert.equal(frames().slice(before).some((f) => f.type === "dialogue"), false);
    assert.equal(frames().slice(before).filter((f) => f.type === "backfill").length, 1);
  } finally { await h.close(); }
});

for (const overrides of [
  { agentId: "other" }, { channel: "discord" }, { sessionKey: "agent:main:group" },
  { sessionId: "" }, { sessionId: "bad\nidentity" }, { trigger: "cron" },
  { trigger: "subagent" }, { sessionKey: "agent:main:subagent:child" }, { runId: "" },
  { inputProvenance: { kind: "inter_session" } },
  { inputProvenance: { kind: "internal_system" } },
]) {
  test(`out-of-scope run performs no IO: ${JSON.stringify(overrides)}`, async () => {
    const h = host({ captureDialogue: true, userId: "default" });
    const before = frames().length;
    try {
      assert.equal(await h.hooks.get("before_prompt_build")({ prompt: "private" }, ctx(overrides)), undefined);
      await h.hooks.get("agent_end")({ success: true }, ctx(overrides));
      assert.equal(frames().length, before);
    } finally { await h.close(); }
  });
}

test("parallel rebuild is deduplicated and terminal runs stay separate", async () => {
  const h = host({ captureDialogue: true, userId: "default" });
  const before = frames().length;
  try {
    const prompt = h.hooks.get("before_prompt_build");
    const end = h.hooks.get("agent_end");
    await Promise.all([
      prompt({ prompt: "one", currentUserMessage: "one" }, ctx()),
      prompt({ prompt: "one", currentUserMessage: "one" }, ctx()),
      prompt({ prompt: "two", currentUserMessage: "two" }, ctx({ runId: "run-2" })),
    ]);
    await end({ success: true }, ctx({ runId: "run-2" }));
    await end({ success: false }, ctx());
    const sent = frames().slice(before);
    assert.equal(sent.filter((f) => !f.type).length, 2);
    assert.deepEqual(sent.filter((f) => f.type === "dialogue"), [
      { type: "dialogue", text: "two", session_id: "session-1", turn: 2 },
    ]);
  } finally { await h.close(); }
});

test("bridge owns host and configured principal without mutating parent", async () => {
  const before = process.env.LANTAI_HOST;
  process.env.LANTAI_HOST = "codex";
  const b = createBridge(validateConfig(config()));
  try {
    const result = await b.request({ query: "部署" });
    assert.equal(result.host, "openclaw");
    assert.equal(result.owner, "owner-test");
    assert.equal(result.role, "user");
    assert.equal(process.env.LANTAI_HOST, "codex");
  } finally {
    await b.close();
    if (before === undefined) delete process.env.LANTAI_HOST;
    else process.env.LANTAI_HOST = before;
  }
});

test("bridge serializes simultaneous frames", async () => {
  const b = createBridge(validateConfig(config()));
  try {
    const results = await Promise.all(["一", "二", "三"].map((query) => b.request({ query })));
    assert.deepEqual(results.map((r) => r.echo.query), ["一", "二", "三"]);
  } finally { await b.close(); }
});

for (const [query, expected] of [
  ["timeout", /timed out/], ["malformed", /invalid frame/], ["array", /invalid frame/],
  ["oversize", /exceeded its boundary/], ["exit", /exited/],
]) {
  test(`bridge isolates bad exchange and recovers: ${query}`, async () => {
    const b = createBridge(validateConfig(config()), query === "timeout" ? { requestTimeoutMs: 5000 } : {});
    try {
      await assert.rejects(b.request({ query }), expected);
      assert.equal((await b.request({ query: "recovered" })).echo.query, "recovered");
    } finally { await b.close(); }
  });
}

test("bridge close refuses later work", async () => {
  const b = createBridge(validateConfig(config()));
  await b.close();
  await assert.rejects(b.request({ query: "closed" }));
});

test("oversized request is rejected before child I/O and the bridge remains usable", async () => {
  const b = createBridge(validateConfig(config()));
  const before = frames().length;
  try {
    await assert.rejects(b.request({ query: "x".repeat(1024 * 1024) }), /invalid/i);
    assert.equal(frames().length, before);
    assert.equal((await b.request({ query: "after-oversize" })).echo.query, "after-oversize");
  } finally { await b.close(); }
});

test("expired prompt records are retrieved again instead of reusing stale context", async () => {
  const h = host();
  const realNow = Date.now;
  const before = frames().length;
  try {
    await h.hooks.get("before_prompt_build")({ prompt: "expiry" }, ctx());
    const later = realNow() + 301000;
    Date.now = () => later;
    await h.hooks.get("before_prompt_build")({ prompt: "expiry" }, ctx());
    assert.equal(frames().slice(before).filter((f) => !f.type).length, 2);
  } finally { Date.now = realNow; await h.close(); }
});

test("pending queue is bounded and rejects overflow without losing accepted requests", async () => {
  const b = createBridge(validateConfig(config()));
  try {
    const accepted = Array.from({ length: 32 }, (_, i) => b.request({ query: String(i) }));
    await assert.rejects(b.request({ query: "overflow" }), /busy/);
    const results = await Promise.all(accepted);
    assert.deepEqual(results.map((r) => r.echo.query), Array.from({ length: 32 }, (_, i) => String(i)));
  } finally { await b.close(); }
});

test("changed prompt under the same native run is never written or acknowledged", async () => {
  const h = host({ captureDialogue: true, userId: "default" });
  const before = frames().length;
  try {
    await h.hooks.get("before_prompt_build")({ currentUserMessage: "first" }, ctx());
    assert.equal(await h.hooks.get("before_prompt_build")({ currentUserMessage: "different" }, ctx()), undefined);
    await h.hooks.get("agent_end")({ success: true }, ctx());
    assert.deepEqual(frames().slice(before), [{ query: "first", session_id: "session-1" }]);
  } finally { await h.close(); }
});

test("receipt failure cannot drop separately enabled user dialogue", async () => {
  const h = host({ captureDialogue: true, userId: "default" });
  const before = frames().length;
  try {
    await h.hooks.get("before_prompt_build")({ currentUserMessage: "bad-ack" }, ctx());
    await h.hooks.get("agent_end")({ success: true }, ctx());
    const sent = frames().slice(before);
    assert.deepEqual(sent.map((f) => f.type ?? "query"), ["query", "backfill", "dialogue"]);
    assert.equal(sent[2].text, "bad-ack");
    assert.equal(h.warnings.length, 1);
    assert.equal(h.warnings[0].includes("bad-ack"), false);
  } finally { await h.close(); }
});

test("host-expired prompt results never produce a receipt", async () => {
  const h = host();
  const before = frames().length;
  const expired = ctx({ hookInvocation: { assertActive() { throw new Error("expired"); } } });
  try {
    assert.equal(await h.hooks.get("before_prompt_build")({ prompt: "expired-result" }, expired), undefined);
    await h.hooks.get("agent_end")({ success: true }, ctx());
    assert.deepEqual(frames().slice(before), [{ query: "expired-result", session_id: "session-1" }]);
  } finally { await h.close(); }
});
