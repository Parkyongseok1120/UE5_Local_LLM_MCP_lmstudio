"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {
  bundleMutationUsage,
  constrainMutationLimits,
  HARD_MUTATION_LIMITS,
  textLineCount,
} = require("./direct-mutation-limits");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");

const MAX_BUNDLE_OPERATIONS = HARD_MUTATION_LIMITS.maxBundleOperations;
const BUNDLE_FIELDS = new Set(["patches"]);
const PATCH_FIELDS = new Set([
  "path",
  "oldText",
  "newText",
  "expectedOccurrences",
  "expectedHash",
  "fileVersionReceipt",
]);

function assertFields(item, allowed, label) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    throw new Error(`${label} must be an object`);
  }
  for (const key of Object.keys(item)) {
    if (!allowed.has(key)) throw new Error(`${label} contains unsupported field: ${key}`);
  }
}

function normalizedBundlePath(item, label) {
  const relativePath = item.path.trim().replace(/\\/g, "/");
  if (!relativePath) throw new Error(`${label}.path must be non-empty`);
  return relativePath;
}

function bundlePaths(bundle) {
  return bundle.patches.map((item) => normalizedBundlePath(item, "patch"));
}

function assertOptionalString(item, field, label) {
  if (item[field] !== undefined && typeof item[field] !== "string") {
    throw new Error(`${label}.${field} must be a string when provided`);
  }
}

function assertPatchTypes(patch) {
  if (typeof patch.path !== "string" || !patch.path.trim()) {
    throw new Error("patch.path must be a non-empty string");
  }
  if (typeof patch.oldText !== "string" || !patch.oldText) {
    throw new Error(`patch.oldText must be a non-empty string: ${patch.path}`);
  }
  if (typeof patch.newText !== "string") {
    throw new Error(`patch.newText must be a string: ${patch.path}`);
  }
  if (!Number.isInteger(patch.expectedOccurrences) || patch.expectedOccurrences !== 1) {
    throw new Error(
      `apply_edit_bundle: expectedOccurrences must be the integer 1 for each focused patch: ${patch.path}`,
    );
  }
  assertOptionalString(patch, "expectedHash", "patch");
  assertOptionalString(patch, "fileVersionReceipt", "patch");
}

function validateBundleLimits(bundle, values = {}) {
  const mutationLimits = constrainMutationLimits(
    typeof values === "number" ? { maxFilesPerEdit: values } : values,
  );
  if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) {
    throw new Error("apply_edit_bundle payload must be an object");
  }
  for (const key of Object.keys(bundle)) {
    if (!BUNDLE_FIELDS.has(key)) {
      throw new Error(`apply_edit_bundle contains unsupported field: ${key}`);
    }
  }
  if (!Array.isArray(bundle.patches)) {
    throw new Error("apply_edit_bundle.patches must be an array");
  }
  const patches = bundle.patches;
  patches.forEach((item) => assertFields(item, PATCH_FIELDS, "patches[]"));
  patches.forEach(assertPatchTypes);
  if (!patches.length) throw new Error("apply_edit_bundle requires at least one patch");
  if (patches.length > mutationLimits.maxBundleOperations) {
    throw new Error(
      `apply_edit_bundle: too many patches (max ${mutationLimits.maxBundleOperations})`,
    );
  }
  for (const patch of patches) {
    const { oldText, newText } = patch;
    if (oldText.length > mutationLimits.maxPatchOldTextChars
      || newText.length > mutationLimits.maxPatchNewTextChars
      || oldText.length + newText.length > mutationLimits.maxPatchChars
      || textLineCount(newText) > mutationLimits.maxPatchLines) {
      throw new Error(
        `apply_edit_bundle: patch exceeds the focused-region limits of ${mutationLimits.maxPatchOldTextChars} oldText characters, ${mutationLimits.maxPatchNewTextChars} newText characters, ${mutationLimits.maxPatchChars} combined characters, or ${mutationLimits.maxPatchLines} newText lines: ${patch.path}`,
      );
    }
  }

  const relativePaths = bundlePaths({ patches });
  const unique = new Set(relativePaths);
  const patchPathList = patches.map((item) => normalizedBundlePath(item, "patch"));
  if (new Set(patchPathList).size !== patchPathList.length) {
    throw new Error("apply_edit_bundle: duplicate patches[] paths are not allowed; use one focused region per file and continue with the returned receipt in the next prediction round");
  }
  if (unique.size > mutationLimits.maxFilesPerEdit) {
    throw new Error(`apply_edit_bundle: too many files (max ${mutationLimits.maxFilesPerEdit})`);
  }
  const usage = bundleMutationUsage({ patches });
  if (usage.bytes > mutationLimits.maxBundleMutationBytes) {
    throw new Error(
      `apply_edit_bundle: aggregate mutation payload exceeds ${mutationLimits.maxBundleMutationBytes} bytes`,
    );
  }
  if (usage.changedLines > mutationLimits.maxBundleChangedLines) {
    throw new Error(
      `apply_edit_bundle: aggregate operations exceed ${mutationLimits.maxBundleChangedLines} changed lines`,
    );
  }
  return { limits: mutationLimits, usage };
}

