"use strict";

// Conversation 1794419304890: 39-, 44- and 52-line newText (798–1,405 chars)
// and a 1,235-char oldText were refused although each fit the 4,000-char
// region budget. newText lines rise to 64 (bundles to 128 so one region does
// not use a whole bundle). Copied oldText stays near its old size, since long
// copies fail on whitespace; replacing proven lines by range needs no copy,
// so a range may cover 2,400 characters.
const HARD_MUTATION_LIMITS = Object.freeze({
  maxPatchOldTextChars: 1_500,
  maxRangeTextChars: 2_400,
  maxPatchNewTextChars: 2_800,
  maxPatchChars: 4_000,
  maxPatchLines: 64,
  maxNewFileChars: 12_000,
  maxNewFileLines: 160,
  maxFilesPerEdit: 2,
  maxBundleOperations: 2,
  maxBundleMutationBytes: 24_000,
  maxBundleChangedLines: 128,
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
    maxRangeTextChars: hardBound(
      values.maxRangeTextChars,
      HARD_MUTATION_LIMITS.maxRangeTextChars,
      HARD_MUTATION_LIMITS.maxRangeTextChars,
    ),
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
    maxRangeTextChars: env.DIRECT_MAX_RANGE_TEXT_CHARS,
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

// A final terminator ends the last line; it does not start another one, so
// a 64-line block ending in "\n" is 64 lines, not 65.
function textLineCount(value) {
  const lines = String(value ?? "").split(/\r?\n/u);
  return lines.length > 1 && lines.at(-1) === "" ? lines.length - 1 : lines.length;
}

// Which per-region limits one edit region exceeds, with the actual values, so
// a rejection names the limit to fix instead of listing all of them.
function regionLimitsExceeded({ oldText = "", newText = "" }, limits) {
  const before = String(oldText ?? ""), after = String(newText ?? "");
  return [
    ["oldText characters", before.length, limits.maxPatchOldTextChars],
    ["newText characters", after.length, limits.maxPatchNewTextChars],
    ["combined characters", before.length + after.length, limits.maxPatchChars],
    ["newText lines", textLineCount(after), limits.maxPatchLines],
  ].filter(([, actual, max]) => actual > max).map(([limit, actual, max]) => ({ limit, actual, max }));
}

function describeExceeded(exceeded) {
  return exceeded.map(({ limit, actual, max }) => `${limit} ${actual} > ${max}`).join(", ");
}

function bundleMutationUsage(bundle) {
  let bytes = 0;
  let changedLines = 0;
  for (const patch of bundle?.patches || []) {
    for (const edit of patch.edits || [patch]) {
      // A line-range patch carries no oldText; its proven range is checked after the read.
      bytes += Buffer.byteLength(edit.oldText ?? "", "utf8");
      bytes += Buffer.byteLength(edit.newText, "utf8");
      changedLines += textLineCount(edit.newText);
    }
  }
  return { bytes, changedLines };
}

module.exports = {
  HARD_MUTATION_LIMITS,
  bundleMutationUsage,
  constrainMutationLimits,
  describeExceeded,
  regionLimitsExceeded,
  resolveMutationLimits,
  textLineCount,
};
