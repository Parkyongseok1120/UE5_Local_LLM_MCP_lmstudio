"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { fail, hash } = require("../../shared-tool-core/files");
const WRITABLE = new Set([".cs", ".json", ".csv", ".txt", ".md", ".asmdef", ".asmref", ".shader", ".hlsl", ".cginc", ".uxml", ".uss"]);
const PROTECTED = new Set([".prefab", ".unity", ".asset", ".meta"]);
function identity(root) { return process.platform === "win32" ? root.replace(/\\/g, "/").toLowerCase() : root; }
function projectPolicy(input) {
  if (!input || !path.isAbsolute(input)) fail("project_required", "UNITY_PROJECT_ROOT must be an absolute Unity project path");
  const root = fs.realpathSync.native(input);
  for (const marker of ["Assets", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"]) if (!fs.existsSync(path.join(root, marker))) fail("not_unity_project", `Missing ${marker}`);
  const resolve = (relative, write = false) => {
    if (typeof relative !== "string" || !relative || path.isAbsolute(relative) || relative.includes("\\") || relative.includes(":")) fail("invalid_path", "Use a project-relative slash-separated path");
    const parts = relative.split("/");
    if (parts.some(p => !p || p === "." || p === ".." || p.startsWith("."))) fail("invalid_path", "Hidden/traversal path segments are forbidden");
    if (!["Assets", "Packages", "ProjectSettings"].includes(parts[0])) fail("path_denied", "Only Assets, Packages and ProjectSettings may be observed");
    const ext = path.extname(relative).toLowerCase();
    if (write && (parts[0] !== "Assets" || !WRITABLE.has(ext) || PROTECTED.has(ext))) fail("path_denied", "Write requires an allowed text file inside Assets; Unity assets use the Bridge");
    let current = root;
    for (let i = 0; i < parts.length; i++) {
      current = path.join(current, parts[i]);
      let stat;
      try { stat = fs.lstatSync(current); } catch (e) {
        if (e.code === "ENOENT" && write && i === parts.length - 1) break;
        throw e;
      }
      if (stat.isSymbolicLink()) fail("symlink_denied", "Symlink/junction components are forbidden");
      const real = fs.realpathSync(current);
      const rel = path.relative(root, real);
      if (rel.startsWith(`..${path.sep}`) || rel === ".." || path.isAbsolute(rel)) fail("path_denied", "Real path escapes the project");
      if (write && i === parts.length - 1 && stat.nlink > 1) fail("hardlink_denied", "Hard-linked mutation targets are forbidden");
    }
    return current;
  };
  // Internal state is separate from the model-facing file policy.
  const internal = path.join(root, "Library", "EvidenceFirst");
  for (const directory of [path.join(root, "Library"), internal]) {
    if (fs.existsSync(directory) && fs.lstatSync(directory).isSymbolicLink()) fail("symlink_denied", "Internal state directory must not be a symlink");
  }
  return { root, projectIdentity: hash(identity(root)), resolve, stateRoot: internal, discovery: path.join(internal, "bridge.json") };
}
module.exports = { projectPolicy, identity };
