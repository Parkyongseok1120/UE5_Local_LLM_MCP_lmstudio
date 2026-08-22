"use strict";

const path = require("node:path");
const { calculateReplacement } = require("./safe-write");
const { applyDirectEditBundle, validateBundleLimits } = require("./direct-edit-bundle");
const { normalizedBundlePath } = require("./direct-edit-bundle-plan");
const { readStableTextFile } = require("./direct-file-snapshot");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");
const { mutationSemanticAdvisory } = require("./mutation-semantic-guard");
const { failure, success } = require("./direct-response");
const {
  registerCurrentVersion,
  resolveVersionEvidence,
} = require("./direct-file-version-policy.js");
const {
  envFlag,
} = require("./direct-runtime-shared");

const APPLY_EDIT_BUNDLE_FIELDS = new Set(["project", "patches"]);

function frozenBundleTargetIdentity(resolution) {
  const identity = (value) => canonicalAbsolutePathIdentity(
    value,
    process.platform,
    { realpath: false },
  );
  return {
    absolutePath: identity(resolution.absolutePath),
    realPath: identity(resolution.realPath),
    lexicalRoot: identity(resolution.projectDir),
    allowedRealRoot: identity(resolution.allowedRealRoot),
  };
}

function createBundleCapability(context) {
  const {
    env,
    limits,
    mutationResolution,
    options,
    resolveCallProject,
    runtimeOwner,
    stateRoot,
  } = context;

  function validationError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function publicBundle(args) {
    if (!args || typeof args !== "object" || Array.isArray(args)) {
      throw validationError("INVALID_ARGUMENT", "apply_edit_bundle arguments must be an object");
    }
    const unsupported = Object.keys(args).find((key) => !APPLY_EDIT_BUNDLE_FIELDS.has(key));
    if (unsupported) {
      throw validationError(
        "INVALID_ARGUMENT",
        `apply_edit_bundle contains unsupported field: ${unsupported}`,
      );
    }
    if (args.project !== undefined && typeof args.project !== "string") {
      throw validationError("INVALID_ARGUMENT", "apply_edit_bundle.project must be a string when provided");
    }
    validateBundleLimits({ patches: args.patches }, limits);
    return {
      patches: args.patches.map((item) => ({
        ...item,
        path: normalizedBundlePath(item, "patch"),
      })),
    };
  }

  async function validateProspective(bundle, projectSelector, requestContext) {
    validateBundleLimits(bundle, limits);
    const resolutions = new Map();
    const targetIdentities = new Map();
    const prospective = new Map();
    for (const patch of bundle.patches) {
      const resolution = await mutationResolution(patch.path, projectSelector);
      resolutions.set(patch.path, resolution);
      targetIdentities.set(patch.path, frozenBundleTargetIdentity(resolution));
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
      const next = calculateReplacement({
        priorContent: read.content,
        oldText: patch.oldText,
        newText: patch.newText,
        expectedOccurrences: patch.expectedOccurrences,
      });
      if (!next.ok) throw new Error(`${patch.path}: ${next.error}`);
      prospective.set(patch.path, { content: next.updated, initialHash: read.hash, version });
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
    return { resolutions, semanticAdvisories, targetIdentities };
  }

  async function applyEditBundle(args, requestContext = {}) {
    if (!envFlag(env, "ALLOW_WRITE", false)) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1 to enable project mutations.");
    }
    let bundle;
    try {
      bundle = publicBundle(args);
    } catch (error) {
      return failure(
        error.code || "BUNDLE_VALIDATION_FAILED",
        String(error.message || error),
        { retryAllowed: true, retryMode: "different_arguments" },
      );
    }
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
    const { resolutions, semanticAdvisories, targetIdentities } = validation;
    const firstResolution = resolutions.values().next().value;
    const activeProject = firstResolution?.activeProject || await resolveCallProject(args.project);
    let result;
    try {
      result = await applyDirectEditBundle(bundle, async (relativePath) => {
        const resolution = resolutions.get(relativePath)
          || await mutationResolution(relativePath, args.project);
        return {
          ok: true,
          absolutePath: resolution.absolutePath,
          expectedIdentity: targetIdentities.get(relativePath)
            || frozenBundleTargetIdentity(resolution),
        };
      }, {
        mutationLimits: limits,
        projectRoot: path.dirname(path.resolve(activeProject)),
        projectPath: activeProject,
        runtimeOwner,
        stateRoot,
        transactionHooks: options.transactionHooks,
      });
    } catch (error) {
      return failure(
        "BUNDLE_FAILED",
        `Atomic edit bundle target validation failed: ${String(error.message || error)}`,
        { retryAllowed: true, retryMode: "after_state_change" },
      );
    }
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
