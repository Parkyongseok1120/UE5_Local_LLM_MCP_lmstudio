"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {
  bundleMutationUsage,
  constrainMutationLimits,
  textLineCount,
} = require("./direct-mutation-limits");
const { canonicalLockKey } = require("./write-locks");

const MAX_BUNDLE_OPERATIONS = 128;
const BUNDLE_FIELDS = new Set(["files", "patches"]);
const FILE_FIELDS = new Set(["path", "content"]);
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
  const relativePath = String(item?.path || "").trim().replace(/\\/g, "/");
  if (!relativePath) throw new Error(`${label}.path must be non-empty`);
  return relativePath;
}

function bundlePaths(bundle) {
  return [
    ...(bundle?.patches || []).map((item) => normalizedBundlePath(item, "patch")),
    ...(bundle?.files || []).map((item) => normalizedBundlePath(item, "file")),
  ];
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
  const files = Array.isArray(bundle.files) ? bundle.files : [];
  const patches = Array.isArray(bundle.patches) ? bundle.patches : [];
  files.forEach((item) => assertFields(item, FILE_FIELDS, "files[]"));
  patches.forEach((item) => assertFields(item, PATCH_FIELDS, "patches[]"));
  for (const file of files) {
    const content = String(file.content ?? "");
    if (content.length > mutationLimits.maxNewFileChars
      || textLineCount(content) > mutationLimits.maxNewFileLines) {
      throw new Error(
        `apply_edit_bundle: new file exceeds ${mutationLimits.maxNewFileChars} characters or ${mutationLimits.maxNewFileLines} lines: ${file.path}`,
      );
    }
  }
  for (const patch of patches) {
    const oldText = String(patch.oldText ?? "");
    const newText = String(patch.newText ?? "");
    if (oldText.length + newText.length > mutationLimits.maxPatchChars
      || textLineCount(newText) > mutationLimits.maxPatchLines) {
      throw new Error(
        `apply_edit_bundle: patch exceeds ${mutationLimits.maxPatchChars} characters or ${mutationLimits.maxPatchLines} changed lines: ${patch.path}`,
      );
    }
  }
  const operations = [...patches, ...files];
  if (!operations.length) throw new Error("apply_edit_bundle requires at least one operation");
  if (operations.length > MAX_BUNDLE_OPERATIONS) {
    throw new Error(`apply_edit_bundle: too many operations (max ${MAX_BUNDLE_OPERATIONS})`);
  }

  const relativePaths = bundlePaths({ files, patches });
  const unique = new Set(relativePaths);
  const filePaths = files.map((item) => normalizedBundlePath(item, "file"));
  if (new Set(filePaths).size !== filePaths.length) {
    throw new Error("apply_edit_bundle: duplicate files[] paths are not allowed");
  }
  const patchPaths = new Set(patches.map((item) => normalizedBundlePath(item, "patch")));
  const mixedPath = filePaths.find((relativePath) => patchPaths.has(relativePath));
  if (mixedPath) {
    throw new Error(`apply_edit_bundle: ${mixedPath} cannot appear in both files[] and patches[]`);
  }
  if (unique.size > mutationLimits.maxFilesPerEdit) {
    throw new Error(`apply_edit_bundle: too many files (max ${mutationLimits.maxFilesPerEdit})`);
  }
  const usage = bundleMutationUsage({ files, patches });
  if (usage.bytes > mutationLimits.maxBundleMutationBytes) {
    throw new Error(
      `apply_edit_bundle: aggregate mutation payload exceeds ${mutationLimits.maxBundleMutationBytes} bytes`,
    );
  }
  if (usage.changedLines > mutationLimits.maxBundleChangedLines) {
    throw new Error(
      `apply_edit_bundle: aggregate patches exceed ${mutationLimits.maxBundleChangedLines} changed lines`,
    );
  }
  return { limits: mutationLimits, usage };
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
    let absolutePath = path.resolve(resolution.absolutePath);
    try {
      absolutePath = fs.realpathSync.native
        ? fs.realpathSync.native(absolutePath)
        : fs.realpathSync(absolutePath);
    } catch {
      // A create target keeps its lexical absolute path.
    }
    const canonicalKey = canonicalLockKey(absolutePath);
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
  validateBundleLimits,
};
