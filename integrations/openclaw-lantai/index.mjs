import { statSync } from "node:fs";
import { isAbsolute } from "node:path";
import { createBridge } from "./bridge.mjs";

const KEYS = new Set(["python", "shellHook", "userId", "agentId", "channels", "sessionKeys", "captureDialogue"]);
const MAX_PENDING = 128;
const PENDING_TTL_MS = 5 * 60 * 1000;
const MAX_QUERY_CHARS = 20000;
const validId = (value) => typeof value === "string" && value.length > 0
  && value.length <= 128 && value === value.trim() && !/[\x00-\x1f\x7f-\x9f*]/u.test(value);

export function validateConfig(raw) {
  const fail = () => { throw new Error("Invalid Lantai configuration: check paths and explicit scope allowlists"); };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail();
  if (Object.keys(raw).some((key) => !KEYS.has(key))) fail();
  for (const key of ["python", "shellHook"]) {
    try {
      if (typeof raw[key] !== "string" || !isAbsolute(raw[key]) || !statSync(raw[key]).isFile()) fail();
    } catch { fail(); }
  }
  for (const key of ["userId", "agentId"]) if (!validId(raw[key])) fail();
  for (const key of ["channels", "sessionKeys"]) {
    if (!Array.isArray(raw[key]) || !raw[key].length || raw[key].length > 128
        || !raw[key].every(validId) || new Set(raw[key]).size !== raw[key].length) fail();
  }
  if (raw.captureDialogue !== undefined && typeof raw.captureDialogue !== "boolean") fail();
  // The frozen dialogue action writes as "default"; never imply a scoped write it cannot make.
  if (raw.captureDialogue && raw.userId !== "default") fail();
  return Object.freeze({
    ...raw, captureDialogue: raw.captureDialogue ?? false,
    channels: Object.freeze([...raw.channels]), sessionKeys: Object.freeze([...raw.sessionKeys]),
  });
}

function eligible(config, ctx) {
  return ctx && ctx.agentId === config.agentId && config.channels.includes(ctx.channel)
    && config.sessionKeys.includes(ctx.sessionKey) && validId(ctx.sessionId) && validId(ctx.runId)
    && (ctx.trigger === undefined || ["user", "message"].includes(ctx.trigger))
    && (ctx.inputProvenance === undefined || ctx.inputProvenance?.kind === "external_user")
    && !/:subagent:|:cron:/u.test(ctx.sessionKey);
}

function register(api) {
  const config = validateConfig(api.pluginConfig);
  if (typeof api.on !== "function" || typeof api.registerService !== "function") {
    throw new Error("Lantai requires OpenClaw typed hooks and service lifecycle support");
  }
  const bridge = createBridge(config);
  const pending = new Map();
  const turns = new Map();
  let stopped = false;
  const warn = () => api.logger?.warn?.("Lantai memory bridge unavailable; this operation was skipped");
  const keyFor = (ctx) => JSON.stringify([ctx.sessionKey, ctx.sessionId, ctx.runId]);

  async function sendControl(frame) {
    try {
      const result = await bridge.request(frame);
      if (result.ok !== true || (frame.type === "backfill" && result.receipt_status !== "acked")) warn();
    } catch { warn(); }
  }

  function prune() {
    const cutoff = Date.now() - PENDING_TTL_MS;
    for (const [key, value] of pending) if (value.created < cutoff) pending.delete(key);
  }

  api.on("before_prompt_build", async (event, ctx) => {
    if (stopped || !eligible(config, ctx)) return;
    const original = event?.currentUserMessage;
    const query = original === undefined ? event?.prompt : original;
    if (typeof query !== "string" || !query.trim() || query.length > MAX_QUERY_CHARS) return;
    prune();
    const key = keyFor(ctx);
    const existing = pending.get(key);
    if (existing) {
      if (existing.query !== query) existing.invalid = true;
      if (!existing.finalized && !existing.invalid) return existing.response;
      return;
    }
    while (pending.size >= MAX_PENDING) pending.delete(pending.keys().next().value);
    const session = JSON.stringify([ctx.sessionKey, ctx.sessionId]);
    const turn = (turns.get(session) ?? 0) + 1;
    turns.set(session, turn);
    if (turns.size > MAX_PENDING) turns.delete(turns.keys().next().value);
    const entry = { created: Date.now(), query, original, turn, finalized: false, invalid: false };
    pending.set(key, entry);
    entry.response = (async () => {
      try {
        const result = await bridge.request({ query, session_id: ctx.sessionId });
        if (stopped || entry.invalid || entry.finalized || pending.get(key) !== entry) return;
        if (typeof result.context !== "string" || !result.context.trim()) return;
        ctx.hookInvocation?.assertActive();
        entry.eventId = validId(result.event_id) ? result.event_id : undefined;
        entry.usedIds = Array.isArray(result.evidence)
          ? [...new Set(result.evidence.map((e) => e?.id).filter(validId))] : [];
        return { prependContext: result.context };
      } catch { warn(); }
    })();
    return entry.response;
  });

  api.on("agent_end", async (event, ctx) => {
    const resolved = ctx && { ...ctx, runId: ctx.runId ?? event?.runId };
    if (stopped || !eligible(config, resolved)) return;
    const entry = pending.get(keyFor(resolved));
    if (!entry || entry.finalized) return;
    entry.finalized = true;
    await entry.response;
    if (event?.success !== true || entry.invalid || stopped
        || entry.created < Date.now() - PENDING_TTL_MS) return;
    if (entry.eventId && entry.usedIds?.length) {
      await sendControl({ type: "backfill", event_id: entry.eventId, used_ids: entry.usedIds });
    }
    // Only the host's separate current-user field is safe to capture, never history or projection.
    if (!stopped && config.captureDialogue && typeof entry.original === "string" && entry.original.trim()) {
      await sendControl({
        type: "dialogue", text: entry.original, session_id: resolved.sessionId, turn: entry.turn,
      });
    }
  });

  api.registerService({
    id: "lantai-memory-bridge",
    start() {},
    async stop() {
      stopped = true;
      pending.clear();
      turns.clear();
      await bridge.close();
    },
  });
}

export default { id: "lantai-memory", name: "Lantai memory bridge", register };
