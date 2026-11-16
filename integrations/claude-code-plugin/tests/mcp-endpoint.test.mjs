import "./temp-home.mjs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { claudeEndpoint, EndpointError, parseEndpoint } from "../lib/mcp-endpoint.mjs";

test("the endpoint keeps a proxy base and the server's union spelling", () => {
  for (const query of ["coding+tracker", "coding%2Btracker", "Coding%20Tracker", "coding,tracker,coding"]) {
    const endpoint = parseEndpoint(`https://loom.example/proxy/mcp?toolset=${query}`);
    assert.equal(endpoint.base, "https://loom.example/proxy");
    assert.equal(endpoint.toolset, "coding+tracker");
  }
  assert.equal(parseEndpoint("http://[::1]:7777/mcp?toolset=readonly").base, "http://[::1]:7777");
});

test("the server selects absent, empty and unknown rosters", () => {
  for (const suffix of ["", "?toolset=", "?toolset=unknown", "?toolset=readonly+coding"]) {
    assert.equal(parseEndpoint("https://loom.example/mcp" + suffix).toolset, null);
  }
});

test("invalid endpoints yield an actionable notice without their credentials", () => {
  for (const value of [
    "",
    "https://loom.example",
    "https://loom.example/prefix",
    "ftp://loom.example/mcp",
    "https://secret:password@loom.example/mcp",
    "https://loom.example/mcp#secret",
    "https://loom.example/mcp?toolset=coding&toolset=readonly",
    "https://loom.example/mcp?token=secret&token=other",
    "https://loom.example/a%2fmcp",
    "https://loom.example/a\\mcp",
  ]) {
    assert.throws(
      () => parseEndpoint(value),
      (error) =>
        error instanceof EndpointError &&
        error.message.includes("full MCP URL") &&
        !error.message.includes("secret") &&
        !error.message.includes("password"),
    );
  }
});

test("an explicitly empty endpoint does not select another destination", () => {
  assert.throws(
    () => claudeEndpoint({ CLAUDE_PLUGIN_OPTION_SERVER_URL: "", LOOM_BASE_URL: "https://other" }),
    EndpointError,
  );
});

test("the installed endpoint wins over the environment's default base", () => {
  assert.equal(
    claudeEndpoint({
      CLAUDE_PLUGIN_OPTION_SERVER_URL: "https://opt/proxy/mcp?toolset=tracker",
      LOOM_BASE_URL: "https://env",
    }).base,
    "https://opt/proxy",
  );
  assert.equal(claudeEndpoint({ CLAUDE_CODE_MCP_SERVER_URL: "https://helper/mcp" }).base, "https://helper");
  assert.equal(claudeEndpoint({ LOOM_BASE_URL: "https://env/proxy/" }).url, "https://env/proxy/mcp?toolset=coding");
});
