"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { exists, uniquePaths } = require("./unreal-project-core");

const DEFAULT_EPIC_ROOT = path.join("C:", "Program Files", "Epic Games");

function engineFolderFromAssociation(value) {
  if (!value) return null;
  const match = String(value).trim().match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i);
  return match ? `UE_${match[1]}` : null;
}

function engineAssociationVersion(value) {
  return String(value || "").trim().match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i)?.[1] || "";
}

function engineAssociationsMatch(left, right) {
  const leftText = String(left || "").trim();
  const rightText = String(right || "").trim();
  const leftVersion = engineAssociationVersion(leftText);
  const rightVersion = engineAssociationVersion(rightText);
  if (leftVersion || rightVersion) {
    return Boolean(leftVersion && rightVersion && leftVersion === rightVersion);
  }
  return Boolean(leftText && leftText === rightText);
}

function configuredEngineRootForAssociation(engineAssociation, config) {
  const association = String(engineAssociation || "").trim();
  const roots = config?.engineRootsByAssociation;
  if (!association || !roots || typeof roots !== "object" || Array.isArray(roots)) return "";
  if (!Object.prototype.hasOwnProperty.call(roots, association)) return "";
  return String(roots[association] || "").trim();
}

function compareEngineFolders(left, right) {
  const parts = (value) => {
    const match = String(value || "").match(/UE[_ -]?(\d+(?:\.\d+)*)/i);
    return match ? match[1].split(".").map((part) => Number(part)) : [];
  };
  const leftParts = parts(left);
  const rightParts = parts(right);
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
    const delta = Number(leftParts[index] || 0) - Number(rightParts[index] || 0);
    if (delta !== 0) return delta;
  }
  return String(left || "").localeCompare(String(right || ""));
}

function engineBuildToolCandidates(engineRoot, hostPlatform = process.platform) {
  const batchRoot = path.join(engineRoot, "Engine", "Build", "BatchFiles");
  const ubtRoot = path.join(engineRoot, "Engine", "Binaries", "DotNET", "UnrealBuildTool");
  if (hostPlatform === "win32") {
    return [
      { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
      { path: path.join(batchRoot, "Build.bat"), kind: "build_bat" },
      { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    ];
  }
  const hostFolder = hostPlatform === "darwin" ? "Mac" : "Linux";
  return [
    { path: path.join(batchRoot, hostFolder, "Build.sh"), kind: "build_sh" },
    { path: path.join(batchRoot, "Build.sh"), kind: "build_sh" },
    { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
  ];
}

async function resolveEngineBuildTool(engineRoot, hostPlatform = process.platform) {
  for (const candidate of engineBuildToolCandidates(engineRoot, hostPlatform)) {
    if (await exists(candidate.path)) return candidate;
  }
  return null;
}

function defaultEngineLocations(
  hostPlatform = process.platform,
  env = process.env,
  homeDirectory = os.homedir(),
) {
  if (hostPlatform === "win32") {
    return uniquePaths([
      env.ProgramFiles ? path.join(env.ProgramFiles, "Epic Games") : "",
      env["ProgramFiles(x86)"] ? path.join(env["ProgramFiles(x86)"], "Epic Games") : "",
      DEFAULT_EPIC_ROOT,
    ], hostPlatform);
  }
  if (hostPlatform === "darwin") {
    return ["/Users/Shared/Epic Games", "/Applications/Epic Games"];
  }
  return uniquePaths([
    path.join(homeDirectory, "UnrealEngine"),
    path.join(homeDirectory, "Epic Games"),
    "/opt/UnrealEngine",
    "/opt/Epic Games",
  ], hostPlatform);
}

function engineRootNumericVersion(engineRoot) {
  const versionPath = path.join(engineRoot, "Engine", "Build", "Build.version");
  try {
    const payload = JSON.parse(fs.readFileSync(versionPath, "utf8"));
    const major = Number(payload.MajorVersion);
    const minor = Number(payload.MinorVersion);
    if (Number.isInteger(major) && Number.isInteger(minor)) return `${major}.${minor}`;
  } catch {
    // Fall back to a conventional UE_<major>.<minor> directory name.
  }
  return path.basename(String(engineRoot || "")).match(/UE[_ -]?(\d+(?:\.\d+)*)/i)?.[1] || "";
}

function engineRootMatchesNumericAssociation(engineRoot, association) {
  const requested = String(association || "").match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i)?.[1] || "";
  const actual = engineRootNumericVersion(engineRoot);
  return !requested || Boolean(actual && requested === actual);
}

module.exports = {
  compareEngineFolders,
  configuredEngineRootForAssociation,
  defaultEngineLocations,
  engineAssociationsMatch,
  engineBuildToolCandidates,
  engineFolderFromAssociation,
  engineRootMatchesNumericAssociation,
  engineRootNumericVersion,
  resolveEngineBuildTool,
};
