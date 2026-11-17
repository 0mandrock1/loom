import { createHash } from "node:crypto";
import type { MastraDBMessage, StorageThreadType } from "@mastra/core/memory";
import { MastraCompositeStore, MemoryStorage } from "@mastra/core/storage";
import { Memory } from "@mastra/memory";
import { describe, expect, it } from "vitest";
import { Agent } from "../agent.js";
import { LoomMemoryStorage } from "./mastra.js";

process.env.MASTRA_TELEMETRY_DISABLED = "1";

type Row = {
  body: Record<string, unknown>;
  value: { kind: string; resourceId: string; data: Record<string, unknown> };
  hash: string;
};
type Call = { op: string; body: Record<string, unknown> };
const hash = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");

function harness() {
  const rows = new Map<string, Row>();
  const calls: Call[] = [];
  const controls = {
    cap: 2,
    emptyPages: 0,
    noHash: false,
    disappear: false,
    refuseSet: false,
    beforeSet: undefined as ((row: Row) => void) | undefined,
    failMessage: "",
  };
  const id = (body: Record<string, unknown>) =>
    JSON.stringify([body.org, body.namespace, body.agent, body.user_id, body.session_id, body.memory_key]);
  const fetch = async (url: string, init: RequestInit) => {
    const op = url.split("/v1/")[1];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    calls.push({ op, body });
    let reply: unknown = {};
    const key = id(body);
    if (op === "memory.set") {
      const value = JSON.parse(body.value as string) as Row["value"];
      const current = rows.get(key);
      if (current && controls.beforeSet) {
        const update = controls.beforeSet;
        controls.beforeSet = undefined;
        update(current);
        current.hash = hash(current.value);
      }
      if (body.if_match_sha256 && body.if_match_sha256 !== current?.hash) {
        return new Response(JSON.stringify({ detail: { code: "PRECONDITION_FAILED", message: "Changed" } }), {
          status: 409,
        });
      }
      if (value.data.id === controls.failMessage) {
        return new Response(JSON.stringify({ detail: { message: "Write refused" } }), { status: 422 });
      }
      if (controls.refuseSet) return new Response(JSON.stringify({ version: 0, status: "proposed" }));
      rows.set(key, { body: { ...body }, value, hash: hash(value) });
      reply = { version: 1, memory_key: body.memory_key, content_sha256: hash(value) };
    } else if (op === "memory.get") {
      const row = controls.disappear ? undefined : rows.get(key);
      reply = row
        ? {
            found: true,
            value: structuredClone(row.value),
            content_sha256: controls.noHash ? "" : row.hash,
            agent: row.body.agent,
            user_id: row.body.user_id,
            session_id: row.body.session_id,
          }
        : { found: false };
    } else if (op === "memory.list") {
      const scope = ["org", "namespace", "agent", "user_id", "session_id"];
      const matching = [...rows.values()].filter(
        (row) =>
          scope.every((field) => row.body[field] === body[field]) &&
          (body.memory_type === undefined || row.body.memory_type === body.memory_type) &&
          (row.body.memory_key as string).startsWith(body.prefix as string),
      );
      const scanned = [...Array.from({ length: controls.emptyPages }, () => null), ...matching];
      const offset = body.offset as number;
      const page = scanned.slice(offset, offset + controls.cap);
      reply = {
        entries: page.filter((row) => row !== null).map((row) => ({ memory_key: row.body.memory_key })),
        truncated: offset + controls.cap < scanned.length,
      };
    } else if (op === "memory.delete") {
      reply = { version: 2, tombstoned: rows.delete(key) };
    } else throw new Error(`Unexpected operation ${op}`);
    return new Response(JSON.stringify(reply));
  };
  const agent = new Agent("o", "n", "bot", { baseUrl: "http://x", fetch, logLevel: "off" });
  const storage = (resourceId = "alice", storeId = "app") => new LoomMemoryStorage(agent, { resourceId, storeId });
  return { agent, rows, calls, controls, storage };
}

