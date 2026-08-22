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

function treeSnapshot(root) {
  if (!fs.existsSync(root)) return [];
  const entries = [];
  function visit(current, relative = "") {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
      const child = path.join(current, entry.name);
      if (entry.isDirectory()) {
        entries.push([childRelative, "directory"]);
        visit(child, childRelative);
      } else if (entry.isSymbolicLink()) {
        entries.push([childRelative, "link", fs.readlinkSync(child)]);
      } else {
        entries.push([childRelative, "file", fs.readFileSync(child).toString("base64")]);
      }
    }
  }
  visit(root);
  return entries;
}

function runtimeFixture(t, env = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-mutation-limits-"));
  const projectRoot = path.join(root, "LimitsProject");
  const projectPath = path.join(projectRoot, "LimitsProject.uproject");
  const sourcePath = path.join(projectRoot, "Source", "LimitsProject", "Limits.cpp");
  const stateRoot = path.join(root, "state");
  fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
  fs.writeFileSync(projectPath, JSON.stringify({ FileVersion: 3 }), "utf8");
  fs.writeFileSync(sourcePath, "old\n", "utf8");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    root,
    projectRoot,
    projectPath,
    sourcePath,
    stateRoot,
    runtime: createDirectRuntime({
      workspaceRoot: root,
      stateRoot,
      configPath: path.join(root, "agent-mcp.json"),
      getActiveProject: () => projectPath,
      env: {
        AGENT_STATE_ROOT: stateRoot,
        ALLOW_WRITE: "1",
        ...env,
      },
    }),
  };
}

test("Direct mutation limits restore bounded defaults and hard-clamp environment overrides", () => {
  assert.deepStrictEqual(resolveMutationLimits({}), {
    maxMutationChars: 4_000,
    maxPatchOldTextChars: 1_200,
    maxPatchNewTextChars: 2_800,
    maxPatchChars: 4_000,
    maxPatchLines: 32,
    maxNewFileChars: 12_000,
    maxNewFileLines: 160,
    maxFilesPerEdit: 2,
    maxBundleOperations: 2,
    maxBundleMutationBytes: 24_000,
    maxBundleChangedLines: 64,
  });

  const unlimitedRequest = Object.fromEntries([
    "DIRECT_MAX_MUTATION_CHARS",
    "DIRECT_MAX_PATCH_OLD_TEXT_CHARS",
    "DIRECT_MAX_PATCH_NEW_TEXT_CHARS",
    "DIRECT_MAX_PATCH_CHARS",
    "DIRECT_MAX_PATCH_LINES",
    "DIRECT_MAX_NEW_FILE_CHARS",
    "DIRECT_MAX_NEW_FILE_LINES",
    "DIRECT_MAX_FILES_PER_EDIT",
    "DIRECT_MAX_BUNDLE_OPERATIONS",
    "DIRECT_MAX_BUNDLE_MUTATION_BYTES",
    "DIRECT_MAX_BUNDLE_CHANGED_LINES",
  ].map((name) => [name, "999999999"]));
  assert.deepStrictEqual(resolveMutationLimits(unlimitedRequest), {
    maxMutationChars: HARD_MUTATION_LIMITS.maxPatchChars,
    ...HARD_MUTATION_LIMITS,
  });

  const reduced = resolveMutationLimits({
    DIRECT_MAX_PATCH_OLD_TEXT_CHARS: "1000",
    DIRECT_MAX_PATCH_NEW_TEXT_CHARS: "2500",
    DIRECT_MAX_PATCH_CHARS: "3000",
    DIRECT_MAX_NEW_FILE_LINES: "80",
    DIRECT_MAX_FILES_PER_EDIT: "1",
    DIRECT_MAX_BUNDLE_OPERATIONS: "1",
  });
  assert.strictEqual(reduced.maxPatchOldTextChars, 1000);
  assert.strictEqual(reduced.maxPatchNewTextChars, 2500);
  assert.strictEqual(reduced.maxPatchChars, 3000);
  assert.strictEqual(reduced.maxMutationChars, 3000);
  assert.strictEqual(reduced.maxNewFileLines, 80);
  assert.strictEqual(reduced.maxFilesPerEdit, 1);
  assert.strictEqual(reduced.maxBundleOperations, 1);
});

