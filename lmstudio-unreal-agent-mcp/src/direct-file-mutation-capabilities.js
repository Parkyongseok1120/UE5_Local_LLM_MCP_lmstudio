"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const {
  calculateReplacement,
  createExclusive,
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

const WRITE_FILE_FIELDS = new Set(["path", "project", "content", "createDirs"]);
const REPLACE_FILE_FIELDS = new Set([
  "path",
  "project",
  "oldText",
  "newText",
  "expectedOccurrences",
  "expectedHash",
  "fileVersionReceipt",
]);

function mutationArgumentError(args, allowedFields, label) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return `${label} arguments must be an object.`;
  }
  const unsupported = Object.keys(args).find((key) => !allowedFields.has(key));
  return unsupported ? `${label} contains unsupported field: ${unsupported}` : "";
}

function requiredStringError(args, field, { allowEmpty = false } = {}) {
  if (typeof args[field] !== "string") return `${field} must be a string.`;
  if (!allowEmpty && !args[field].trim()) return `${field} must be a non-empty string.`;
  return "";
}

function optionalStringError(args, field) {
  return args[field] === undefined || typeof args[field] === "string"
    ? ""
    : `${field} must be a string when provided.`;
}

function writeFileArgumentError(args) {
  return mutationArgumentError(args, WRITE_FILE_FIELDS, "write_file")
    || requiredStringError(args, "path")
    || requiredStringError(args, "content", { allowEmpty: true })
    || optionalStringError(args, "project")
    || (args.createDirs === undefined || typeof args.createDirs === "boolean"
      ? ""
      : "createDirs must be a boolean when provided.");
}

function replaceFileArgumentError(args) {
  return mutationArgumentError(args, REPLACE_FILE_FIELDS, "replace_in_file")
    || requiredStringError(args, "path")
    || requiredStringError(args, "oldText")
    || requiredStringError(args, "newText", { allowEmpty: true })
    || optionalStringError(args, "project")
    || optionalStringError(args, "expectedHash")
    || optionalStringError(args, "fileVersionReceipt")
    || (Number.isInteger(args.expectedOccurrences) && args.expectedOccurrences === 1
      ? ""
      : "expectedOccurrences must be the integer 1 for a focused replacement.");
}

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
        if (args.createDirs === true) {
          await fsp.mkdir(path.dirname(refreshed.absolutePath), { recursive: true });
        }
        return createExclusive(refreshed.absolutePath, content);
      }, { stateRoot });
    } catch (error) {
      return failure(
        "WRITE_TARGET_BLOCKED",
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
    const snapshot = await registerCurrentVersion(context, resolution, hash, requestContext);
    return success({
      operation: "created",
      path: `project://${resolution.relativePath}`,
      bytesWritten: Buffer.byteLength(content, "utf8"),
      sha256: hash,
      ...snapshot,
      advisory: "Build or validate when useful; neither is required before the other.",
      ...(semanticAdvisory ? {
        semanticAdvisories: [{ ...semanticAdvisory, path: `project://${resolution.relativePath}` }],
      } : {}),
    });
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
    const oldText = args.oldText;
    const newText = args.newText;
    if (oldText.length > limits.maxPatchOldTextChars
      || newText.length > limits.maxPatchNewTextChars
      || oldText.length + newText.length > limits.maxPatchChars
      || textLineCount(newText) > limits.maxPatchLines) {
      return failure(
        "PATCH_TOO_LARGE",
        `Patch exceeds the focused-region limits: oldText ${limits.maxPatchOldTextChars} characters, newText ${limits.maxPatchNewTextChars} characters, ${limits.maxPatchChars} combined characters, or ${limits.maxPatchLines} newText lines. Apply one exact region, then use the returned fileVersionReceipt in the next prediction round.`,
        { retryAllowed: true },
      );
    }
    const resolution = await mutationResolution(args.path, args.project);
    const targetIdentity = frozenMutationTarget(resolution);
    const version = resolveVersionEvidence(context, resolution, args, requestContext);
    if (!version.ok) {
      return versionEvidenceFailure(version, {
        suggestion: {
          tool: "read_file_range",
          args: projectScopedSuggestionArgs(args, { path: args.path, startLine: 1, endLine: 300 }),
        },
      });
    }
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    if (read.hash.toLowerCase() !== version.expectedHash) {
      return versionConflict(version, read.hash, {
        suggestion: {
          tool: "read_file_range",
          args: projectScopedSuggestionArgs(args, { path: args.path, startLine: 1, endLine: 300 }),
        },
      });
    }
    const prospective = calculateReplacement({
      priorContent: read.buffer,
      oldText,
      newText,
      expectedOccurrences: args.expectedOccurrences,
    });
    if (!prospective.ok) {
      return failure(
        /occurrence mismatch/iu.test(prospective.error) ? "OCCURRENCE_MISMATCH" : "OLD_TEXT_NOT_FOUND",
        prospective.error,
        {
          retryAllowed: true,
          suggestion: {
            tool: "read_file_range",
            args: projectScopedSuggestionArgs(args, { path: args.path, startLine: 1, endLine: 300 }),
          },
        },
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
          targetPath: refreshed.absolutePath,
          priorContent: read.buffer,
          oldText,
          newText,
          expectedOccurrences: args.expectedOccurrences,
          readHash: version.expectedHash,
        });
      }, { stateRoot });
    } catch (error) {
      return failure(
        "WRITE_TARGET_BLOCKED",
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
    const snapshot = await registerCurrentVersion(context, resolution, nextHash, requestContext);
    return success({
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
    });
  }

  return { replace_in_file: replaceInFile, write_file: writeFile };
}

module.exports = { createFileMutationCapabilities };
