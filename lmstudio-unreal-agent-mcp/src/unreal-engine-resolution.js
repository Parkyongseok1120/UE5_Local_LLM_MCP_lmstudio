"use strict";

const path = require("node:path");
const { filesystemPathIdentity } = require("./filesystem-path-identity");
const {
  configuredEngineRootForAssociation,
  engineAssociationsMatch,
  engineFolderFromAssociation,
  engineRootMatchesNumericAssociation,
  resolveEngineBuildTool,
} = require("./unreal-engine-core");
const { findEngineInstalls } = require("./unreal-engine-registry");
const { pathIdentity } = require("./unreal-project-core");

function engineAssociationUnresolved(engineAssociation, detail) {
  const association = String(engineAssociation || "").trim();
  return {
    engineRoot: "",
    buildTool: "",
    buildToolKind: "",
    buildBat: "",
    source: "",
    requestedEngineAssociation: association,
    warning: null,
    errorCode: "ENGINE_ASSOCIATION_UNRESOLVED",
    error: `ENGINE_ASSOCIATION_UNRESOLVED: EngineAssociation ${JSON.stringify(association)} ${detail}. `
      + "Set engineRoot, UNREAL_ENGINE_ROOT, or an exact engineRootsByAssociation entry.",
  };
}

function engineRootUnresolved(detail) {
  return {
    engineRoot: "",
    buildTool: "",
    buildToolKind: "",
    buildBat: "",
    source: "",
    requestedEngineAssociation: "",
    warning: null,
    errorCode: "ENGINE_ROOT_UNRESOLVED",
    error: detail,
  };
}

function usesInjectedRegistrationContext(options) {
  return [
    "hostPlatform",
    "env",
    "homeDirectory",
    "roots",
    "launcherManifestPaths",
    "registryInstallations",
    "installIniPaths",
  ].some((key) => Object.prototype.hasOwnProperty.call(options, key))
    || typeof options.registryReader === "function";
}

