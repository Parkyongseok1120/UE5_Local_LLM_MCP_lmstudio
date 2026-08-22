"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function payloadOf(result) {
  assert.ok(result?.structuredContent);
  return result.structuredContent;
}

function fixture(t, validateMutationSemanticText) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-semantic-advisory-"));
  const projectRoot = path.join(root, "SemanticProject");
  const projectPath = path.join(projectRoot, "SemanticProject.uproject");
  const sourcePath = path.join(projectRoot, "Source", "SemanticProject", "Existing.cpp");
  const original = "int32 ExistingValue() { return 1; }\n";
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(projectPath, JSON.stringify({ FileVersion: 3 }), "utf8");
  fs.writeFileSync(sourcePath, original, "utf8");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    original,
    projectRoot,
    sourcePath,
    runtime: createDirectRuntime({
      workspaceRoot: root,
      stateRoot: path.join(root, "state"),
      configPath: path.join(root, "agent-mcp.json"),
      env: { ALLOW_WRITE: "1", AGENT_STATE_ROOT: path.join(root, "state") },
      getActiveProject: () => projectPath,
      validateMutationSemanticText,
    }),
  };
}

test("denylist hits and missing analyzers are advisory for create and exact-CAS replace", async (t) => {
  const { original, projectRoot, runtime, sourcePath } = fixture(t, (text) => (
    String(text).includes("CreatedWhileGuardMissing")
      ? {
        ok: false,
        infrastructureError: true,
        reason: "mutation_semantic_guard.py missing",
        hits: [],
      }
      : {
        ok: false,
        hits: [{ term: "disablegravity", message: "Verify the exact component gravity API." }],
      }
  ));

  const created = payloadOf(await runtime.callTool("write_file", {
    path: "project://Source/SemanticProject/Created.cpp",
    content: "void CreatedWhileGuardMissing() {}\n",
    createDirs: true,
  }));
  assert.strictEqual(created.ok, true);
  assert.strictEqual(created.semanticAdvisories[0].code, "SEMANTIC_GUARD_UNAVAILABLE");
  assert.strictEqual(created.semanticAdvisories[0].blocking, false);
  assert.strictEqual(created.semanticAdvisories[0].path, "project://Source/SemanticProject/Created.cpp");
  assert.match(fs.readFileSync(path.join(projectRoot, "Source", "SemanticProject", "Created.cpp"), "utf8"), /CreatedWhileGuardMissing/u);

  const replaced = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Source/SemanticProject/Existing.cpp",
    oldText: "return 1;",
    newText: "return 2;",
    expectedOccurrences: 1,
    expectedHash: sha256(original),
  }));
  assert.strictEqual(replaced.ok, true);
  assert.strictEqual(replaced.semanticAdvisories[0].code, "UNREAL_API_SEMANTIC_FINDINGS");
  assert.strictEqual(replaced.semanticAdvisories[0].severity, "warning");
  assert.strictEqual(replaced.semanticAdvisories[0].blocking, false);
  assert.strictEqual(replaced.semanticAdvisories[0].findingCount, 1);
  assert.deepStrictEqual(replaced.semanticAdvisories[0].findings, [{
    term: "disablegravity",
    message: "Verify the exact component gravity API.",
  }]);
  assert.match(fs.readFileSync(sourcePath, "utf8"), /return 2;/u);
  assert.ok(runtime.tools.some((tool) => tool.name === "build_unreal_project"));
});

test("a failing semantic analyzer cannot block an atomic bundle or weaken its CAS gate", async (t) => {
  let analyzerFails = true;
  const { original, runtime, sourcePath } = fixture(t, () => {
    if (analyzerFails) throw new Error("mock semantic analyzer process failure");
    return {
      ok: false,
      hits: [{ term: "setrestorestate", message: "Verify the exact Sequencer API." }],
    };
  });
  const patch = {
    path: "Source/SemanticProject/Existing.cpp",
    oldText: "return 1;",
    newText: "return 3;",
    expectedOccurrences: 1,
    expectedHash: sha256(original),
  };

  const applied = payloadOf(await runtime.callTool("apply_edit_bundle", {
    files: [],
    patches: [patch],
  }));
  assert.strictEqual(applied.ok, true);
  assert.match(applied.transactionId, /^[a-f0-9-]+$/u);
  assert.strictEqual(applied.semanticAdvisories[0].code, "SEMANTIC_GUARD_UNAVAILABLE");
  assert.strictEqual(applied.semanticAdvisories[0].blocking, false);
  assert.match(applied.semanticAdvisories[0].message, /mock semantic analyzer process failure/u);
  assert.match(fs.readFileSync(sourcePath, "utf8"), /return 3;/u);

  analyzerFails = false;
  const current = fs.readFileSync(sourcePath, "utf8");
  const findingApplied = payloadOf(await runtime.callTool("apply_edit_bundle", {
    files: [],
    patches: [{
      ...patch,
      oldText: "return 3;",
      newText: "return 4;",
      expectedHash: sha256(current),
    }],
  }));
  assert.strictEqual(findingApplied.ok, true);
  assert.strictEqual(findingApplied.semanticAdvisories[0].code, "UNREAL_API_SEMANTIC_FINDINGS");
  assert.strictEqual(findingApplied.semanticAdvisories[0].blocking, false);
  assert.match(fs.readFileSync(sourcePath, "utf8"), /return 4;/u);

  const stale = payloadOf(await runtime.callTool("apply_edit_bundle", {
    files: [],
    patches: [{ ...patch, oldText: "return 4;", newText: "return 5;" }],
  }));
  assert.strictEqual(stale.ok, false);
  assert.strictEqual(stale.errorCode, "FILE_VERSION_CONFLICT");
  assert.match(fs.readFileSync(sourcePath, "utf8"), /return 4;/u);
});
