"use strict";

const HARD_MUTATION_LIMITS = Object.freeze({
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

function hardBound(value, fallback, hardMaximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.max(1, Math.min(hardMaximum, Math.trunc(parsed)));
}

function constrainMutationLimits(values = {}) {
  const maxPatchOldTextChars = hardBound(
    values.maxPatchOldTextChars,
    HARD_MUTATION_LIMITS.maxPatchOldTextChars,
    HARD_MUTATION_LIMITS.maxPatchOldTextChars,
  );
  const maxPatchNewTextChars = hardBound(
    values.maxPatchNewTextChars,
    HARD_MUTATION_LIMITS.maxPatchNewTextChars,
    HARD_MUTATION_LIMITS.maxPatchNewTextChars,
  );
  const maxPatchChars = hardBound(
    values.maxPatchChars ?? values.maxMutationChars,
    HARD_MUTATION_LIMITS.maxPatchChars,
    HARD_MUTATION_LIMITS.maxPatchChars,
  );
  return Object.freeze({
    maxMutationChars: maxPatchChars,
    maxPatchOldTextChars,
    maxPatchNewTextChars,
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
    maxBundleOperations: hardBound(
      values.maxBundleOperations,
      HARD_MUTATION_LIMITS.maxBundleOperations,
      HARD_MUTATION_LIMITS.maxBundleOperations,
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
    maxPatchOldTextChars: env.DIRECT_MAX_PATCH_OLD_TEXT_CHARS,
    maxPatchNewTextChars: env.DIRECT_MAX_PATCH_NEW_TEXT_CHARS,
    maxPatchChars: env.DIRECT_MAX_PATCH_CHARS ?? env.DIRECT_MAX_MUTATION_CHARS,
    maxPatchLines: env.DIRECT_MAX_PATCH_LINES,
    maxNewFileChars: env.DIRECT_MAX_NEW_FILE_CHARS ?? env.DIRECT_MAX_MUTATION_CHARS,
    maxNewFileLines: env.DIRECT_MAX_NEW_FILE_LINES,
    maxFilesPerEdit: env.DIRECT_MAX_FILES_PER_EDIT,
    maxBundleOperations: env.DIRECT_MAX_BUNDLE_OPERATIONS,
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
  for (const patch of bundle?.patches || []) {
    bytes += Buffer.byteLength(patch.oldText, "utf8");
    bytes += Buffer.byteLength(patch.newText, "utf8");
    changedLines += textLineCount(patch.newText);
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
