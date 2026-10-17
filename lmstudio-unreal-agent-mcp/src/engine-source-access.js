"use strict";

// Read-only access policy for the installed Unreal Engine's own source.
//
// `engine://Engine/Source/...` lets an auditor read an engine implementation
// (.cpp included) instead of guessing from a header. The engine is selected by
// the active project's EngineAssociation through the existing resolver, never
// by a version or path constant. Nothing here writes; mutation paths do not
// receive an engine resolver and therefore reject the scheme.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { resolveEngineRoot } = require("./unreal-engine-resolution.js");
const { loadMergedConfig } = require("./unreal-config.js");

const ENGINE_URI_PREFIX = "engine://";
const BUILD_VERSION_RELATIVE = "Engine/Build/Build.version";
const ENGINE_READ_EXTENSIONS = new Set([".h", ".hpp", ".inl", ".cpp", ".cs", ".ini", ".json", ".version"]);
const ENGINE_SOURCE_DIRECTORY = /^Engine\/Source(?:\/.*)?$/;
const ENGINE_PLUGIN_SOURCE_DIRECTORY = /^Engine\/Plugins\/(?:[^/]+\/)*Source(?:\/.*)?$/;
const MAX_IDENTITY_BYTES = 16384;

function isEngineUri(value) {
  return typeof value === "string" && value.trim().toLowerCase().startsWith(ENGINE_URI_PREFIX);
}

/** Whether an engine-root-relative path (slash separated) may be read. */
function isAllowedEngineRelative(relative, { isFile = null } = {}) {
  const normalized = String(relative || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized || normalized.split("/").includes("..")) return false;
  if (normalized === BUILD_VERSION_RELATIVE) return true;
  if (!ENGINE_SOURCE_DIRECTORY.test(normalized) && !ENGINE_PLUGIN_SOURCE_DIRECTORY.test(normalized)) return false;
  if (isFile === false) return true;
  const extension = path.posix.extname(normalized).toLowerCase();
  // A missing path is judged as a file when it has a recognised extension.
  return isFile === true ? ENGINE_READ_EXTENSIONS.has(extension) : extension === "" || ENGINE_READ_EXTENSIONS.has(extension);
}

/** Strict, bounded Build.version observation: `{status, version, source, sha256}`. */
function readEngineIdentity(engineRoot) {
  const file = path.join(engineRoot, BUILD_VERSION_RELATIVE);
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > MAX_IDENTITY_BYTES) throw new Error("Build.version is not a small regular file");
    const raw = fs.readFileSync(file);
    const parsed = JSON.parse(raw.toString("utf8").replace(/^\uFEFF/, ""));
    const major = parsed.MajorVersion;
    const minor = parsed.MinorVersion;
    if (!Number.isSafeInteger(major) || !Number.isSafeInteger(minor) || major < 0 || minor < 0) {
      throw new Error("Build.version lacks a verified major/minor");
    }
    const patch = Number.isSafeInteger(parsed.PatchVersion) && parsed.PatchVersion >= 0 ? `.${parsed.PatchVersion}` : "";
    return { status: "observed", version: `${major}.${minor}${patch}`, source: "build_version_file",
      sha256: crypto.createHash("sha256").update(raw).digest("hex"), hashScope: "file" };
  } catch (error) {
    return { status: "unknown", source: "build_version_file", reason: String(error.message || error).slice(0, 180) };
  }
}

/**
 * Resolves the installed engine for a project descriptor.
 * Returns `async (activeProject) => { engineRoot, identity }` and throws a
 * descriptive error when the project or its engine cannot be bound.
 */
function createEngineSourceResolver({ workspaceRoot, configPath, env = process.env, hostPlatform, loadConfig = loadMergedConfig }) {
  return async function resolveEngineSource(activeProject) {
    if (!activeProject) throw new Error("engine:// requires an active or per-call .uproject to select the engine");
    let association = "";
    try {
      const descriptor = JSON.parse(fs.readFileSync(activeProject, "utf8").replace(/^\uFEFF/, ""));
      association = typeof descriptor.EngineAssociation === "string" ? descriptor.EngineAssociation.trim() : "";
    } catch (error) {
      throw new Error(`engine:// could not read the project descriptor: ${String(error.message || error)}`);
    }
    const config = await loadConfig(configPath);
    const engine = await resolveEngineRoot(association, config, undefined,
      { workspaceRoot, env, ...(hostPlatform ? { hostPlatform } : {}) });
    if (!engine || engine.errorCode || !engine.engineRoot) {
      throw new Error(engine?.error || "engine:// could not resolve the installed engine for the project");
    }
    const identity = readEngineIdentity(engine.engineRoot);
    return { engineRoot: path.resolve(engine.engineRoot), identity: { ...identity, engineAssociation: association || null,
      engineRootSource: engine.source || null } };
  };
}

module.exports = {
  BUILD_VERSION_RELATIVE,
  ENGINE_READ_EXTENSIONS,
  ENGINE_URI_PREFIX,
  createEngineSourceResolver,
  isAllowedEngineRelative,
  isEngineUri,
  readEngineIdentity,
};
