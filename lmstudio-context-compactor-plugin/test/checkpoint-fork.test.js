"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs/promises");
const store = require("../src/checkpoint-store.js");
const core = require("../src/compaction-core.js");

test("resolveSessionFork isolates identical openers once lineage diverges", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ucc-fork-"));
  const baseKey = "same-opener-base";
  const opener = core.messageLineageFingerprints([
    { role: "system", content: "rules" },
    { role: "user", content: "현재 프로젝트 구조 분석해줘" },
  ]);
  const chatA1 = await store.resolveSessionFork({ baseKey, lineage: opener, root });
  assert.equal(chatA1.minted, true);
  const chatAGrew = core.messageLineageFingerprints([
    { role: "system", content: "rules" },
    { role: "user", content: "현재 프로젝트 구조 분석해줘" },
    { role: "assistant", content: "structure overview A" },
  ]);
  await store.touchSessionFork(baseKey, chatA1.sessionId, chatAGrew, root);
  const chatA2 = await store.resolveSessionFork({ baseKey, lineage: chatAGrew, root });
  assert.equal(chatA2.sessionId, chatA1.sessionId);
  assert.equal(chatA2.minted, false);

  const chatB = await store.resolveSessionFork({ baseKey, lineage: opener, root });
  assert.notEqual(chatB.sessionId, chatA1.sessionId);
  assert.equal(chatB.minted, true);

  // #region agent log
  try {
    const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG
      || path.join(__dirname, "..", "..", "debug-49b048.log");
    await fs.appendFile(
      debugLog,
      `${JSON.stringify({
        sessionId: "49b048",
        runId: "release-harden",
        hypothesisId: "H-SESSION",
        location: "checkpoint-store.fork.test",
        message: "fork isolation runtime proof",
        data: {
          chatA: String(chatA1.sessionId).slice(0, 12),
          chatB: String(chatB.sessionId).slice(0, 12),
          isolated: chatA1.sessionId !== chatB.sessionId,
        },
        timestamp: Date.now(),
      })}\n`,
    );
  } catch {
    /* ignore */
  }
  // #endregion
});
