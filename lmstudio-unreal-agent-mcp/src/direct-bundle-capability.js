"use strict";

const path = require("node:path");
const { calculateReplacement } = require("./safe-write");
const { validateWriteTarget } = require("./write-guards");
const { applyDirectEditBundle, validateBundleLimits } = require("./direct-edit-bundle");
const { readStableTextFile } = require("./direct-file-snapshot");
const { mutationSemanticAdvisory } = require("./mutation-semantic-guard");
const { failure, success } = require("./direct-response");
const {
  envFlag,
  statOrNull,
} = require("./direct-runtime-shared");

function createBundleCapability(context) {
  const {
    env,
    limits,
    mutationResolution,
    options,
    resolveCallProject,
    runtimeOwner,
    stateRoot,
    workspaceRoot,
  } = context;

  async function validateProspective(bundle, projectSelector) {
    validateBundleLimits(bundle, limits);
    const resolutions = new Map();
    const prospective = new Map();
    for (const file of bundle.files || []) {
      const resolution = await mutationResolution(file.path, projectSelector);
      resolutions.set(file.path, resolution);
      const content = String(file.content ?? "");
      const guard = await validateWriteTarget({
        targetAbsPath: resolution.absolutePath,
        workspaceRoot,
        activeProjectPath: resolution.activeProject,
        createDirs: true,
        fileExists: async (target) => Boolean(await statOrNull(target)),
        allowExistingWrite: false,
      });
      if (!guard.ok) throw new Error(guard.message);
      prospective.set(file.path, { content, initialHash: "" });
    }
    for (const patch of bundle.patches || []) {
      const resolution = resolutions.get(patch.path)
        || await mutationResolution(patch.path, projectSelector);
      resolutions.set(patch.path, resolution);
      let state = prospective.get(patch.path);
      if (!state) {
        const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
        if (!read.ok) throw new Error(read.message);
        if (!/^[a-f0-9]{64}$/iu.test(String(patch.expectedHash || ""))) {
          throw new Error(`expectedHash is required for ${patch.path}`);
        }
        if (read.hash.toLowerCase() !== String(patch.expectedHash).toLowerCase()) {
          throw new Error(`READ_CONFLICT: ${patch.path} changed after it was read`);
        }
        state = { content: read.content, initialHash: read.hash };
      } else if (state.initialHash
        && state.initialHash.toLowerCase() !== String(patch.expectedHash || "").toLowerCase()) {
        throw new Error(`expectedHash differs between patches for ${patch.path}`);
      }
      const next = calculateReplacement({
        priorContent: state.content,
        oldText: patch.oldText,
        newText: patch.newText,
        expectedOccurrences: Number(patch.expectedOccurrences),
      });
      if (!next.ok) throw new Error(`${patch.path}: ${next.error}`);
      prospective.set(patch.path, { ...state, content: next.updated });
    }
    const semanticAdvisories = [];
    for (const [relativePath, state] of prospective) {
      const advisory = mutationSemanticAdvisory(
        resolutions.get(relativePath).absolutePath,
        state.content,
        options.validateMutationSemanticText,
      );
      if (advisory) {
        semanticAdvisories.push({ ...advisory, path: `project://${relativePath}` });
      }
    }
    return { resolutions, semanticAdvisories };
  }

  async function applyEditBundle(args) {
    if (!envFlag(env, "ALLOW_WRITE", false)) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const bundle = {
      files: Array.isArray(args.files) ? args.files : [],
      patches: Array.isArray(args.patches) ? args.patches : [],
    };
    let validation;
    try {
      validation = await validateProspective(bundle, args.project);
    } catch (error) {
      const message = String(error.message || error);
      return failure(
        message.includes("READ_CONFLICT") ? "READ_CONFLICT" : "BUNDLE_VALIDATION_FAILED",
        message,
        { retryAllowed: true, retryMode: "different_arguments" },
      );
    }
    const { resolutions, semanticAdvisories } = validation;
    const firstResolution = resolutions.values().next().value;
    const activeProject = firstResolution?.activeProject || await resolveCallProject(args.project);
    const result = await applyDirectEditBundle(bundle, async (relativePath) => {
      const resolution = resolutions.get(relativePath)
        || await mutationResolution(relativePath, args.project);
      return { ok: true, absolutePath: resolution.absolutePath };
    }, {
      mutationLimits: limits,
      projectRoot: path.dirname(path.resolve(activeProject)),
      projectPath: activeProject,
      runtimeOwner,
      stateRoot,
      transactionHooks: options.transactionHooks,
    });
    if (!result.ok) {
      return failure(
        result.rollbackIncomplete ? "ROLLBACK_INCOMPLETE" : "BUNDLE_FAILED",
        result.error || "Atomic edit bundle failed",
        {
          details: { transactionId: result.transactionId, rollback: result.rollback },
          retryAllowed: !result.rollbackIncomplete,
          retryMode: "after_state_change",
        },
      );
    }
    return success({
      operation: "bundle_applied",
      transactionId: result.transactionId,
      files: Object.entries(result.postWriteHashes || {}).map(([filePath, hash]) => ({
        path: filePath,
        sha256: hash,
      })),
      advisory: "The atomic edit committed. Build can run immediately; static validation is optional diagnostic evidence.",
      ...(semanticAdvisories.length ? { semanticAdvisories } : {}),
    });
  }

  return { apply_edit_bundle: applyEditBundle };
}

module.exports = { createBundleCapability };