function frozenAbsolutePathIdentity(value) {
  return canonicalAbsolutePathIdentity(value, process.platform, { realpath: false });
}

function identityIsWithinOrEqual(candidateIdentity, rootIdentity) {
  if (!candidateIdentity || !rootIdentity) return false;
  const separator = process.platform === "win32" ? "/" : path.sep;
  const prefix = rootIdentity.endsWith(separator)
    ? rootIdentity
    : `${rootIdentity}${separator}`;
  return candidateIdentity === rootIdentity || candidateIdentity.startsWith(prefix);
}

function requireExpectedIdentity(resolution, relativePath) {
  const expected = resolution?.expectedIdentity;
  if (!expected || typeof expected !== "object" || Array.isArray(expected)) {
    throw new Error(`apply_edit_bundle: initial target identity is required: ${relativePath}`);
  }
  for (const field of ["absolutePath", "realPath", "lexicalRoot", "allowedRealRoot"]) {
    if (typeof expected[field] !== "string" || !expected[field]) {
      throw new Error(`apply_edit_bundle: invalid initial target identity field ${field}: ${relativePath}`);
    }
  }
  return expected;
}

async function canonicalizeBundleTargets(bundle, resolvePath, mutationLimits) {
  validateBundleLimits(bundle, mutationLimits);
  const relativePaths = [...new Set(bundlePaths(bundle))];
  const targets = new Map();
  const canonicalKeys = new Map();
  for (const relativePath of relativePaths) {
    const resolution = await resolvePath(relativePath);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid bundle path: ${relativePath}`);
    }
    const expected = requireExpectedIdentity(resolution, relativePath);
    const resolvedAbsolutePath = path.resolve(resolution.absolutePath);
    if (frozenAbsolutePathIdentity(resolvedAbsolutePath) !== expected.absolutePath) {
      throw new Error(`Bundle lexical target changed before lock acquisition: ${relativePath}`);
    }
    const currentRealRoot = fs.realpathSync.native
      ? fs.realpathSync.native(expected.lexicalRoot)
      : fs.realpathSync(expected.lexicalRoot);
    if (frozenAbsolutePathIdentity(currentRealRoot) !== expected.allowedRealRoot) {
      throw new Error(`Bundle containment root identity changed before lock acquisition: ${relativePath}`);
    }
    const absolutePath = fs.realpathSync.native
      ? fs.realpathSync.native(resolvedAbsolutePath)
      : fs.realpathSync(resolvedAbsolutePath);
    const canonicalKey = frozenAbsolutePathIdentity(absolutePath);
    if (canonicalKey !== expected.realPath) {
      throw new Error(`Bundle target identity changed before lock acquisition: ${relativePath}`);
    }
    if (!identityIsWithinOrEqual(canonicalKey, expected.allowedRealRoot)) {
      throw new Error(`Bundle target escaped its validated containment root: ${relativePath}`);
    }
    const alias = canonicalKeys.get(canonicalKey);
    if (alias && alias !== relativePath) {
      throw new Error(`apply_edit_bundle: alias paths resolve to same file: ${relativePath} and ${alias}`);
    }
    canonicalKeys.set(canonicalKey, relativePath);
    targets.set(relativePath, { relativePath, absolutePath, canonicalKey });
  }
  return { relativePaths, targets };
}

module.exports = {
  MAX_BUNDLE_OPERATIONS,
  bundlePaths,
  canonicalizeBundleTargets,
  normalizedBundlePath,
  validateBundleLimits,
};
