"use strict";

const path = require("node:path");
const { resolveEngineRoot } = require("./unreal-engine-resolution");
const { projectNameFromPath } = require("./unreal-project-core");
const { resolveProjectSelection } = require("./unreal-project-selection");

function defaultPlatform(hostPlatform = process.platform) {
  if (hostPlatform === "win32") return "Win64";
  if (hostPlatform === "darwin") return "Mac";
  return "Linux";
}

function resolveBuildTarget(requestedTarget, project) {
  const requested = String(requestedTarget || "").trim();
  if (requested && requested.toLowerCase() !== "editor") return requested;

  const targets = Array.isArray(project.allTargets)
    ? project.allTargets.map((target) => String(target).trim()).filter(Boolean)
    : [];
  const preferred = String(project.preferredTarget || "").trim();
  const canonicalEditor = `${project.projectName}Editor`;

  if (requested) {
    const exactCanonical = targets.find(
      (target) => target.toLowerCase() === canonicalEditor.toLowerCase(),
    );
    if (exactCanonical) return exactCanonical;
    if (preferred.toLowerCase().endsWith("editor")) return preferred;

    const editorTargets = targets.filter((target) => target.toLowerCase().endsWith("editor"));
    if (editorTargets.length === 1) return editorTargets[0];
  }

  return preferred || canonicalEditor;
}

async function resolveBuildPlan(workspaceRoot, configPath, args = {}) {
  const selection = await resolveProjectSelection(workspaceRoot, configPath, {
    hint: args.hint,
    project: args.project,
    maxDepth: args.searchMaxDepth,
  });
  if (!selection.selected) return { ok: false, ...selection };

  const project = selection.selected;
  const projectPath = project.projectPath;
  const engine = await resolveEngineRoot(
    project.engineAssociation,
    selection.config,
    args.engineRoot,
    { workspaceRoot },
  );
  if (!engine || engine.errorCode) {
    return {
      ok: false,
      ...selection,
      requestedEngineAssociation: engine?.requestedEngineAssociation
        || project.engineAssociation
        || null,
      errorCode: engine?.errorCode || "ENGINE_ROOT_UNRESOLVED",
      error: engine?.error
        || "Could not resolve Unreal Engine installation. Set engineRoot or config.defaultEngineRoot.",
    };
  }

  const requestedTarget = String(args.target || "").trim();
  const target = resolveBuildTarget(requestedTarget, project);
  const platform = String(
    args.platform
    || selection.config.defaultPlatform
    || process.env.UNREAL_PLATFORM
    || defaultPlatform(),
  ).trim();
  const configuration = String(
    args.configuration
    || selection.config.defaultConfiguration
    || process.env.UNREAL_CONFIGURATION
    || "Development",
  ).trim();

  return {
    ok: true,
    ...selection,
    build: {
      engineRoot: engine.engineRoot,
      buildTool: engine.buildTool,
      buildToolKind: engine.buildToolKind,
      buildBat: engine.buildBat,
      engineSource: engine.source,
      engineWarning: engine.warning,
      requestedEngineAssociation: engine.requestedEngineAssociation || project.engineAssociation,
      projectPath,
      projectFile: path.basename(projectPath),
      projectDir: path.dirname(projectPath),
      projectName: projectNameFromPath(projectPath),
      target,
      requestedTarget: requestedTarget || null,
      targetWasAlias: requestedTarget.toLowerCase() === "editor",
      platform,
      configuration,
      allTargets: project.allTargets,
      engineAssociation: project.engineAssociation,
    },
  };
}

module.exports = { defaultPlatform, resolveBuildPlan, resolveBuildTarget };
