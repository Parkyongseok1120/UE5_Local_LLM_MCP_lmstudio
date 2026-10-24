"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const {
  assertMutationActive,
  calculatePatch,
  createExclusive,
  isLineRangePatch,
  replaceWithCAS,
  sha256Text,
} = require("./safe-write");
const { validateWriteTarget } = require("./write-guards");
const { withPathLock } = require("./write-locks");
const { readStableTextFile } = require("./direct-file-snapshot");
const { textLineCount } = require("./direct-mutation-limits");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");
const { mutationSemanticAdvisory } = require("./mutation-semantic-guard");
const { failure, success } = require("./direct-response");
const { committedChange, withChangeEvidence } = require("./direct-change-evidence");
const { locateOldText, regionSuggestion } = require("./edit-region-locator");
const {
  registerCurrentVersion,
  resolveVersionEvidence,
  versionConflict,
  versionEvidenceFailure,
} = require("./direct-file-version-policy.js");
const {
  envFlag,
  statOrNull,
} = require("./direct-runtime-shared");

const { replaceFileArgumentError, writeFileArgumentError } = require("./direct-mutation-arguments");

function frozenMutationTarget(resolution) {
  const identity = (value) => canonicalAbsolutePathIdentity(
    value,
    process.platform,
    { realpath: false },
  );
  return {
    absolutePath: identity(resolution.absolutePath),
    realPath: identity(resolution.realPath),
    allowedRealRoot: identity(resolution.allowedRealRoot),
  };
}

function sameMutationTarget(initialIdentity, refreshed) {
  const refreshedIdentity = frozenMutationTarget(refreshed);
  return refreshedIdentity.absolutePath === initialIdentity.absolutePath
    && refreshedIdentity.realPath === initialIdentity.realPath
    && refreshedIdentity.allowedRealRoot === initialIdentity.allowedRealRoot;
}

