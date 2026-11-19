// One checked native operation per Windows session-protection call.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Startup configuration is a locator under the process-owner trust model.
// Hook/session data can select neither the executable nor the fixed source.
const startupSystemRoot = process.env.SystemRoot;
const startupHome = os.homedir();
const script = fileURLToPath(new URL("./windows-session-protection.ps1", import.meta.url));
const operationTimeoutMs = 2000;
const cleanupReserveMs = 500;
const unavailable = () => new Error("Session protection state could not be confirmed");

export function windowsSessionProtection(op, args) {
  if (process.platform !== "win32" || !startupSystemRoot || !path.isAbsolute(startupSystemRoot)) throw unavailable();
  if (!["create", "inspect", "protect", "bind", "find"].includes(op)) throw unavailable();
  if (!args || typeof args.base !== "string" || !path.isAbsolute(args.base) || args.base.length > 2048)
    throw unavailable();
  if (["inspect", "protect", "bind"].includes(op) && !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(args.id)) {
    throw unavailable();
  }
  if (["bind", "find"].includes(op) && !/^[0-9a-f]{64}$/.test(args.key)) throw unavailable();
  const request = JSON.stringify({ ...args, op });
  if (Buffer.byteLength(request, "utf8") > 4096) throw unavailable();
  const system32 = path.join(startupSystemRoot, "System32");
  const powershellHome = path.join(system32, "WindowsPowerShell", "v1.0");
  const compilationId = crypto.randomBytes(16).toString("hex");
  const deadline = performance.now() + operationTimeoutMs;
  const executable = path.join(powershellHome, "powershell.exe");
  const arguments_ = ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", script];
  const options = {
    encoding: "utf8",
    windowsHide: true,
    cwd: system32,
    maxBuffer: 1024,
    env: {
      SystemRoot: startupSystemRoot,
      WINDIR: startupSystemRoot,
      PATH: system32,
      PSModulePath: path.join(powershellHome, "Modules"),
      LOOM_PRIVATE_COMPILER_BASE: path.join(startupHome, ".loom", "session-protection-compiler"),
      LOOM_PRIVATE_COMPILATION_ID: compilationId,
      LOOM_PRIVATE_DEADLINE_UTC: String(Date.now() + operationTimeoutMs - cleanupReserveMs),
    },
  };
  const result = spawnSync(executable, arguments_, {
    ...options,
    input: request,
    timeout: operationTimeoutMs - cleanupReserveMs,
  });
  if (result.error || result.signal) {
    // Cleanup has its own exact invocation receipt, no compiler and no retry
    // of the failed session operation. It shares the original overall budget.
    const remaining = Math.floor(deadline - performance.now());
    let cleanupConfirmed = false;
    if (remaining > 0) {
      const cleanup = spawnSync(executable, [...arguments_, "-CleanupCompilation"], {
        ...options,
        env: { ...options.env, LOOM_PRIVATE_DEADLINE_UTC: String(Date.now() + remaining) },
        input: "",
        timeout: remaining,
      });
      if (!cleanup.error && !cleanup.signal && cleanup.status === 0 && !cleanup.stderr) {
        try {
          const response = JSON.parse(cleanup.stdout.replace(/^\uFEFF/, ""));
          cleanupConfirmed =
            response?.ok === true && response.value === null && Object.keys(response).sort().join(",") === "ok,value";
        } catch {
          cleanupConfirmed = false;
        }
      }
    }
    const error = unavailable();
    error.cleanupPending = !cleanupConfirmed;
    throw error;
  }
  if (result.error || result.signal || result.status !== 0 || result.stderr) throw unavailable();
  let response;
  try {
    response = JSON.parse(result.stdout.replace(/^\uFEFF/, ""));
  } catch {
    throw unavailable();
  }
  if (!response || Object.keys(response).sort().join(",") !== "ok,value" || response.ok !== true) throw unavailable();
  const value = response.value;
  if (op === "create" && (typeof value !== "string" || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)))
    throw unavailable();
  if (op === "inspect" && typeof value !== "boolean") throw unavailable();
  if (["protect", "bind"].includes(op) && value !== null) throw unavailable();
  if (
    op === "find" &&
    value !== null &&
    (typeof value !== "string" || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value))
  )
    throw unavailable();
  return value;
}
