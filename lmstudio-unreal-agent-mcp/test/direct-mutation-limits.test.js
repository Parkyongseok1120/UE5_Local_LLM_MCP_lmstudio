"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server");
const { validateBundleLimits } = require("../src/direct-edit-bundle");
const {
  HARD_MUTATION_LIMITS,
  resolveMutationLimits,
} = require("../src/direct-mutation-limits");

function lines(count, value = "x") {
  return Array.from({ length: count }, () => value).join("\n");
}

function patch(pathname, oldText, newText) {
  return {
    path: pathname,
    oldText,
    newText,
    expectedOccurrences: 1,
    expectedHash: "a".repeat(64),
  };
}

function payloadOf(result) {
  return result.structuredContent;
}

function runtimeFixture(t, env = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-mutation-limits-"));
  const projectRoot = path.join(root, "LimitsProject");
  const projectPath = path.join(projectRoot, "LimitsProject.uproject");
  const sourcePath = path.join(projectRoot, "Source", "LimitsProject", "Limits.cpp");
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(projectPath, JSON.stringify({ FileVersion: 3 }), "utf8");
  fs.writeFileSync(sourcePath, "old\n", "utf8");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    root,
    projectRoot,
    projectPath,
    sourcePath,
    runtime: createDirectRuntime({
      workspaceRoot: root,
      stateRoot: path.join(root, "state"),
      configPath: path.join(root, "agent-mcp.json"),
      getActiveProject: () => projectPath,
      env: {
        AGENT_STATE_ROOT: path.join(root, "state"),
        ALLOW_WRITE: "1",
        ...env,
      },
    }),
  };
}

test("Direct mutation limits restore bounded defaults and hard-clamp environment overrides", () => {
  assert.deepStrictEqual(resolveMutationLimits({}), {
    maxMutationChars: 12_000,
    maxPatchChars: 12_000,
    maxPatchLines: 60,
    maxNewFileChars: 12_000,
    maxNewFileLines: 160,
    maxFilesPerEdit: 2,
    maxBundleMutationBytes: 24_000,
    maxBundleChangedLines: 120,
  });

  const unlimitedRequest = Object.fromEntries([
    "DIRECT_MAX_MUTATION_CHARS",
    "DIRECT_MAX_PATCH_CHARS",
    "DIRECT_MAX_PATCH_LINES",
    "DIRECT_MAX_NEW_FILE_CHARS",
    "DIRECT_MAX_NEW_FILE_LINES",
    "DIRECT_MAX_FILES_PER_EDIT",
    "DIRECT_MAX_BUNDLE_MUTATION_BYTES",
    "DIRECT_MAX_BUNDLE_CHANGED_LINES",
  ].map((name) => [name, "999999999"]));
  assert.deepStrictEqual(resolveMutationLimits(unlimitedRequest), {
    maxMutationChars: HARD_MUTATION_LIMITS.maxPatchChars,
    ...HARD_MUTATION_LIMITS,
  });

  const reduced = resolveMutationLimits({
    DIRECT_MAX_PATCH_CHARS: "9000",
    DIRECT_MAX_NEW_FILE_LINES: "80",
    DIRECT_MAX_FILES_PER_EDIT: "1",
  });
  assert.strictEqual(reduced.maxPatchChars, 9000);
  assert.strictEqual(reduced.maxMutationChars, 9000);
  assert.strictEqual(reduced.maxNewFileLines, 80);
  assert.strictEqual(reduced.maxFilesPerEdit, 1);
});

test("new files and individual patches keep independent 12k/160 and 12k/60 bounds", () => {
  assert.doesNotThrow(() => validateBundleLimits({
    files: [{ path: "Config/New.ini", content: lines(160) }],
    patches: [],
  }));
  assert.throws(() => validateBundleLimits({
    files: [{ path: "Config/New.ini", content: lines(161) }],
    patches: [],
  }), /160 lines/u);

  assert.doesNotThrow(() => validateBundleLimits({
    files: [],
    patches: [patch("Config/One.ini", "x", lines(60))],
  }));
  assert.throws(() => validateBundleLimits({
    files: [],
    patches: [patch("Config/One.ini", "x", lines(61))],
  }), /60 changed lines/u);

  assert.doesNotThrow(() => validateBundleLimits({
    files: [],
    patches: [patch("Config/One.ini", "x", "n".repeat(11_999))],
  }));
  assert.throws(() => validateBundleLimits({
    files: [],
    patches: [patch("Config/One.ini", "xx", "n".repeat(11_999))],
  }), /12000 characters/u);
});

