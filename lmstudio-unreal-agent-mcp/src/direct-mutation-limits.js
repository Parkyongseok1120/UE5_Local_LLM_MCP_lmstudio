"use strict";

const HARD_MUTATION_LIMITS = Object.freeze({
  maxPatchChars: 12_000,
  maxPatchLines: 60,
  maxNewFileChars: 12_000,
  maxNewFileLines: 160,
  maxFilesPerEdit: 2,
  maxBundleMutationBytes: 24_000,
  maxBundleChangedLines: 120,
});

function hardBound(value, fallback, hardMaximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.max(1, Math.min(hardMaximum, Math.trunc(parsed)));
}

function constrainMutationLimits(values = {}) {
  const maxPatchChars = hardBound(
    values.maxPatchChars ?? values.maxMutationChars,
    HARD_MUTATION_LIMITS.maxPatchChars,
    HARD_MUTATION_LIMITS.maxPatchChars,
  );
  return Object.freeze({
    maxMutationChars: maxPatchChars,
    maxPatchChars,
    maxPatchLines: hardBound(
      values.maxPatchLines,
      HARD_MUTATION_LIMITS.maxPatchLines,
      HARD_MUTATION_LIMITS.maxPatchLines,
    ),
    maxNewFileChars: hardBound(
      values.maxNewFileChars,
      HARD_MUTATION_LIMITS.maxNewFileChars,
      HARD_MUTATION_LIMITS.maxNewFileChars,
    ),
    maxNewFileLines: hardBound(
      values.maxNewFileLines,
      HARD_MUTATION_LIMITS.maxNewFileLines,
      HARD_MUTATION_LIMITS.maxNewFileLines,
    ),
    maxFilesPerEdit: hardBound(
      values.maxFilesPerEdit,
      HARD_MUTATION_LIMITS.maxFilesPerEdit,
      HARD_MUTATION_LIMITS.maxFilesPerEdit,
    ),
    maxBundleMutationBytes: hardBound(
      values.maxBundleMutationBytes,
      HARD_MUTATION_LIMITS.maxBundleMutationBytes,
      HARD_MUTATION_LIMITS.maxBundleMutationBytes,
    ),
    maxBundleChangedLines: hardBound(
      values.maxBundleChangedLines,
      HARD_MUTATION_LIMITS.maxBundleChangedLines,
      HARD_MUTATION_LIMITS.maxBundleChangedLines,
    ),
  });
}

function resolveMutationLimits(env = {}) {
  return constrainMutationLimits({
    maxPatchChars: env.DIRECT_MAX_PATCH_CHARS ?? env.DIRECT_MAX_MUTATION_CHARS,
    maxPatchLines: env.DIRECT_MAX_PATCH_LINES,
    maxNewFileChars: env.DIRECT_MAX_NEW_FILE_CHARS ?? env.DIRECT_MAX_MUTATION_CHARS,
    maxNewFileLines: env.DIRECT_MAX_NEW_FILE_LINES,
    maxFilesPerEdit: env.DIRECT_MAX_FILES_PER_EDIT,
    maxBundleMutationBytes: env.DIRECT_MAX_BUNDLE_MUTATION_BYTES,
    maxBundleChangedLines: env.DIRECT_MAX_BUNDLE_CHANGED_LINES,
  });
}

function textLineCount(value) {
  return String(value ?? "").split(/\r?\n/u).length;
}

function bundleMutationUsage(bundle) {
  let bytes = 0;
  let changedLines = 0;
  for (const file of bundle?.files || []) {
    bytes += Buffer.byteLength(String(file?.content ?? ""), "utf8");
  }
  for (const patch of bundle?.patches || []) {
    bytes += Buffer.byteLength(String(patch?.oldText ?? ""), "utf8");
    bytes += Buffer.byteLength(String(patch?.newText ?? ""), "utf8");
    changedLines += textLineCount(patch?.newText);
  }
  return { bytes, changedLines };
}

module.exports = {
  HARD_MUTATION_LIMITS,
  bundleMutationUsage,
  constrainMutationLimits,
  resolveMutationLimits,
  textLineCount,
};