const date = (second: number) => new Date(Date.UTC(2025, 0, 1, 0, 0, second));
const thread = (id: string, second = 0, resourceId = "alice"): StorageThreadType => ({
  id,
  resourceId,
  title: `Thread ${id}`,
  createdAt: date(second),
  updatedAt: date(second),
  metadata: { group: "work" },
});
const message = (id: string, threadId = "t1", second = 0, resourceId = "alice"): MastraDBMessage => ({
  id,
  threadId,
  resourceId,
  role: "user",
  createdAt: date(second),
  content: { format: 2, parts: [{ type: "text", text: "same text" }], metadata: { label: "kept", count: 1 } },
});

describe("LoomMemoryStorage native memory domain", () => {
  it("serves native Memory save, recall, working-memory and delete methods without a vector store", async () => {
    const h = harness();
    const native = () =>
      new Memory({
        storage: new MastraCompositeStore({ id: "app", domains: { memory: h.storage() } }),
        options: {
          semanticRecall: false,
          observationalMemory: false,
          workingMemory: { enabled: true, scope: "resource" },
        },
      });
    await native().saveThread({ thread: thread("t1") });
    await native().saveMessages({ messages: [message("m1")] });
    expect(
      (await native().recall({ threadId: "t1", resourceId: "alice", perPage: false })).messages.map((item) => item.id),
    ).toEqual(["m1"]);
    await native().updateWorkingMemory({ threadId: "t1", resourceId: "alice", workingMemory: "Resource notes" });
    expect(await native().getWorkingMemory({ threadId: "t1", resourceId: "alice" })).toBe("Resource notes");
    await native().deleteThread("t1");
    await expect(native().recall({ threadId: "t1", resourceId: "alice", perPage: false })).rejects.toThrow(
      "No thread found",
    );
    expect((await h.storage().listMessages({ threadId: "t1", perPage: false })).messages).toEqual([]);
  });
  it("is a native memory-only composite domain and refuses unsupported capabilities", async () => {
    const storage = harness().storage();
    const composite = new MastraCompositeStore({ id: "test", domains: { memory: storage } });
    expect(await composite.getStore("memory")).toBe(storage);
    expect(await composite.getStore("workflows")).toBeUndefined();
    expect(storage).toBeInstanceOf(MemoryStorage);
    expect(storage.supportsRunFencing()).toBe(false);
    expect(storage.supportsObservationalMemory).toBe(false);
    expect(storage.supportsAtomicWorkingMemoryMerge).toBe(false);
    expect(storage.supportsPartialThreadUpdate).toBe(true);
    await storage.init();
    await expect(storage.saveThread({ thread: thread("t1"), fence: {} as never })).rejects.toThrow("fencing");
    await expect(storage.copyThread({ sourceThreadId: "t1" })).rejects.toThrow("not implemented");
  });

  it.each(["", "-", "*", " alice", "alice\u0085", "alice\u001c"])("rejects noncanonical user %j", (resourceId) => {
    const { agent, calls } = harness();
    expect(() => new LoomMemoryStorage(agent, { resourceId })).toThrow("resourceId");
    expect(calls).toEqual([]);
  });

  it("snapshots constructor options and the client identity before the first write", async () => {
    const { agent, calls } = harness();
    const options = { resourceId: "alice", storeId: "app" };
    const storage = new LoomMemoryStorage(agent, options);
    options.resourceId = "bob";
    options.storeId = "other";
    Object.assign(agent, { org: "other", namespace: "other", agent: "other" });
    await storage.saveThread({ thread: thread("t1") });
    expect(
      calls.every(
        (call) =>
          call.body.org === "o" &&
          call.body.namespace === "n" &&
          call.body.agent === "bot" &&
          call.body.user_id === "alice",
      ),
    ).toBe(true);
    expect(calls[0].body).toMatchObject({ session_id: "-", memory_type: "episodic", clear_expiry: true });
  });

  it("resolves identical thread/message IDs from a fresh instance within each real user", async () => {
    const h = harness();
    for (const user of ["alice", "bob"]) {
      await h.storage(user).saveThread({ thread: thread("t1", 0, user) });
      await h.storage(user).saveMessages({ messages: [message("m1", "t1", 0, user)] });
    }
    const alice = h.storage();
    expect((await alice.getThreadById({ threadId: "t1" }))?.resourceId).toBe("alice");
    expect((await alice.listMessagesById({ messageIds: ["m1"] })).messages[0].resourceId).toBe("alice");
    expect((await h.storage("bob").listMessagesById({ messageIds: ["m1"] })).messages[0].resourceId).toBe("bob");
    expect([...h.rows.values()].every((row) => row.body.user_id === row.value.resourceId)).toBe(true);
  });

  it("returns missing across resources and rejects every cross-resource write before a batch starts", async () => {
    const h = harness();
    const storage = h.storage();
    await storage.saveThread({ thread: thread("t1") });
    const before = h.calls.length;
    expect(await storage.getThreadById({ threadId: "t1", resourceId: "bob" })).toBeNull();
    expect(await storage.getResourceById({ resourceId: "bob" })).toBeNull();
    expect((await storage.listThreads({ filter: { resourceId: "bob" } })).threads).toEqual([]);
    expect(
      (await storage.listMessages({ threadId: "t1", resourceId: "bob", include: [{ id: "m1" }] })).messages,
    ).toEqual([]);
    expect((await storage.listMessagesByResourceId({ resourceId: "bob" })).messages).toEqual([]);
    await expect(storage.saveThread({ thread: thread("t2", 0, "bob") })).rejects.toThrow("resource");
    await expect(storage.saveMessages({ messages: [message("good"), message("bad", "t1", 0, "bob")] })).rejects.toThrow(
      "resource",
    );
    await expect(
      storage.updateMessages({ messages: [{ id: "good" }, { id: "bad", resourceId: "bob" }] }),
    ).rejects.toThrow("resource");
    await expect(storage.updateResource({ resourceId: "bob", workingMemory: "secret" })).rejects.toThrow("resource");
    await expect(storage.updateThreadResourceId({ threadId: "t1", resourceId: "bob" })).rejects.toThrow("resource");
    expect(h.calls).toHaveLength(before);
  });

  it("round-trips full native payloads and dates and upserts a repeated message ID", async () => {
    const h = harness();
    await h.storage().saveThread({ thread: thread("t1") });
    const original = message("m1");
    original.role = "signal";
    original.type = "native";
    original.content.parts = [{ type: "text", text: "x".repeat(6000) }];
    original.content.providerMetadata = { test: { detail: "preserved" } };
    original.content.annotations = [{ source: "native" }];
    await h.storage().saveMessages({ messages: [original, message("m2")] });
    await h.storage().saveMessages({ messages: [original] });
    original.content.parts = [];
    for (const row of h.rows.values()) row.body.memory_type = "semantic";
    const resumed = h.storage();
    const out = await resumed.listMessages({ threadId: "t1", perPage: false });
    expect(out.messages).toHaveLength(2);
    expect(out.messages[0]).toMatchObject({ role: "signal", type: "native", createdAt: date(0) });
    expect(out.messages[0].content.parts).toEqual([{ type: "text", text: "x".repeat(6000) }]);
    expect(out.messages[0].content.providerMetadata).toEqual({ test: { detail: "preserved" } });
    expect(out.messages[0].content.annotations).toEqual([{ source: "native" }]);
    expect((await resumed.getThreadById({ threadId: "t1" }))?.createdAt).toEqual(date(0));
  });

  it("reapplies a partial metadata update after a concurrent title change without clobbering it", async () => {
    const h = harness();
    const storage = h.storage();
    await storage.saveThread({ thread: thread("t1") });
    h.controls.beforeSet = (row) => {
      row.value.data.title = "Concurrent title";
    };
    const updated = await storage.patchThread({ id: "t1", metadata: { added: true } });
    expect(updated.title).toBe("Concurrent title");
    expect(updated.metadata).toEqual({ group: "work", added: true });
    expect(h.calls.filter((call) => call.op === "memory.set" && call.body.if_match_sha256)).toHaveLength(2);
    await storage.updateThreadMetadata({
      id: "t1",
      update: (current) => ({ count: Number(current.metadata?.count ?? 0) + 1 }),
    });
    expect((await h.storage().getThreadById({ threadId: "t1" }))?.metadata).toMatchObject({ added: true, count: 1 });
    const writes = h.calls.filter((call) => call.op === "memory.set").length;
    await storage.updateThreadMetadata({ id: "t1", update: () => undefined });
    expect(h.calls.filter((call) => call.op === "memory.set")).toHaveLength(writes);
    await expect(storage.updateThread({ id: "missing", title: "x" })).rejects.toThrow("not found");
  });

  it("stores resource working memory and preserves omitted fields on metadata updates", async () => {
    const h = harness();
    await h.storage().updateResource({ resourceId: "alice", workingMemory: "preferences", metadata: { first: 1 } });
    await h.storage().updateResource({ resourceId: "alice", metadata: { second: 2 } });
    const resource = await h.storage().getResourceById({ resourceId: "alice" });
    expect(resource).toMatchObject({ id: "alice", workingMemory: "preferences", metadata: { first: 1, second: 2 } });
    expect(resource?.createdAt).toBeInstanceOf(Date);
    expect([...h.rows.values()].every((row) => row.body.user_id === "alice")).toBe(true);
  });

  it("scans capped and empty projected pages before native thread filtering and pagination", async () => {
    const h = harness();
    h.controls.cap = 1;
    h.controls.emptyPages = 1;
    const storage = h.storage();
    for (let i = 0; i < 4; i++) {
      await storage.saveThread({
        thread: { ...thread(`t${i}`, i), metadata: { nested: { a: 1, b: 2 }, group: i < 3 ? "work" : "other" } },
      });
    }
    const out = await storage.listThreads({
      filter: { metadata: { nested: { b: 2, a: 1 }, group: "work" } },
      page: 1,
      perPage: 1,
    });
    expect(out).toMatchObject({ total: 3, page: 1, perPage: 1, hasMore: true });
    expect(out.threads.map((item) => item.id)).toEqual(["t1"]);
    expect((await storage.listThreads({ perPage: false })).threads).toHaveLength(4);
    expect((await storage.listThreads({ perPage: 0 })).threads).toEqual([]);
    await expect(storage.listThreads({ page: 1, perPage: false })).rejects.toThrow();
    await expect(storage.listThreads({ perPage: -1 })).rejects.toThrow();
  });

  it("applies native date/scalar metadata filters and adds deduplicated cross-thread context", async () => {
    const h = harness();
    const storage = h.storage();
    await storage.saveMessages({
      messages: [
        message("a", "t1", 0),
        message("b", "t1", 1),
        message("c", "t1", 2),
        message("d", "t2", 3),
        message("e", "t2", 4),
      ],
    });
    const out = await storage.listMessages({
      threadId: ["t1"],
      perPage: 1,
      filter: {
        dateRange: { start: date(0), startExclusive: true, end: date(2), endExclusive: true },
        metadata: { count: 1 },
      },
      include: [{ id: "d", withNextMessages: 1 }, { id: "d" }],
    });
    expect(out.messages.map((item) => item.id)).toEqual(["b", "d", "e"]);
    expect(out.total).toBe(1);
    expect(out.hasMore).toBe(false);
    const allIncluded = await storage.listMessages({
      threadId: "t1",
      perPage: 1,
      include: [{ id: "b", withPreviousMessages: 1, withNextMessages: 1 }],
    });
    expect(allIncluded.messages.map((item) => item.id)).toEqual(["a", "b", "c"]);
    expect(allIncluded.hasMore).toBe(false);
    expect(await storage.listMessagesByResourceId({ resourceId: "alice", perPage: 0 })).toMatchObject({
      total: 5,
      hasMore: true,
      messages: [],
    });
    expect(
      (
        await storage.listMessages({ threadId: "t1", perPage: 0, include: [{ id: "b", withPreviousMessages: 1 }] })
      ).messages.map((item) => item.id),
    ).toEqual(["a", "b"]);
    expect(
      (
        await storage.listMessagesByResourceId({ resourceId: "alice", perPage: 2, orderBy: { direction: "DESC" } })
      ).messages.map((item) => item.id),
    ).toEqual(["e", "d"]);
    expect((await storage.listMessages({ threadId: "t1", filter: { metadata: { count: "1" } } })).messages).toEqual([]);
  });

  it("merges message metadata, preserves createdAt and role, and touches both threads on a move", async () => {
    const h = harness();
    const storage = h.storage();
    await storage.saveThread({ thread: thread("t1") });
    await storage.saveThread({ thread: thread("t2") });
    await storage.saveMessages({ messages: [message("m1")] });
    const before = await storage.getThreadById({ threadId: "t1" });
    if (!before) throw new Error("Missing source thread");
    const updated = await storage.updateMessages({
      messages: [
        { id: "missing" },
        { id: "m1", threadId: "t2", role: "system", content: { metadata: { added: true } } },
      ],
    });
    expect(updated).toHaveLength(1);
    expect(updated[0]).toMatchObject({
      role: "system",
      threadId: "t2",
      createdAt: date(0),
      content: { metadata: { label: "kept", count: 1, added: true } },
    });
    expect(updated[0].content.parts).toEqual(message("m1").content.parts);
    await storage.updateMessages({
      messages: [{ id: "m1", role: undefined, threadId: undefined, content: { metadata: undefined } }],
    });
    const preserved = (await h.storage().listMessagesById({ messageIds: ["m1"] })).messages[0];
    expect(preserved).toMatchObject({ role: "system", threadId: "t2", content: { metadata: { added: true } } });
    expect((await storage.getThreadById({ threadId: "t1" }))?.updatedAt.getTime()).toBeGreaterThan(
      before.updatedAt.getTime(),
    );
    expect((await storage.getThreadById({ threadId: "t2" }))?.updatedAt.getTime()).toBeGreaterThan(date(0).getTime());
  });

  it("cascades deletes and clears only its own store and user", async () => {
    const h = harness();
    for (const [user, store] of [
      ["alice", "app"],
      ["bob", "app"],
      ["alice", "other"],
    ]) {
      const storage = h.storage(user, store);
      await storage.saveThread({ thread: thread("t1", 0, user) });
      await storage.saveThread({ thread: thread("t2", 0, user) });
      await storage.saveMessages({ messages: [message("m1", "t1", 0, user), message("m2", "t2", 0, user)] });
      await storage.updateResource({ resourceId: user, workingMemory: "notes" });
    }
    await h.storage().deleteMessages(["m1", "missing"]);
    expect((await h.storage().listMessagesById({ messageIds: ["m1", "m2"] })).messages.map((item) => item.id)).toEqual([
      "m2",
    ]);
    await h.storage().deleteThread({ threadId: "t2" });
    expect((await h.storage().listMessagesById({ messageIds: ["m2"] })).messages).toEqual([]);
    await h.storage().dangerouslyClearAll();
    expect((await h.storage().listThreads({})).threads).toEqual([]);
    expect((await h.storage("bob").listMessagesById({ messageIds: ["m1"] })).messages).toHaveLength(1);
    expect((await h.storage("alice", "other").listMessagesById({ messageIds: ["m1"] })).messages).toHaveLength(1);
  });

  it("propagates partial batch failure, refuses missing hashes and fails a disappearing history read", async () => {
    const h = harness();
    h.controls.failMessage = "bad";
    await expect(h.storage().saveMessages({ messages: [message("good"), message("bad")] })).rejects.toThrow(
      "Write refused",
    );
    expect((await h.storage().listMessagesById({ messageIds: ["good", "bad"] })).messages).toHaveLength(1);
    await h.storage().saveThread({ thread: thread("t1") });
    h.controls.noHash = true;
    await expect(h.storage().updateThread({ id: "t1", title: "change" })).rejects.toThrow("hash");
    h.controls.noHash = false;
    h.controls.disappear = true;
    await expect(h.storage().listThreads({})).rejects.toThrow("changed during listing");
  });

  it("does not acknowledge a policy-held write as persisted", async () => {
    const h = harness();
    h.controls.refuseSet = true;
    await expect(h.storage().saveThread({ thread: thread("t1") })).rejects.toThrow("did not persist");
  });
});