test("bundle limits hard-cap files plus aggregate patch lines and UTF-8 bytes", () => {
  assert.throws(() => validateBundleLimits({
    files: [
      { path: "Config/A.ini", content: "a" },
      { path: "Config/B.ini", content: "b" },
      { path: "Config/C.ini", content: "c" },
    ],
    patches: [],
  }, {
    maxFilesPerEdit: 999,
    maxBundleMutationBytes: 999_999,
    maxBundleChangedLines: 999_999,
  }), /too many files \(max 2\)/u);

  assert.doesNotThrow(() => validateBundleLimits({
    files: [],
    patches: [
      patch("Config/A.ini", "a", lines(60)),
      patch("Config/B.ini", "b", lines(60)),
    ],
  }));
  assert.throws(() => validateBundleLimits({
    files: [],
    patches: [
      patch("Config/A.ini", "a", lines(41)),
      patch("Config/A.ini", "b", lines(41)),
      patch("Config/B.ini", "c", lines(41)),
    ],
  }), /aggregate patches exceed 120 changed lines/u);

  assert.doesNotThrow(() => validateBundleLimits({
    files: [],
    patches: [
      patch("Config/A.ini", "a".repeat(4000), "b".repeat(4000)),
      patch("Config/A.ini", "c".repeat(4000), "d".repeat(4000)),
      patch("Config/B.ini", "e".repeat(4000), "f".repeat(4000)),
    ],
  }));
  assert.throws(() => validateBundleLimits({
    files: [],
    patches: [
      patch("Config/A.ini", "a".repeat(4000), "b".repeat(4000)),
      patch("Config/A.ini", "c".repeat(4000), "d".repeat(4000)),
      patch("Config/B.ini", "e".repeat(4000), `${"f".repeat(3998)}한`),
    ],
  }), /aggregate mutation payload exceeds 24000 bytes/u);
});

test("Direct handlers cannot bypass mutation hard caps through oversized env values", async (t) => {
  const oversizedEnv = Object.fromEntries([
    "DIRECT_MAX_MUTATION_CHARS",
    "DIRECT_MAX_PATCH_LINES",
    "DIRECT_MAX_NEW_FILE_LINES",
    "DIRECT_MAX_FILES_PER_EDIT",
    "DIRECT_MAX_BUNDLE_MUTATION_BYTES",
    "DIRECT_MAX_BUNDLE_CHANGED_LINES",
  ].map((name) => [name, "999999999"]));
  const { projectRoot, runtime, sourcePath } = runtimeFixture(t, oversizedEnv);

  assert.strictEqual(runtime.limits.maxPatchChars, 12_000);
  assert.strictEqual(runtime.limits.maxPatchLines, 60);
  assert.strictEqual(runtime.limits.maxFilesPerEdit, 2);

  const newFile = payloadOf(await runtime.callTool("write_file", {
    path: "project://Config/TooManyLines.ini",
    content: lines(161),
    createDirs: true,
  }));
  assert.strictEqual(newFile.errorCode, "MUTATION_TOO_LARGE");
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "TooManyLines.ini")), false);

  const source = fs.readFileSync(sourcePath, "utf8");
  const oversizedPatch = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Source/LimitsProject/Limits.cpp",
    oldText: "old",
    newText: lines(61, "replacement"),
    expectedOccurrences: 1,
    expectedHash: crypto.createHash("sha256").update(source).digest("hex"),
  }));
  assert.strictEqual(oversizedPatch.errorCode, "PATCH_TOO_LARGE");
  assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), source);

  const tooManyFiles = payloadOf(await runtime.callTool("apply_edit_bundle", {
    files: [
      { path: "Config/A.ini", content: "a" },
      { path: "Config/B.ini", content: "b" },
      { path: "Config/C.ini", content: "c" },
    ],
    patches: [],
  }));
  assert.strictEqual(tooManyFiles.errorCode, "BUNDLE_VALIDATION_FAILED");
  assert.match(tooManyFiles.message, /too many files \(max 2\)/u);
});
