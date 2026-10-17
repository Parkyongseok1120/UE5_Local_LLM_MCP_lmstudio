"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { fail, hash } = require("../../shared-tool-core/files");
const WRITABLE = new Set([".cs", ".json", ".csv", ".txt", ".md", ".asmdef", ".asmref", ".shader", ".hlsl", ".cginc", ".uxml", ".uss"]);
const PROTECTED = new Set([".prefab", ".unity", ".asset", ".meta"]);
// package://<name>/<path> reads the resolved source of an installed package,
// read-only. The name must be a dependency of Packages/packages-lock.json.
const PACKAGE_URI = "package://";
const PACKAGE_READABLE = new Set([".cs", ".asmdef", ".json", ".md", ".uss", ".uxml"]);
const PACKAGE_NAME = /^[a-z0-9][a-z0-9._-]{0,127}$/;
function packageDirectory(root, name) {
  let lock;
  try { lock = JSON.parse(fs.readFileSync(path.join(root, "Packages", "packages-lock.json"), "utf8").replace(/^\uFEFF/, "")); }
  catch { fail("package_lock_unreadable", "Packages/packages-lock.json is required to resolve package://"); }
  const entry = lock && typeof lock.dependencies === "object" ? lock.dependencies[name] : undefined;
  if (!entry || typeof entry !== "object") fail("package_not_found", `Package is not a dependency in packages-lock.json: ${name}`);
  const source = String(entry.source || "registry"), version = String(entry.version || "");
  if (source === "builtin") fail("package_source_unavailable", "Built-in packages are compiled into the Editor and have no readable source");
  if (source === "embedded") return path.join(root, "Packages", name);
  if (source === "local") {
    if (!version.startsWith("file:")) fail("package_source_unavailable", "Local package has no file: location");
    return path.resolve(root, "Packages", version.slice(5));
  }
  const cache = path.join(root, "Library", "PackageCache");
  let names = [];
  try { names = fs.readdirSync(cache); } catch { /* resolved below */ }
  const exact = `${name}@${version}`;
  // Newer cache layouts can use opaque suffixes. A unique directory is not
  // proof that it holds the locked version: stale caches survive lock changes.
  // Registry manifests can bind an opaque folder to the exact name/version;
  // a manifest's semantic version cannot verify a Git URL or commit identity.
  const candidates = names.includes(exact) ? [exact] : names.filter(entryName => {
    if (source !== "registry" || !entryName.startsWith(`${name}@`)) return false;
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(cache, entryName, "package.json"), "utf8").replace(/^\uFEFF/, ""));
      return manifest?.name === name && manifest.version === version;
    } catch { return false; }
  });
  if (candidates.length !== 1) fail("package_not_cached", candidates.length
    ? "Several cached copies match the locked package; the source cannot be identified uniquely"
    : "The locked package source could not be verified in Library/PackageCache; open the project in Unity to resolve packages");
  return path.join(cache, candidates[0]);
}
function resolvePackage(root, uri, write) {
  if (write) fail("path_denied", "package:// is read-only");
  const rest = uri.slice(PACKAGE_URI.length);
  const parts = rest.split("/");
  const name = parts[0];
  if (!PACKAGE_NAME.test(name || "") || rest.includes("\\") || rest.includes(":")) fail("invalid_path", "Use package://<package-name>/<relative path>");
  const inner = parts.slice(1);
  if (inner.some(p => !p || p === "." || p === ".." || p.startsWith("."))) fail("invalid_path", "Hidden/traversal path segments are forbidden");
  const base = packageDirectory(root, name);
  let realBase;
  try { realBase = fs.realpathSync(base); } catch { fail("package_not_cached", "Package source directory is missing"); }
  let current = realBase;
  for (const segment of inner) {
    current = path.join(current, segment);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) fail("symlink_denied", "Symlink/junction components are forbidden");
    const rel = path.relative(realBase, fs.realpathSync(current));
    if (rel.startsWith(`..${path.sep}`) || rel === ".." || path.isAbsolute(rel)) fail("path_denied", "Real path escapes the package");
  }
  const stat = fs.lstatSync(current);
  if (stat.isFile() && !PACKAGE_READABLE.has(path.extname(current).toLowerCase())) fail("path_denied", "Package reads allow .cs .asmdef .json .md .uss .uxml");
  return current;
}
function identity(root) { return process.platform === "win32" ? root.replace(/\\/g, "/").toLowerCase() : root; }
function projectPolicy(input) {
  if (!input || !path.isAbsolute(input)) fail("project_required", "UNITY_PROJECT_ROOT must be an absolute Unity project path");
  const root = fs.realpathSync.native(input);
  for (const marker of ["Assets", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"]) if (!fs.existsSync(path.join(root, marker))) fail("not_unity_project", `Missing ${marker}`);
  const resolve = (relative, write = false) => {
    if (typeof relative === "string" && relative.startsWith(PACKAGE_URI)) return resolvePackage(root, relative, write);
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
module.exports = { projectPolicy, identity, PACKAGE_URI };