test("focused bundle patches enforce strict per-field character and line bounds", () => {
  assert.doesNotThrow(() => validateBundleLimits({
    patches: [patch("Config/One.ini", "x".repeat(1_200), "n".repeat(2_800))],
  }));
  assert.throws(() => validateBundleLimits({
    patches: [patch("Config/One.ini", "x".repeat(1_201), "n")],
  }), /1200 oldText characters/u);
  assert.throws(() => validateBundleLimits({
    patches: [patch("Config/One.ini", "x", "n".repeat(2_801))],
  }), /2800 newText characters/u);
  assert.throws(() => validateBundleLimits({
    patches: [patch("Config/One.ini", "x", lines(33))],
  }), /32 newText lines/u);

  assert.throws(() => validateBundleLimits({
    patches: [patch("Config/One.ini", "x".repeat(1_200), "n".repeat(2_800))],
  }, { maxPatchChars: 3_999 }), /3999 combined characters/u);
  assert.throws(() => validateBundleLimits({
    patches: [{ ...patch("Config/One.ini", "x", "n"), expectedOccurrences: 2 }],
  }), /expectedOccurrences must be the integer 1/u);
  assert.throws(() => validateBundleLimits({
    files: [{ path: "Config/New.ini", content: "x" }],
    patches: [patch("Config/One.ini", "x", "n")],
  }), /unsupported field: files/u);
});

test("bundle limits cap total operations and forbid repeated patch paths", () => {
  assert.throws(() => validateBundleLimits({
    patches: [
      patch("Config/A.ini", "a", "one"),
      patch("Config/B.ini", "b", "two"),
      patch("Config/C.ini", "c", "three"),
    ],
  }, {
    maxFilesPerEdit: 999,
    maxBundleOperations: 999,
    maxBundleMutationBytes: 999_999,
    maxBundleChangedLines: 999_999,
  }), /too many patches \(max 2\)/u);

  assert.throws(() => validateBundleLimits({
    patches: Array.from({ length: 8 }, (_, index) => (
      patch("Config/A.ini", `old-${index}`, `new-${index}`)
    )),
  }), /too many patches \(max 2\)/u);

  assert.throws(() => validateBundleLimits({
    patches: [
      patch("Config/A.ini", "a", "first"),
      patch("Config/A.ini", "b", "second"),
    ],
  }), /duplicate patches\[\] paths are not allowed/u);

  assert.doesNotThrow(() => validateBundleLimits({
    patches: [
      patch("Config/A.ini", "a", lines(32)),
      patch("Config/B.ini", "b", lines(32)),
    ],
  }));
  assert.throws(() => validateBundleLimits({
    patches: [
      patch("Config/A.ini", "a", lines(32)),
      patch("Config/B.ini", "b", lines(32)),
    ],
  }, { maxBundleChangedLines: 63 }), /aggregate operations exceed 63 changed lines/u);
});

test("bundle path normalization is identical from public validation through commit", async (t) => {
  const { runtime, sourcePath } = runtimeFixture(t);
  const before = fs.readFileSync(sourcePath, "utf8");
  const result = payloadOf(await runtime.callTool("apply_edit_bundle", {
    patches: [{
      path: "  project://Source/LimitsProject/Limits.cpp  ",
      oldText: "old",
      newText: "new",
      expectedOccurrences: 1,
      expectedHash: crypto.createHash("sha256").update(before).digest("hex"),
    }],
  }));

  assert.strictEqual(result.ok, true);
  assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), "new\n");
  assert.strictEqual(result.files[0].path, "project://Source/LimitsProject/Limits.cpp");
});

