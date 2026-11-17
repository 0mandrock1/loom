import "./temp-home.mjs";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { processIdentity, publishProject, sessionProject } from "../lib/mcp-project.mjs";

test("the native platform supplies this live process's creation identity", () => {
  const failures = [];
  const run = (...args) => {
    const started = Date.now();
    try {
      return execFileSync(...args);
    } catch (error) {
      failures.push({ code: error.code, signal: error.signal, status: error.status, elapsedMs: Date.now() - started });
      throw error;
    }
  };
  const identity = processIdentity(process.pid, { run });
  assert.equal(identity?.pid, process.pid, JSON.stringify(failures));
  assert.equal(identity?.parent, process.ppid);
  assert.ok(identity?.born);
  assert.ok(identity?.name);
  assert.deepEqual(processIdentity(process.pid), identity);
});

test("macOS and Windows process queries parse creation identities without shell interpolation", () => {
  for (const [platform, command, output, expected] of [
    [
      "darwin",
      "ps",
      "  20 Mon Nov 16 19:01:02 2026 claude\n",
      { pid: 30, parent: 20, born: "Mon Nov 16 19:01:02 2026", name: "claude" },
    ],
    [
      "win32",
      "powershell.exe",
      '\ufeff{"name":"claude.exe","parent":20,"born":"639041004620000000"}',
      { pid: 30, parent: 20, born: "639041004620000000", name: "claude.exe" },
    ],
  ]) {
    const run = (bin, args, options) => {
      assert.equal(bin, command);
      assert.equal(options.timeout, 1000);
      assert.equal(options.windowsHide, true);
      if (platform === "darwin") assert.deepEqual(args, ["-p", "30", "-o", "ppid=", "-o", "lstart=", "-o", "ucomm="]);
      else {
        assert.deepEqual(args.slice(0, 3), ["-NoProfile", "-NonInteractive", "-Command"]);
        assert.match(args[3], /ProcessId=30/);
        assert.match(
          args[3],
          /\[wmisearcher\]'SELECT Name,ParentProcessId,CreationDate FROM Win32_Process WHERE ProcessId=30'/,
        );
        assert.match(
          args[3],
          /\[System\.Management\.ManagementDateTimeConverter\]::ToDateTime\(\$p\.CreationDate\)\.ToUniversalTime\(\)\.Ticks\.ToString\(\)/,
        );
        assert.doesNotMatch(args[3], /CommandLine|Environment/);
      }
      return output;
    };
    assert.deepEqual(processIdentity(30, { platform, run }), expected);
    assert.equal(processIdentity(30, { platform, run: () => "invalid" }), null);
    assert.equal(
      processIdentity(30, {
        platform,
        run: () => {
          throw new Error("process exited");
        },
      }),
      null,
    );
    assert.equal(processIdentity("30; echo unsafe", { platform, run }), null);
  }
});

test("concurrent clients retain exact quote/dollar paths and separate sessions", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "loom-project-test-"));
  try {
    const identities = new Map([
      [10, { pid: 10, parent: 1, born: "first" }],
      [11, { pid: 11, parent: 10, born: "shell1", name: "sh" }],
      [20, { pid: 20, parent: 1, born: "second" }],
      [21, { pid: 21, parent: 20, born: "shell2", name: "sh" }],
    ]);
    const identity = (pid) => identities.get(pid) || null;
    const first = path.join(tmp, 'one "quoted" $literal');
    const second = path.join(tmp, "two 'quoted' $literal");
    assert.ok(publishProject({ cwd: first, session_id: "one" }, { pid: 10, identity, tmp }));
    assert.ok(publishProject({ cwd: second, session_id: "two" }, { pid: 20, identity, tmp }));
    assert.deepEqual(
      await Promise.all([
        sessionProject({ pid: 11, identity, tmp, waitMs: 0 }),
        sessionProject({ pid: 21, identity, tmp, waitMs: 0 }),
      ]),
      [first, second],
    );
    if (process.platform !== "win32")
      for (const file of fs.readdirSync(tmp)) assert.equal(fs.statSync(path.join(tmp, file)).mode & 0o777, 0o600);
    identities.set(30, { pid: 30, parent: 10, born: "nested-client", name: "claude" });
    identities.set(31, { pid: 31, parent: 30, born: "nested-shell", name: "sh" });
    assert.equal(
      await sessionProject({ pid: 31, identity, tmp, waitMs: 0 }),
      "",
      "a nested client never inherits an outer client's context",
    );
    identities.set(10, { pid: 10, parent: 1, born: "reused" });
    assert.equal(await sessionProject({ pid: 11, identity, tmp, waitMs: 0 }), "");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("a later context write can complete while the helper waits", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "loom-project-test-"));
  const identity = (pid) => ({ pid, parent: 1, born: "live" });
  try {
    const pending = sessionProject({ pid: 10, identity, tmp, waitMs: 250 });
    publishProject({ cwd: tmp, session_id: "later" }, { pid: 10, identity, tmp });
    assert.equal(await pending, tmp);
    assert.equal(await sessionProject({ pid: 99, identity, tmp, waitMs: 0 }), "");
    assert.equal(publishProject({ cwd: "relative", session_id: "bad" }, { pid: 10, identity, tmp }), false);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
