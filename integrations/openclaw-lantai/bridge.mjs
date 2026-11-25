import { spawn } from "node:child_process";
import { dirname } from "node:path";

const MAX_FRAME_BYTES = 1024 * 1024;
const MAX_QUEUED = 32;

// One ordered stream per plugin instance. Failed writes are never replayed.
export function createBridge(config, { requestTimeoutMs = 20000 } = {}) {
  let child;
  let pending;
  let closed = false;
  let queued = 0;
  let tail = Promise.resolve();
  const children = new Map();

  function discard(proc, error) {
    if (child === proc) child = undefined;
    if (pending?.proc === proc) {
      const entry = pending;
      pending = undefined;
      clearTimeout(entry.timer);
      entry.reject(error);
    }
    if (proc.exitCode === null) proc.kill();
  }

  function start() {
    if (child) return child;
    const env = { ...process.env };
    delete env.PYTHONPATH;
    delete env.PYTHONHOME;
    Object.assign(env, {
      LANTAI_HOST: "openclaw",
      LANTAI_HOOK_USER_ID: config.userId,
      LANTAI_HOOK_ROLE: "user",
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      SHELL_HOOK_TIMEOUT: "8",
      SHELL_HOOK_DIALOGUE_TIMEOUT: "30",
    });
    const proc = spawn(config.python, [config.shellHook, "--serve"], {
      cwd: dirname(config.shellHook), env, shell: false, windowsHide: true,
      stdio: ["pipe", "pipe", "ignore"],
    });
    child = proc;
    children.set(proc, new Promise((resolve) => proc.once("close", resolve)));
    let buffer = Buffer.alloc(0);
    proc.stdout.on("data", (chunk) => {
      if (child !== proc) return;
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > MAX_FRAME_BYTES || !pending || pending.proc !== proc) {
        discard(proc, new Error("Lantai bridge response exceeded its boundary"));
        return;
      }
      const newline = buffer.indexOf(10);
      if (newline < 0) return;
      try {
        const value = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
        if (!value || typeof value !== "object" || Array.isArray(value)
            || buffer.subarray(newline + 1).toString("utf8").trim()) {
          throw new Error("Invalid response");
        }
        buffer = Buffer.alloc(0);
        const entry = pending;
        pending = undefined;
        clearTimeout(entry.timer);
        entry.resolve(value);
      } catch {
        discard(proc, new Error("Lantai bridge returned an invalid frame"));
      }
    });
    proc.stdin.on("error", () => discard(proc, new Error("Lantai bridge input closed")));
    proc.once("error", () => discard(proc, new Error("Lantai bridge failed to start")));
    proc.once("close", () => {
      discard(proc, new Error("Lantai bridge exited"));
      children.delete(proc);
    });
    return proc;
  }

  function exchange(frame, wire) {
    if (closed) return Promise.reject(new Error("Lantai bridge is closed"));
    const proc = start();
    return new Promise((resolve, reject) => {
      const timeout = frame.type === "dialogue" ? Math.max(requestTimeoutMs, 35000) : requestTimeoutMs;
      const timer = setTimeout(() => discard(proc, new Error("Lantai bridge timed out")), timeout);
      pending = { proc, resolve, reject, timer };
      proc.stdin.write(wire, "utf8", (error) => {
        if (error) discard(proc, new Error("Lantai bridge write failed"));
      });
    });
  }

  return {
    request(frame) {
      if (closed || queued >= MAX_QUEUED) {
        return Promise.reject(new Error("Lantai bridge is closed or busy"));
      }
      let wire;
      try {
        if (!frame || typeof frame !== "object" || Array.isArray(frame)) throw new Error();
        wire = JSON.stringify(frame) + "\n";
        if (Buffer.byteLength(wire) > MAX_FRAME_BYTES) throw new Error();
      } catch {
        return Promise.reject(new Error("Lantai bridge request is invalid"));
      }
      queued++;
      const operation = tail.then(() => exchange(frame, wire));
      tail = operation.then(() => { queued--; }, () => { queued--; });
      return operation;
    },
    async close() {
      closed = true;
      const waits = [...children.values()];
      for (const proc of children.keys()) discard(proc, new Error("Lantai bridge is closed"));
      await Promise.all(waits);
      await tail;
    },
  };
}
