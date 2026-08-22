"use strict";

const path = require("node:path");
const { filesystemPathIdentity } = require("./filesystem-path-identity");

const PROTECTED_MUTATION_SEGMENTS = new Set([
  "saved",
  "binaries",
  "intermediate",
  "deriveddatacache",
  ".git",
  ".vs",
]);

function mutationPathParts(value) {
  return String(value || "")
    .replace(/\\/gu, "/")
    .split("/")
    .filter((part) => part && part !== ".");
}

function isProtectedMutationSegment(value) {
  return PROTECTED_MUTATION_SEGMENTS.has(String(value || "").toLowerCase());
}

function isDeniedMutationPath(value) {
  return mutationPathParts(value).some(isProtectedMutationSegment);
}

function classifyDirectMutationRelativePath(
  relativePath,
  activeDescriptorName,
  hostPlatform = process.platform,
) {
  const parts = mutationPathParts(relativePath);
  if (!parts.length || parts.includes("..") || parts.some(isProtectedMutationSegment)) return null;
  const top = parts[0].toLowerCase();
  if (top === "source" && parts.length >= 2) return "project_source";
  if (top === "config" && parts.length >= 2) return "project_config";
  if (top === "plugins" && parts.length >= 4 && parts[2].toLowerCase() === "source") {
    return "plugin_source";
  }
  if (top === "plugins" && parts.length === 3) {
    const expectedDescriptor = `${parts[1]}.uplugin`;
    if (filesystemPathIdentity(parts[2], hostPlatform, { stripProjectUri: false })
      === filesystemPathIdentity(expectedDescriptor, hostPlatform, { stripProjectUri: false })) {
      return "plugin_descriptor";
    }
  }
  const activeDescriptorExtension = filesystemPathIdentity(
    path.extname(String(activeDescriptorName || "")),
    hostPlatform,
    { stripProjectUri: false },
  );
  if (parts.length === 1 && activeDescriptorExtension === ".uproject") {
    if (filesystemPathIdentity(parts[0], hostPlatform, { stripProjectUri: false })
      === filesystemPathIdentity(activeDescriptorName, hostPlatform, { stripProjectUri: false })) {
      return "project_descriptor";
    }
  }
  return null;
}

function assertDirectMutationScope({
  absolutePath,
  activeProject,
  realPath,
  relativePath,
  realRelativePath,
  hostPlatform = process.platform,
}) {
  if (isDeniedMutationPath(absolutePath)
    || isDeniedMutationPath(realPath)
    || isDeniedMutationPath(relativePath)
    || isDeniedMutationPath(realRelativePath)) {
    throw new Error(
      "Mutation path is not allowed in protected directories: Saved, Binaries, Intermediate, DerivedDataCache, .git, or .vs.",
    );
  }
  const descriptorName = path.basename(String(activeProject || ""));
  const lexicalScope = classifyDirectMutationRelativePath(
    relativePath,
    descriptorName,
    hostPlatform,
  );
  const realScope = classifyDirectMutationRelativePath(
    realRelativePath,
    descriptorName,
    hostPlatform,
  );
  if (!lexicalScope || !realScope) {
    throw new Error(
      "Mutation path is not allowed. Use Source/**, Config/**, Plugins/<plugin>/Source/**, Plugins/<plugin>/<plugin>.uplugin, or the active .uproject.",
    );
  }
  if (lexicalScope !== realScope) {
    throw new Error("Mutation path is not allowed to change writable scope through a symlink or junction.");
  }
  return lexicalScope;
}

module.exports = {
  PROTECTED_MUTATION_SEGMENTS,
  assertDirectMutationScope,
  classifyDirectMutationRelativePath,
  isDeniedMutationPath,
  isProtectedMutationSegment,
};