function createFileMutationCapabilities(context) {
  const {
    env,
    limits,
    mutationResolution,
    options,
    projectScopedSuggestionArgs,
    stateRoot,
    workspaceRoot,
  } = context;
  const writesAllowed = () => envFlag(env, "ALLOW_WRITE", false);

  async function writeFile(args, requestContext = {}) {
    if (!writesAllowed()) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const argumentError = writeFileArgumentError(args);
    if (argumentError) {
      return failure("INVALID_ARGUMENT", argumentError, {
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    const content = args.content;
    if (content.length > limits.maxNewFileChars
      || textLineCount(content) > limits.maxNewFileLines) {
      return failure(
        "MUTATION_TOO_LARGE",
        `New file exceeds ${limits.maxNewFileChars} characters or ${limits.maxNewFileLines} lines.`,
        {
          retryAllowed: true,
          retryMode: "different_arguments",
        },
      );
    }
    assertMutationActive(requestContext.signal);
    const resolution = await mutationResolution(args.path, args.project);
    const targetIdentity = frozenMutationTarget(resolution);
    const guard = await validateWriteTarget({
      targetAbsPath: resolution.absolutePath,
      workspaceRoot,
      activeProjectPath: resolution.activeProject,
      createDirs: args.createDirs === true,
      fileExists: async (target) => Boolean(await statOrNull(target)),
      allowExistingWrite: false,
    });
    if (!guard.ok) return failure("WRITE_TARGET_BLOCKED", guard.message);
    const semanticAdvisory = mutationSemanticAdvisory(
      resolution.absolutePath,
      content,
      options.validateMutationSemanticText,
    );
    let locked;
    try {
      locked = await withPathLock(resolution.absolutePath, `${context.runtimeOwner}_write_file`, async () => {
        const refreshed = await mutationResolution(args.path, args.project);
        if (!sameMutationTarget(targetIdentity, refreshed)) {
          throw new Error("write_file real target or containment root changed during locked revalidation");
        }
        const refreshedGuard = await validateWriteTarget({
          targetAbsPath: refreshed.absolutePath,
          workspaceRoot,
          activeProjectPath: refreshed.activeProject,
          createDirs: args.createDirs === true,
          fileExists: async (target) => Boolean(await statOrNull(target)),
          allowExistingWrite: false,
        });
        if (!refreshedGuard.ok) throw new Error(refreshedGuard.message);
        assertMutationActive(requestContext.signal);
        if (args.createDirs === true) {
          await fsp.mkdir(path.dirname(refreshed.absolutePath), { recursive: true });
        }
        return createExclusive(refreshed.absolutePath, content, requestContext.signal);
      }, { stateRoot });
    } catch (error) {
      return failure(
        error.code === "REQUEST_CANCELLED" ? error.code : "WRITE_TARGET_BLOCKED",
        `write_file locked containment revalidation failed: ${String(error.message || error)}`,
        { retryAllowed: true, retryMode: "after_state_change" },
      );
    }
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    const hash = sha256Text(content);
    context.buildChanges.record(resolution.activeProject, [resolution.relativePath]);
    const snapshot = await registerCurrentVersion(context, resolution, hash, requestContext);
    return withChangeEvidence(success({
      operation: "created",
      path: `project://${resolution.relativePath}`,
      bytesWritten: Buffer.byteLength(content, "utf8"),
      sha256: hash,
      ...snapshot,
      advisory: "Build or validate when useful; neither is required before the other.",
      ...(semanticAdvisory ? {
        semanticAdvisories: [{ ...semanticAdvisory, path: `project://${resolution.relativePath}` }],
      } : {}),
    }), committedChange("", content, null, hash), context.payloadFits);
  }

  async function replaceInFile(args, requestContext = {}) {
    if (!writesAllowed()) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const argumentError = replaceFileArgumentError(args);
    if (argumentError) {
      return failure("INVALID_ARGUMENT", argumentError, {
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    const edits = args.edits;
    const range = isLineRangePatch(args)
      ? { startLine: args.startLine, endLine: args.endLine, expectedRangeSha256: args.expectedRangeSha256.toLowerCase() } : null;
    const regions = edits || [{ oldText: args.oldText ?? "", newText: args.newText }];
    const oldText = regions[0].oldText;
    const tooLarge = regions.findIndex(({ oldText: before, newText: after }) => before.length > limits.maxPatchOldTextChars
      || after.length > limits.maxPatchNewTextChars
      || before.length + after.length > limits.maxPatchChars
      || textLineCount(after) > limits.maxPatchLines);
    const totalLines = regions.reduce((sum, region) => sum + textLineCount(region.newText), 0);
    const totalBytes = regions.reduce((sum, region) => sum + Buffer.byteLength(region.oldText) + Buffer.byteLength(region.newText), 0);
    if (tooLarge >= 0 || (edits && (totalLines > limits.maxBundleChangedLines || totalBytes > limits.maxBundleMutationBytes))) {
      return failure(
        "PATCH_TOO_LARGE",
        `Each region is limited to oldText ${limits.maxPatchOldTextChars} characters, newText ${limits.maxPatchNewTextChars} characters, ${limits.maxPatchChars} combined characters and ${limits.maxPatchLines} newText lines; edits[] together to ${limits.maxBundleChangedLines} newText lines. Split the change and chain the returned fileVersionReceipt.`,
        { retryAllowed: true, ...(tooLarge >= 0 && edits ? { details: { editIndex: tooLarge } } : {}) },
      );
    }
    assertMutationActive(requestContext.signal);
    const resolution = await mutationResolution(args.path, args.project);
    const targetIdentity = frozenMutationTarget(resolution);
    const version = resolveVersionEvidence(context, resolution, args, requestContext);
    // A failed or unproven edit names the lines around its own target, read
    // from the current file; the version proof still comes only from that read.
    // A line range is aimed at its own lines, with the same margin.
    const aimed = (content, target = oldText) => regionSuggestion(args, range
      ? { candidateLines: [range.startLine], startLine: Math.max(1, range.startLine - 10), endLine: range.endLine + 10 }
      : locateOldText(content, target), projectScopedSuggestionArgs);
    if (!version.ok) {
      const current = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
      const located = current.ok ? aimed(current.content) : {};
      return versionEvidenceFailure(version, { ...located,
        details: { ...located.details, ...(current.ok ? { currentSha256: current.hash } : {}) } });
    }
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    if (read.hash.toLowerCase() !== version.expectedHash) {
      return versionConflict(version, read.hash, aimed(read.content));
    }
    const prospective = calculatePatch(read.buffer, args, { maxRangeTextChars: limits.maxPatchOldTextChars });
    if (!prospective.ok && range) {
      // Like an occurrence mismatch, a stale range answers with what is there now.
      const located = aimed(read.content);
      return failure(prospective.errorCode, prospective.error, { retryAllowed: true, retryMode: "different_arguments",
        ...located,
        details: { ...located.details, currentSha256: read.hash,
          ...(prospective.currentRange ? { currentRange: prospective.currentRange } : {}),
          ...(prospective.totalLines ? { totalLines: prospective.totalLines } : {}) } });
    }
    if (!prospective.ok) {
      const failed = edits ? edits[prospective.editIndex] || edits[0] : regions[0];
      const located = aimed(read.content, failed.oldText);
      return failure(
        prospective.errorCode
          || (/occurrence mismatch/iu.test(prospective.error) ? "OCCURRENCE_MISMATCH" : "OLD_TEXT_NOT_FOUND"),
        prospective.error,
        { retryAllowed: true, ...located,
          details: { ...located.details, currentSha256: read.hash, ...(edits ? { editIndex: prospective.editIndex } : {}) } },
      );
    }
    const semanticAdvisory = mutationSemanticAdvisory(
      resolution.absolutePath,
      prospective.updated,
      options.validateMutationSemanticText,
    );
    let locked;
    try {
      locked = await withPathLock(resolution.absolutePath, `${context.runtimeOwner}_replace`, async () => {
        const refreshed = await mutationResolution(args.path, args.project);
        if (!sameMutationTarget(targetIdentity, refreshed)) {
          throw new Error("replace_in_file real target or containment root changed during locked revalidation");
        }
        return replaceWithCAS({
          signal: requestContext.signal,
          targetPath: refreshed.absolutePath,
          priorContent: read.buffer,
          oldText,
          newText: args.newText,
          edits,
          range,
          expectedOccurrences: args.expectedOccurrences,
          readHash: version.expectedHash,
        });
      }, { stateRoot });
    } catch (error) {
      return failure(
        error.code === "REQUEST_CANCELLED" ? error.code : "WRITE_TARGET_BLOCKED",
        `replace_in_file locked containment revalidation failed: ${String(error.message || error)}`,
        { retryAllowed: true, retryMode: "after_state_change" },
      );
    }
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    if (!locked.result.ok) {
      if (locked.result.errorCode === "READ_HASH_CAS_MISMATCH") {
        const current = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
        return versionConflict(version, current.ok ? current.hash : "unavailable");
      }
      return failure(locked.result.errorCode || "PATCH_FAILED", locked.result.error, {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    const nextHash = sha256Text(locked.result.updated);
    if (nextHash !== read.hash) context.buildChanges.record(resolution.activeProject, [resolution.relativePath]);
    const snapshot = await registerCurrentVersion(context, resolution, nextHash, requestContext);
    return withChangeEvidence(success({
      operation: "replaced",
      path: `project://${resolution.relativePath}`,
      occurrences: locked.result.occurrences,
      previousSha256: read.hash,
      sha256: nextHash,
      hashSource: version.hashSource,
      ...snapshot,
      advisory: "The edit is complete. Static validation and build remain independent diagnostics.",
      ...(semanticAdvisory ? {
        semanticAdvisories: [{ ...semanticAdvisory, path: `project://${resolution.relativePath}` }],
      } : {}),
    }), edits || range
      ? { schemaVersion: 1, previousSha256: locked.result.preHash, sha256: nextHash,
        status: locked.result.preHash === nextHash ? "no_change" : "changed",
        coverage: "complete_changed_regions", hunks: locked.result.regions }
      : committedChange(read.buffer, locked.result.updated, locked.result.preHash, nextHash), context.payloadFits);
  }

  return { replace_in_file: replaceInFile, write_file: writeFile };
}

module.exports = { createFileMutationCapabilities };