test("Direct handlers cannot bypass mutation hard caps through oversized env values", async (t) => {
  const oversizedEnv = Object.fromEntries([
    "DIRECT_MAX_MUTATION_CHARS",
    "DIRECT_MAX_PATCH_OLD_TEXT_CHARS",
    "DIRECT_MAX_PATCH_NEW_TEXT_CHARS",
    "DIRECT_MAX_PATCH_LINES",
    "DIRECT_MAX_NEW_FILE_LINES",
    "DIRECT_MAX_FILES_PER_EDIT",
    "DIRECT_MAX_BUNDLE_OPERATIONS",
    "DIRECT_MAX_BUNDLE_MUTATION_BYTES",
    "DIRECT_MAX_BUNDLE_CHANGED_LINES",
  ].map((name) => [name, "999999999"]));
  const { projectRoot, runtime, sourcePath, stateRoot } = runtimeFixture(t, oversizedEnv);

  assert.strictEqual(runtime.limits.maxPatchOldTextChars, 1_200);
  assert.strictEqual(runtime.limits.maxPatchNewTextChars, 2_800);
  assert.strictEqual(runtime.limits.maxPatchChars, 4_000);
  assert.strictEqual(runtime.limits.maxPatchLines, 32);
  assert.strictEqual(runtime.limits.maxFilesPerEdit, 2);
  assert.strictEqual(runtime.limits.maxBundleOperations, 2);

  const newFile = payloadOf(await runtime.callTool("write_file", {
    path: "project://Config/TooManyLines.ini",
    content: lines(161),
    createDirs: true,
  }));
  assert.strictEqual(newFile.errorCode, "MUTATION_TOO_LARGE");
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "TooManyLines.ini")), false);

  const source = fs.readFileSync(sourcePath, "utf8");
  const invalidPatches = [
    { oldText: "o".repeat(1_201), newText: "replacement", expectedOccurrences: 1, errorCode: "PATCH_TOO_LARGE" },
    { oldText: "old", newText: "n".repeat(2_801), expectedOccurrences: 1, errorCode: "PATCH_TOO_LARGE" },
    { oldText: "old", newText: lines(33, "replacement"), expectedOccurrences: 1, errorCode: "PATCH_TOO_LARGE" },
    { oldText: "old", newText: "replacement", expectedOccurrences: 2, errorCode: "INVALID_ARGUMENT" },
  ];
  for (const invalid of invalidPatches) {
    const rejected = payloadOf(await runtime.callTool("replace_in_file", {
      path: "project://Source/LimitsProject/Limits.cpp",
      oldText: invalid.oldText,
      newText: invalid.newText,
      expectedOccurrences: invalid.expectedOccurrences,
      expectedHash: crypto.createHash("sha256").update(source).digest("hex"),
    }));
    assert.strictEqual(rejected.errorCode, invalid.errorCode);
    assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), source);
  }

  const stateBeforeDeprecatedCreate = treeSnapshot(stateRoot);
  const deprecatedCreate = payloadOf(await runtime.callTool("apply_edit_bundle", {
    files: [{ path: "Config/A.ini", content: "a" }],
  }));
  assert.strictEqual(deprecatedCreate.errorCode, "INVALID_ARGUMENT");
  assert.match(deprecatedCreate.message, /unsupported argument\(s\): files/u);
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "A.ini")), false);
  assert.deepStrictEqual(treeSnapshot(stateRoot), stateBeforeDeprecatedCreate);

  const duplicatePath = payloadOf(await runtime.callTool("apply_edit_bundle", {
    patches: [
      patch("Source/LimitsProject/Limits.cpp", "old", "first"),
      patch("Source/LimitsProject/Limits.cpp", "old", "second"),
    ],
  }));
  assert.strictEqual(duplicatePath.errorCode, "BUNDLE_VALIDATION_FAILED");
  assert.match(duplicatePath.message, /duplicate patches\[\] paths/u);
  assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), source);

  const invalidOccurrences = payloadOf(await runtime.callTool("apply_edit_bundle", {
    patches: [{
      ...patch("Source/LimitsProject/Limits.cpp", "old", "replacement"),
      expectedOccurrences: 2,
    }],
  }));
  assert.strictEqual(invalidOccurrences.errorCode, "BUNDLE_VALIDATION_FAILED");
  assert.match(invalidOccurrences.message, /expectedOccurrences must be the integer 1/u);
  assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), source);
});

