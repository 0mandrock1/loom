import { describe, expect, it } from "vitest";
import { Agent } from "./agent.js";
import { LoomClient } from "./client.js";
import { Transport } from "./transport.js";

function setup(reply: unknown) {
  const calls: Array<{ op: string; body: Record<string, unknown> }> = [];
  const fetch = async (url: string, init: RequestInit): Promise<Response> => {
    calls.push({ op: url.split("/v1/")[1], body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify(reply), { status: 200 });
  };
  const agent = new Agent(
    "test",
    "memory",
    "consumer",
    new LoomClient(new Transport({ baseUrl: "http://example.invalid", fetch, logLevel: "off" })),
  );
  return { agent, calls };
}

describe("model memory metadata", () => {
  it.each([{ files: ["src/app.py"] }, { files: [] }])("retains explicit file references: %j", async ({ files }) => {
    const receipt = { memory_id: "m", version: 3, code_state: "unresolved" };
    const { agent, calls } = setup(receipt);
    const result = JSON.parse(
      await agent.tools().execute("loom_remember", {
        memory_key: "app/rationale",
        value: "A fact",
        files,
        tags: ["repo:test"],
        source_resource_id: "src/app.py:8",
      }),
    );
    expect(calls[0]).toMatchObject({
      op: "memory.set",
      body: { files, tags: ["repo:test"], source_resource_id: "src/app.py:8" },
    });
    expect(result).toMatchObject({ stored: true, code_state: "unresolved" });
  });

  it("rejects malformed metadata before a request", async () => {
    const { agent, calls } = setup({});
    await expect(
      agent.tools().execute("loom_remember", { memory_key: "k", value: "v", files: "app.py" }),
    ).rejects.toThrow("array of strings");
    expect(calls).toEqual([]);
  });

  it("opts into corrections without changing the six defaults", async () => {
    const receipt = { updated: true, memory_id: "m", new_version: 9, content_sha256: "new" };
    const { agent, calls } = setup(receipt);
    expect(agent.tools().definitions()).toHaveLength(6);
    expect(agent.tools().has("loom_update")).toBe(false);
    const tools = agent.tools({ userId: "user-one", sessionId: "new-session" }, { only: ["loom_update"] });
    expect(
      JSON.parse(await tools.execute("loom_update", { memory_key: "k", value: "v", if_match_sha256: "old" })),
    ).toEqual(receipt);
    expect(calls[0]).toMatchObject({
      op: "memory.update",
      body: {
        org: "test",
        namespace: "memory",
        agent: "*",
        user_id: "user-one",
        session_id: "-",
        memory_key: "k",
        value: "v",
        if_match_sha256: "old",
      },
    });
  });
});
