"use strict";

const path = require("node:path");
const { calculateReplacement } = require("./safe-write");
const { validateWriteTarget } = require("./write-guards");
const { applyDirectEditBundle, validateBundleLimits } = require("./direct-edit-bundle");
const { readStableTextFile } = require("./direct-file-snapshot");
const { mutationSemanticAdvisory } = require("./mutation-semantic-guard");
const { failure, success } = require("./direct-response");
const {
  registerCurrentVersion,
  resolveVersionEvidence,
} = require("./direct-file-version-policy.js");
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

  function validationError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  async function validateProspective(bundle, projectSelector, requestContext) {
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
        const version = resolveVersionEvidence(context, resolution, patch, requestContext);
        if (!version.ok) {
          throw validationError(version.errorCode, `${patch.path}: ${version.message}`);
        }
        if (read.hash.toLowerCase() !== version.expectedHash) {
          throw validationError("FILE_VERSION_CONFLICT", `${patch.path} changed after the selected read snapshot`);
        }
        patch.expectedHash = version.expectedHash;
        state = { content: read.content, initialHash: read.hash, version };
      } else if (state.initialHash) {
        const carriesEvidence = String(patch.expectedHash || "").trim()
          || String(patch.fileVersionReceipt || "").trim();
        if (carriesEvidence) {
          const version = resolveVersionEvidence(context, resolution, patch, requestContext);
          if (!version.ok) throw validationError(version.errorCode, `${patch.path}: ${version.message}`);
          if (state.initialHash.toLowerCase() !== version.expectedHash) {
            throw validationError("FILE_VERSION_CONFLICT", `Version evidence differs between patches for ${patch.path}`);
          }
        }
        patch.expectedHash = state.initialHash;
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

  async function applyEditBundle(args, requestContext = {}) {
    if (!envFlag(env, "ALLOW_WRITE", false)) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    const bundle = {
      files: Array.isArray(args.files) ? args.files.map((item) => ({ ...item })) : [],
      patches: Array.isArray(args.patches) ? args.patches.map((item) => ({ ...item })) : [],
    };
    let validation;
    try {
      validation = await validateProspective(bundle, args.project, requestContext);
    } catch (error) {
      const message = String(error.message || error);
      return failure(
        error.code || "BUNDLE_VALIDATION_FAILED",
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
        result.rollbackIncomplete
          ? "ROLLBACK_INCOMPLETE"
          : result.mutationFailure?.errorCode === "FILE_VERSION_CONFLICT"
            ? "FILE_VERSION_CONFLICT"
            : "BUNDLE_FAILED",
        result.error || "Atomic edit bundle failed",
        {
          details: { transactionId: result.transactionId, rollback: result.rollback },
          retryAllowed: !result.rollbackIncomplete,
          retryMode: "after_state_change",
        },
      );
    }
    const files = [];
    for (const [filePath, hash] of Object.entries(result.postWriteHashes || {})) {
      const resolution = resolutions.get(filePath)
        || await mutationResolution(filePath, args.project);
      const snapshot = await registerCurrentVersion(context, resolution, hash, requestContext);
      files.push({ path: filePath, sha256: hash, ...snapshot });
    }
    return success({
      operation: "bundle_applied",
      transactionId: result.transactionId,
      files,
      advisory: "The atomic edit committed. Build can run immediately; static validation is optional diagnostic evidence.",
      ...(semanticAdvisories.length ? { semanticAdvisories } : {}),
    });
  }

  return { apply_edit_bundle: applyEditBundle };
}

module.exports = { createBundleCapability };