test("raw mutation handlers reject non-schema types without coercion or writes", async (t) => {
  const {
    projectPath,
    projectRoot,
    runtime,
    sourcePath,
    stateRoot,
  } = runtimeFixture(t);
  const source = fs.readFileSync(sourcePath, "utf8");
  const expectedHash = crypto.createHash("sha256").update(source).digest("hex");
  const projectBefore = treeSnapshot(projectRoot);
  const stateBefore = treeSnapshot(stateRoot);
  const validReplace = {
    path: "project://Source/LimitsProject/Limits.cpp",
    oldText: "old",
    newText: "replacement",
    expectedOccurrences: 1,
    expectedHash,
  };
  for (const malformed of [
    { ...validReplace, path: [validReplace.path] },
    { ...validReplace, oldText: [validReplace.oldText] },
    { ...validReplace, newText: [] },
    { ...validReplace, expectedOccurrences: [1] },
    { ...validReplace, expectedHash: [expectedHash] },
    { ...validReplace, fileVersionReceipt: ["fvr1_not-a-string-argument"] },
    { ...validReplace, project: [projectPath] },
  ]) {
    const result = payloadOf(await runtime.callTool("replace_in_file", malformed));
    assert.strictEqual(result.errorCode, "INVALID_ARGUMENT");
    assert.strictEqual(fs.readFileSync(sourcePath, "utf8"), source);
  }

  const validWrite = {
    path: "project://Config/MalformedWrite.ini",
    content: "Value=1\n",
    createDirs: true,
  };
  for (const malformed of [
    { ...validWrite, path: [validWrite.path] },
    { ...validWrite, content: [validWrite.content] },
    { ...validWrite, createDirs: "true" },
    { ...validWrite, project: { path: projectPath } },
  ]) {
    const result = payloadOf(await runtime.callTool("write_file", malformed));
    assert.strictEqual(result.errorCode, "INVALID_ARGUMENT");
  }
  assert.deepStrictEqual(treeSnapshot(projectRoot), projectBefore);
  assert.deepStrictEqual(treeSnapshot(stateRoot), stateBefore);
});

test("one malformed bundle sibling leaves every file and journal byte-for-byte unchanged", async (t) => {
  const { projectRoot, runtime, sourcePath, stateRoot } = runtimeFixture(t);
  const siblingPath = path.join(projectRoot, "Source", "LimitsProject", "Sibling.cpp");
  fs.writeFileSync(siblingPath, "sibling\n", "utf8");
  const source = fs.readFileSync(sourcePath, "utf8");
  const sibling = fs.readFileSync(siblingPath, "utf8");
  const projectBefore = treeSnapshot(projectRoot);
  const stateBefore = treeSnapshot(stateRoot);

  const validPatch = {
    path: "project://Source/LimitsProject/Limits.cpp",
    oldText: "old",
    newText: "valid",
    expectedOccurrences: 1,
    expectedHash: crypto.createHash("sha256").update(source).digest("hex"),
  };
  const siblingPatch = {
    path: "project://Source/LimitsProject/Sibling.cpp",
    oldText: "sibling",
    newText: "changed",
    expectedOccurrences: 1,
    expectedHash: crypto.createHash("sha256").update(sibling).digest("hex"),
  };
  for (const malformedSibling of [
    { ...siblingPatch, path: [siblingPatch.path] },
    { ...siblingPatch, oldText: [siblingPatch.oldText] },
    { ...siblingPatch, newText: [] },
    { ...siblingPatch, expectedOccurrences: [1] },
    { ...siblingPatch, expectedHash: [siblingPatch.expectedHash] },
    { ...siblingPatch, fileVersionReceipt: ["fvr1_array"] },
  ]) {
    const result = payloadOf(await runtime.callTool("apply_edit_bundle", {
      patches: [validPatch, malformedSibling],
    }));
    assert.strictEqual(result.errorCode, "BUNDLE_VALIDATION_FAILED");
    assert.deepStrictEqual(treeSnapshot(projectRoot), projectBefore);
    assert.deepStrictEqual(treeSnapshot(stateRoot), stateBefore);
  }

  const invalidProject = payloadOf(await runtime.callTool("apply_edit_bundle", {
    project: [path.join(projectRoot, "LimitsProject.uproject")],
    patches: [validPatch],
  }));
  assert.strictEqual(invalidProject.errorCode, "INVALID_ARGUMENT");
  assert.deepStrictEqual(treeSnapshot(projectRoot), projectBefore);
  assert.deepStrictEqual(treeSnapshot(stateRoot), stateBefore);
});
