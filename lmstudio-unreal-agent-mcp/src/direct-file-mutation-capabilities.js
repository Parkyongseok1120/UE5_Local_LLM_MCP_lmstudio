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
const { mutationSemanticAdvisory } = require("./mutation-semantic-guard");
const { failure, success } = require("./direct-response");
const {
  envFlag,
  statOrNull,
} = require("./direct-runtime-shared");

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

  async function writeFile(args) {
    if (!writesAllowed()) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const content = String(args.content ?? "");
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
    const locked = await withPathLock(resolution.absolutePath, `${context.runtimeOwner}_write_file`, async () => {
      if (args.createDirs === true) {
        await fsp.mkdir(path.dirname(resolution.absolutePath), { recursive: true });
      }
      return createExclusive(resolution.absolutePath, content);
    }, { stateRoot });
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    return success({
      operation: "created",
      path: `project://${resolution.relativePath}`,
      bytesWritten: Buffer.byteLength(content, "utf8"),
      sha256: sha256Text(content),
      advisory: "Build or validate when useful; neither is required before the other.",
      ...(semanticAdvisory ? {
        semanticAdvisories: [{ ...semanticAdvisory, path: `project://${resolution.relativePath}` }],
      } : {}),
    });
  }

  async function replaceInFile(args) {
    if (!writesAllowed()) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const oldText = String(args.oldText ?? "");
    const newText = String(args.newText ?? "");
    if (!oldText) return failure("INVALID_ARGUMENT", "oldText must be non-empty", { retryAllowed: true });
    if (oldText.length + newText.length > limits.maxPatchChars
      || textLineCount(newText) > limits.maxPatchLines) {
      return failure(
        "PATCH_TOO_LARGE",
        `Patch exceeds ${limits.maxPatchChars} characters or ${limits.maxPatchLines} changed lines. Split it into exact regions.`,
        { retryAllowed: true },
      );
    }
    if (!/^[a-f0-9]{64}$/iu.test(String(args.expectedHash || ""))) {
      return failure(
        "EXPECTED_HASH_REQUIRED",
        "expectedHash must be the 64-character SHA-256 returned by read_file/read_file_range.",
        { retryAllowed: true },
      );
    }
    const resolution = await mutationResolution(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    if (read.hash.toLowerCase() !== String(args.expectedHash).toLowerCase()) {
      return failure("READ_CONFLICT", "The file changed after it was read. Re-read the target and recompute the patch.", {
        retryAllowed: true,
        retryMode: "after_state_change",
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
      expectedOccurrences: Number(args.expectedOccurrences),
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
    const locked = await withPathLock(resolution.absolutePath, `${context.runtimeOwner}_replace`, async () => (
      replaceWithCAS({
        targetPath: resolution.absolutePath,
        priorContent: read.buffer,
        oldText,
        newText,
        expectedOccurrences: Number(args.expectedOccurrences),
        readHash: String(args.expectedHash),
      })
    ), { stateRoot });
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    if (!locked.result.ok) {
      return failure(locked.result.errorCode || "PATCH_FAILED", locked.result.error, {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    return success({
      operation: "replaced",
      path: `project://${resolution.relativePath}`,
      occurrences: locked.result.occurrences,
      previousSha256: read.hash,
      sha256: sha256Text(locked.result.updated),
      advisory: "The edit is complete. Static validation and build remain independent diagnostics.",
      ...(semanticAdvisory ? {
        semanticAdvisories: [{ ...semanticAdvisory, path: `project://${resolution.relativePath}` }],
      } : {}),
    });
  }

  return { replace_in_file: replaceInFile, write_file: writeFile };
}

module.exports = { createFileMutationCapabilities };