async function resolveEngineRoot(engineAssociation, config, explicitEngineRoot, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const association = String(engineAssociation || "").trim();
  const requestedFolder = engineFolderFromAssociation(association);
  const configBase = options.workspaceRoot || process.cwd();
  const readSystemRegistry = options.readSystemRegistry === undefined
    ? !usesInjectedRegistrationContext(options)
    : options.readSystemRegistry === true;
  const discoveryOptions = { ...options, hostPlatform, env, readSystemRegistry };

  const resolveCandidate = async (value, source, { relativeToConfig = false } = {}) => {
    const raw = String(value || "").trim();
    if (!raw) return null;
    const resolved = path.isAbsolute(raw)
      ? path.resolve(raw)
      : path.resolve(relativeToConfig ? configBase : process.cwd(), raw);
    const buildTool = await resolveEngineBuildTool(resolved, hostPlatform);
    if (!buildTool) return null;
    return {
      engineRoot: resolved,
      buildTool: buildTool.path,
      buildToolKind: buildTool.kind,
      buildBat: buildTool.path,
      source,
      requestedEngineAssociation: association,
      warning: source === "environment" && association
        ? `Using UNREAL_ENGINE_ROOT for EngineAssociation ${association}.`
        : null,
    };
  };

  const explicit = await resolveCandidate(explicitEngineRoot, "argument");
  if (explicit) return explicit;
  if (explicitEngineRoot && association) {
    return engineAssociationUnresolved(association, "could not use the explicit engineRoot");
  }

  const environmentEngineRoot = String(env.UNREAL_ENGINE_ROOT || "").trim();
  const environment = await resolveCandidate(environmentEngineRoot, "environment");
  const environmentAssociationIsManaged = Object.prototype.hasOwnProperty.call(
    env,
    "UNREAL_ENGINE_ROOT_ASSOCIATION",
  );
  const environmentAssociation = String(env.UNREAL_ENGINE_ROOT_ASSOCIATION || "").trim();
  const staleManagedEnvironment = Boolean(
    association
    && environmentAssociationIsManaged
    && !engineAssociationsMatch(environmentAssociation, association)
  );
  const staleNumericEnvironment = Boolean(
    environment
    && requestedFolder
    && !engineRootMatchesNumericAssociation(environment.engineRoot, association)
  );
  const sameResolvedRoot = (left, right) => Boolean(
    left?.engineRoot
    && right?.engineRoot
    && pathIdentity(left.engineRoot, hostPlatform) === pathIdentity(right.engineRoot, hostPlatform)
  );

  if (association) {
    const mappedEngineRoot = configuredEngineRootForAssociation(association, config);
    if (mappedEngineRoot) {
      const mapped = await resolveCandidate(
        mappedEngineRoot,
        "config.engineRootsByAssociation",
        { relativeToConfig: true },
      );
      if (mapped) {
        return !staleManagedEnvironment && sameResolvedRoot(environment, mapped)
          ? environment
          : mapped;
      }
      return engineAssociationUnresolved(
        association,
        "has an engineRootsByAssociation entry that is not a usable engine root",
      );
    }

    const installs = await findEngineInstalls(discoveryOptions);
    const registeredMatches = [];
    const registeredRoots = new Set();
    for (const install of installs) {
      const registration = (install.registrations || []).find((item) => (
        engineAssociationsMatch(item.association, association)
      ));
      if (!registration) continue;
      if (requestedFolder && !engineRootMatchesNumericAssociation(install.engineRoot, association)) {
        continue;
      }
      const key = pathIdentity(install.engineRoot, hostPlatform);
      if (registeredRoots.has(key)) continue;
      registeredRoots.add(key);
      registeredMatches.push({ install, registration });
    }
    if (registeredMatches.length > 1) {
      return engineAssociationUnresolved(
        association,
        "has multiple conflicting registered engine roots",
      );
    }
    if (registeredMatches.length === 1) {
      const { install, registration } = registeredMatches[0];
      const registered = {
        engineRoot: install.engineRoot,
        buildTool: install.buildTool,
        buildToolKind: install.buildToolKind,
        buildBat: install.buildBat,
        source: `registered.${registration.source}`,
        requestedEngineAssociation: association,
        warning: null,
      };
      return !staleManagedEnvironment && sameResolvedRoot(environment, registered)
        ? environment
        : registered;
    }

    if (environment && !staleManagedEnvironment && !staleNumericEnvironment) return environment;
    if (environmentEngineRoot && !staleManagedEnvironment && !staleNumericEnvironment) {
      return engineAssociationUnresolved(association, "could not use UNREAL_ENGINE_ROOT");
    }
    if (!requestedFolder) {
      return engineAssociationUnresolved(
        association,
        "is a custom/source-build identifier without an exact mapping",
      );
    }

    const requestedKey = filesystemPathIdentity(requestedFolder, hostPlatform, {
      stripProjectUri: false,
    });
    const exact = installs.find((item) => filesystemPathIdentity(
      item.folderName,
      hostPlatform,
      { stripProjectUri: false },
    ) === requestedKey);
    if (exact) {
      return {
        engineRoot: exact.engineRoot,
        buildTool: exact.buildTool,
        buildToolKind: exact.buildToolKind,
        buildBat: exact.buildBat,
        source: "EngineAssociation",
        requestedEngineAssociation: association,
        warning: null,
      };
    }
    return engineAssociationUnresolved(
      association,
      `does not have an installed ${requestedFolder} engine`,
    );
  }

  if (environment) return environment;

  const configured = await resolveCandidate(
    config?.defaultEngineRoot,
    "config.defaultEngineRoot",
    { relativeToConfig: true },
  );
  if (configured) return configured;

  const installs = await findEngineInstalls(discoveryOptions);
  const fallback = installs[installs.length - 1];
  if (fallback) {
    return {
      engineRoot: fallback.engineRoot,
      buildTool: fallback.buildTool,
      buildToolKind: fallback.buildToolKind,
      buildBat: fallback.buildBat,
      source: fallback.source === "environment" ? "environment" : "latest-installed",
      requestedEngineAssociation: "",
      warning: null,
    };
  }

  return engineRootUnresolved(
    "Could not resolve Unreal Engine installation. Set engineRoot, UNREAL_ENGINE_ROOT, or config.defaultEngineRoot.",
  );
}

module.exports = { resolveEngineRoot };
