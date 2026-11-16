import "./temp-home.mjs";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  checkoutPython,
  decideBlock,
  gatePayload,
  gateSignature,
  outsideEntries,
  renderGate,
  sessionScope,
  venvPython,
} from "../lib/test-gate.mjs";
import { pathUnder } from "../lib/loom-client.mjs";

/** Whether `cmd` starts here, so a test of what the probe runs is not vacuous. */
function runs(cmd) {
  try {
    execFileSync(cmd, ["-c", ""], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const GATE = {
  obligations: true,
  counts_floor: true,
  tests: [
    { test_path: "tests/unit/test_a.py", why: "stem_partner", run: "pytest tests/unit/test_a.py", run_unknown: false },
    { test_path: "tests/unit/test_a.py", why: "test_map", run: "pytest tests/unit/test_a.py", run_unknown: false },
    { test_path: "src/x/tests/gtest_parts.cpp", why: "graph_hop", run: "", run_unknown: true },
  ],
  untested: [
    { qualified_name: "DB::MergeTreeData::renameTempPartAndAdd", path: "src/a.cpp" },
    { qualified_name: "DB::MergeTreeData::renameTempPartAndAdd", path: "src/a.cpp" },
    { qualified_name: "DB::Foo::bar", path: "src/b.cpp" },
  ],
  untested_total: 2,
};

test("signature is order-independent over paths and bound to the diff", () => {
  assert.equal(gateSignature(["b.py", "a.py"], "h1"), gateSignature(["a.py", "b.py"], "h1"));
  assert.notEqual(gateSignature(["a.py"], "h1"), gateSignature(["a.py"], "h2"));
});

test("blocks once per signature, then lets the stop through", () => {
  const first = decideBlock({}, "sig");
  assert.equal(first.block, true);
  const second = decideBlock(first.cache, "sig");
  assert.equal(second.block, false);
  const other = decideBlock(second.cache, "sig2");
  assert.equal(other.block, true);
});

test("render dedupes commands, lists unknown-runner tests and untested names", () => {
  const text = renderGate(GATE);
  assert.match(text, /^Loom test gate \(graph is a floor\): run: pytest tests\/unit\/test_a\.py · /);
  assert.equal((text.match(/pytest tests\/unit\/test_a\.py/g) || []).length, 1);
  assert.match(text, /also reached \(no known runner\): src\/x\/tests\/gtest_parts\.cpp/);
  assert.match(text, /no test reaches: MergeTreeData::renameTempPartAndAdd, Foo::bar\./);
  assert.match(text, /Run the tests and rely on green/);
});

test("render caps and counts the overflow", () => {
  const many = {
    obligations: true,
    tests: Array.from({ length: 6 }, (_, i) => ({ run: `pytest t${i}.py` })),
    untested: [],
    untested_total: 0,
  };
  const text = renderGate(many, { maxRuns: 2 });
  assert.match(text, /pytest t0\.py ; pytest t1\.py \(\+4 more\)/);
});

test("a deleted indexed test is reported as missing, never prescribed as a runnable command", () => {
  const deleted = "tests/unit/test_migrations_schema_slice.py";
  const gate = {
    ...GATE,
    tests: [
      ...GATE.tests,
      { test_path: deleted, run: `pytest ${deleted}` },
      { test_path: deleted, run: `pytest ${deleted}` },
    ],
  };
  const text = renderGate(gate, {
    python: ".venv/bin/python",
    testExists: (p) => p !== deleted,
  });
  assert.match(text, /run: \.venv\/bin\/python -m pytest tests\/unit\/test_a\.py/);
  assert.doesNotMatch(text, /pytest tests\/unit\/test_migrations_schema_slice\.py/);
  assert.match(text, /indexed tests not present in this checkout: tests\/unit\/test_migrations_schema_slice\.py/);
  assert.equal((text.match(/test_migrations_schema_slice\.py/g) || []).length, 1);
  assert.match(text, /Missing tests are not passes/);
  assert.match(text, /no test reaches:/);
});

test("a gate with only unavailable tests remains an explicit obligation", () => {
  const text = renderGate(
    { obligations: true, tests: [{ test_path: "tests/old.py", run: "pytest tests/old.py" }] },
    { testExists: () => false },
  );
  assert.doesNotMatch(text, /run:|pytest/);
  assert.match(text, /indexed tests not present in this checkout: tests\/old\.py/);
  assert.match(text, /current equivalent tests/);
  assert.equal(gatePayload(text).decision, "block");
});

test("missing unknown-runner tests are distinguished from existing unknown-runner tests", () => {
  const text = renderGate(GATE, { testExists: (p) => p.endsWith(".py") });
  assert.doesNotMatch(text, /also reached \(no known runner\)/);
  assert.match(text, /indexed tests not present in this checkout: src\/x\/tests\/gtest_parts\.cpp/);
  assert.match(renderGate(GATE, { testExists: () => true }), /also reached \(no known runner\)/);
});

test("missing-path reporting is bounded and does not mutate the server response", () => {
  const gate = {
    obligations: true,
    tests: Array.from({ length: 6 }, (_, i) => ({ test_path: `tests/t${i}.py`, run: `pytest tests/t${i}.py` })),
  };
  const original = structuredClone(gate);
  const text = renderGate(gate, { maxNames: 2, testExists: () => false });
  assert.match(text, /tests\/t0\.py, tests\/t1\.py \(\+4\)/);
  assert.deepEqual(gate, original);
});

test("no obligations renders nothing and produces no payload", () => {
  assert.equal(renderGate({ obligations: false, tests: [], untested: [] }), "");
  assert.equal(renderGate(null), "");
  assert.equal(gatePayload(""), null);
  assert.deepEqual(gatePayload("x"), { decision: "block", reason: "x" });
});

// --- sessionScope: the change is the session's, not the tree's

const ROOT = "/r";
const use = (name, input, extra = {}) => ({
  type: "assistant",
  message: { content: [{ type: "tool_use", name, input }] },
  ...extra,
});
const DIRTY = ["src/a.py", "src/b.py", "tests/test_a.py", "a.py", "docs/notes.md"];

test("a dirty tree the session never touched gates nothing", () => {
  assert.deepEqual(sessionScope(DIRTY, [], { root: ROOT }), []);
  assert.deepEqual(
    sessionScope(DIRTY, [{ type: "user", message: { content: "edit src/a.py please" } }], { root: ROOT }),
    [],
  );
  assert.deepEqual(sessionScope([], [use("Edit", { file_path: "/r/src/a.py" })], { root: ROOT }), []);
});

test("edit-tool targets count, absolute or relative to cwd, in dirty order", () => {
  const objs = [
    use("Write", { file_path: "/r/tests/test_a.py" }),
    use("Edit", { file_path: "a.py" }), // relative to cwd /r/src → src/a.py
    use("MultiEdit", { edits: [{ file_path: "/r/docs/notes.md" }] }),
    use("Edit", { file_path: "/elsewhere/a.py" }), // outside the repo
  ];
  assert.deepEqual(sessionScope(DIRTY, objs, { root: ROOT, cwd: "/r/src" }), [
    "src/a.py",
    "tests/test_a.py",
    "docs/notes.md",
  ]);
});

test("a shell command naming the path counts; a longer name containing it does not", () => {
  const sed = [use("Bash", { command: "sed -i 's/x/y/' src/b.py && git diff --stat" })];
  assert.deepEqual(sessionScope(DIRTY, sed, { root: ROOT }), ["src/b.py"]);
  const abs = [use("Bash", { command: "cat > /r/docs/notes.md <<'EOF'\nhi\nEOF" })];
  assert.deepEqual(sessionScope(DIRTY, abs, { root: ROOT }), ["docs/notes.md"]);
  const pytest = [use("Bash", { command: "pytest tests/test_a.py::test_x -q" })];
  assert.deepEqual(sessionScope(DIRTY, pytest, { root: ROOT }), ["tests/test_a.py"]);
  const near = [use("Bash", { command: "python data.py; ls src/a.pyc; echo /other/r/src/b.py" })];
  assert.deepEqual(sessionScope(DIRTY, near, { root: ROOT }), []);
});

test("on Windows a command names an absolute path with either slash or drive case, or as Git Bash writes it", {
  skip: process.platform !== "win32",
}, () => {
  const root = "C:\\r";
  for (const named of ["C:\\r\\src\\b.py", "C:/r/src/b.py", "c:/r/src/b.py", "c:\\r\\src\\b.py", "/c/r/src/b.py"]) {
    const cmd = [use("Bash", { command: `sed -i 's/x/y/' ${named}` })];
    assert.deepEqual(sessionScope(["src/b.py"], cmd, { root }), ["src/b.py"], named);
  }
});

test("a virtualenv's interpreter: bin/python, and Scripts\\python.exe on Windows", () => {
  assert.equal(venvPython("/r/.venv", "linux"), "/r/.venv/bin/python");
  assert.equal(venvPython("/r/.venv", "darwin"), "/r/.venv/bin/python");
  assert.equal(venvPython("C:\\r\\.venv", "win32"), "C:\\r\\.venv\\Scripts\\python.exe");
});

test("a PowerShell command naming the path counts, as a Bash one does", () => {
  const ps = [use("PowerShell", { command: "(Get-Content src/b.py) -replace 'x','y' | Set-Content src/b.py" })];
  assert.deepEqual(sessionScope(DIRTY, ps, { root: ROOT }), ["src/b.py"]);
});

test("Codex shapes: argv shell commands and apply_patch envelopes", () => {
  const shell = [use("shell", { command: ["bash", "-lc", "sed -i 's/x/y/' src/a.py"] })];
  assert.deepEqual(sessionScope(DIRTY, shell, { root: ROOT }), ["src/a.py"]);
  const patch = [
    use("apply_patch", { input: "*** Begin Patch\n*** Update File: tests/test_a.py\n@@\n-a\n+b\n*** End Patch" }),
  ];
  assert.deepEqual(sessionScope(DIRTY, patch, { root: ROOT, cwd: ROOT }), ["tests/test_a.py"]);
});

test("Codex rollout tool calls scope the gate to paths named by this session", () => {
  const rows = [
    {
      type: "response_item",
      payload: {
        type: "custom_tool_call",
        name: "exec",
        input: "const r=await tools.exec_command({cmd:\"python3 -c 'edit src/a.py'\"});text(r)",
      },
    },
    {
      type: "response_item",
      payload: {
        type: "custom_tool_call",
        name: "exec",
        input: 'await tools.apply_patch("*** Begin Patch\\n*** Update File: tests/test_a.py\\n*** End Patch")',
      },
    },
    { type: "response_item", payload: { type: "message", content: "src/b.py" } },
  ];
  assert.deepEqual(sessionScope(DIRTY, rows, { root: ROOT }), ["src/a.py", "tests/test_a.py"]);
});

test("sidechain edits and non-assistant rows are not the session's change", () => {
  const objs = [
    use("Edit", { file_path: "/r/src/a.py" }, { isSidechain: true }),
    { type: "user", message: { content: [{ type: "tool_result", content: "M src/b.py" }] } },
  ];
  assert.deepEqual(sessionScope(DIRTY, objs, { root: ROOT }), []);
});

// ---- ---------------------------------------------------------

import { commentOnlyDiff, withPython } from "../lib/test-gate.mjs";

const hunk = (file, lines) =>
  `diff --git a/${file} b/${file}\n--- a/${file}\n+++ b/${file}\n@@ -1,0 +2 @@\n${lines.join("\n")}\n`;

test("a comment- or blank-only diff owes no test", () => {
  assert.equal(commentOnlyDiff(hunk("server/naming.py", ["+# loomcheck"])), true);
  assert.equal(commentOnlyDiff(hunk("lib/a.mjs", ["+// note", "+", "-/* old */"])), true);
  assert.equal(commentOnlyDiff(hunk("q.sql", ["+-- why"])), true);
  assert.equal(commentOnlyDiff(hunk("server/naming.py", ["+# note", "+x = 1"])), false);
  // `*ptr = 1;` is C, not a comment line
  assert.equal(commentOnlyDiff(hunk("src/a.cpp", ["+*ptr = 1;"])), false);
  // an unknown language, a rename and an empty diff count as code
  assert.equal(commentOnlyDiff(hunk("data.csv", ["+# a,b"])), false);
  assert.equal(commentOnlyDiff("diff --git a/a.py b/b.py\nsimilarity index 100%\nrename from a.py\n"), false);
  assert.equal(commentOnlyDiff(""), false);
  // every file of a multi-file diff must be comment-only
  assert.equal(commentOnlyDiff(hunk("a.py", ["+# x"]) + hunk("b.py", ["+y()"])), false);
});

test("pytest runs use the checkout's interpreter; none found is said plainly", () => {
  assert.equal(withPython("pytest tests/a.py", ".venv/bin/python"), ".venv/bin/python -m pytest tests/a.py");
  assert.equal(withPython("go test ./x/", ".venv/bin/python"), "go test ./x/");
  assert.equal(withPython("pytest tests/a.py", undefined), "pytest tests/a.py");
  const found = renderGate(GATE, { python: ".venv/bin/python" });
  assert.match(found, /run: \.venv\/bin\/python -m pytest tests\/unit\/test_a\.py/);
  assert.doesNotMatch(found, /No Python env/);
  const none = renderGate(GATE, { python: "" });
  assert.match(none, /No Python env with pytest was found in this checkout/);
  assert.match(renderGate(GATE), /run: pytest tests\/unit\/test_a\.py/);
});

test("nothing to run asks for a test, not for running tests", () => {
  const text = renderGate({
    obligations: true,
    untested: [{ qualified_name: "server.naming::validate_namespace_name" }],
  });
  assert.match(text, /no test reaches: server\.naming::validate_namespace_name/);
  assert.match(text, /Add a test that reaches it, or say why the change needs none/);
  assert.doesNotMatch(text, /Run the tests/);
});

test("checkoutPython's pytest probe never imports a pytest.py from the checkout", (t) => {
  if (!runs("python3") && !runs("python")) {
    t.skip("no python3 or python");
    return;
  }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-gate-"));
  const venv = process.env.VIRTUAL_ENV;
  delete process.env.VIRTUAL_ENV; // an active virtualenv is used before the probe runs
  const pythonPath = process.env.PYTHONPATH;
  // Empty, relative and absolute entries that name the checkout.
  process.env.PYTHONPATH = ["", ".", "src", root, path.join(root, "src")].join(path.delimiter);
  const marker = path.join(root, "ran");
  fs.writeFileSync(path.join(root, "pytest.py"), `open(${JSON.stringify(marker)}, "w").write("x")\n`);
  fs.mkdirSync(path.join(root, "src"));
  fs.writeFileSync(path.join(root, "src", "pytest.py"), `open(${JSON.stringify(marker)}, "w").write("x")\n`);
  try {
    checkoutPython(root);
    assert.equal(fs.existsSync(marker), false);
  } finally {
    if (venv !== undefined) process.env.VIRTUAL_ENV = venv;
    if (pythonPath === undefined) delete process.env.PYTHONPATH;
    else process.env.PYTHONPATH = pythonPath;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

const USER_SITE = "import site; print(site.ENABLE_USER_SITE); print(site.getusersitepackages())";

test("checkoutPython neither runs nor accepts a pytest an editable install puts on sys.path", (t) => {
  const userBase = fs.mkdtempSync(path.join(os.tmpdir(), "loom-userbase-"));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-gate-"));
  const env = { VIRTUAL_ENV: process.env.VIRTUAL_ENV, PYTHONUSERBASE: process.env.PYTHONUSERBASE };
  delete process.env.VIRTUAL_ENV;
  process.env.PYTHONUSERBASE = userBase;
  try {
    let enabled, site;
    try {
      const out = execFileSync("python3", ["-c", USER_SITE], { cwd: userBase, encoding: "utf8" });
      [enabled, site] = out.trim().split("\n");
    } catch {
      t.skip("no python3");
      return;
    }
    if (enabled !== "True") {
      t.skip("python3 reads no user site-packages");
      return;
    }
    // An editable install's .pth file names the checkout; its pytest.py writes a marker.
    fs.mkdirSync(site, { recursive: true });
    fs.writeFileSync(path.join(site, "__editable__.checkout.pth"), `${root}\n`);
    const marker = path.join(root, "ran");
    fs.writeFileSync(path.join(root, "pytest.py"), `open(${JSON.stringify(marker)}, "w").write("x")\n`);
    const calls = [];
    const exec = (cmd, args, opts) => {
      calls.push(cmd);
      return execFileSync(cmd, args, opts);
    };
    const py = checkoutPython(root, { exec });
    assert.equal(fs.existsSync(marker), false);
    // The only pytest python3 finds is the checkout's, so python3 is not chosen.
    assert.notEqual(py, "python3");
    assert.ok(calls.includes("python3"));
  } finally {
    for (const [k, v] of Object.entries(env)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(userBase, { recursive: true, force: true });
  }
});

test("checkoutPython never starts a python3 that PATH finds in the checkout", (t) => {
  if (process.platform === "win32") {
    t.skip("a shell script is not an executable on Windows");
    return;
  }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-gate-"));
  const env = { VIRTUAL_ENV: process.env.VIRTUAL_ENV, PATH: process.env.PATH };
  delete process.env.VIRTUAL_ENV;
  const marker = path.join(root, "ran");
  fs.mkdirSync(path.join(root, "bin"));
  for (const py of ["python3", "python"]) {
    fs.writeFileSync(path.join(root, "bin", py), `#!/bin/sh\ntouch ${JSON.stringify(marker)}\n`, { mode: 0o755 });
  }
  process.env.PATH = [path.join(root, "bin"), env.PATH].join(path.delimiter);
  try {
    checkoutPython(root);
    assert.equal(fs.existsSync(marker), false);
  } finally {
    for (const [k, v] of Object.entries(env)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("checkoutPython runs its pytest probe outside the checkout", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-gate-"));
  const venv = process.env.VIRTUAL_ENV;
  delete process.env.VIRTUAL_ENV;
  const calls = [];
  try {
    const py = checkoutPython(root, { exec: (cmd, args, opts) => calls.push([cmd, args, opts.cwd, opts.env]) });
    assert.equal(py, "python3");
    const [cmd, args, cwd, env] = calls[0];
    assert.equal(env.PATH, outsideEntries(root, process.env.PATH ?? ""));
    assert.equal(env.PYTHONPATH, outsideEntries(root, process.env.PYTHONPATH ?? ""));
    assert.equal(cmd, "python3");
    assert.deepEqual([args[0], args[2]], ["-c", root]);
    assert.match(args[1], /find_spec\("pytest"\)/);
    assert.equal(pathUnder(root, cwd), null, cwd);
  } finally {
    if (venv !== undefined) process.env.VIRTUAL_ENV = venv;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("outsideEntries keeps only absolute entries whose real path is outside the checkout", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-gate-"));
  const other = fs.mkdtempSync(path.join(os.tmpdir(), "loom-lib-"));
  const link = path.join(other, "vendor");
  fs.symlinkSync(root, link, "junction");
  try {
    const value = ["", ".", "src", root, path.join(root, "src"), link, other].join(path.delimiter);
    assert.equal(outsideEntries(root, value), other);
    assert.equal(outsideEntries(root, ""), "");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(other, { recursive: true, force: true });
  }
});
