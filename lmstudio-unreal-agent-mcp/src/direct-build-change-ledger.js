"use strict";

const path = require("node:path");
const { moduleRootForTarget } = require("./automation-source-discovery");

// Project Source files committed by this runtime since that project's last
// successful build, taken from the same commits that produce change receipts.
// In memory only: after a restart nothing is pending and the build precheck
// says so instead of guessing a scope.
const MAX_PENDING_PATHS = 256;

function projectKey(projectFile) {
  const resolved = path.resolve(String(projectFile || ""));
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function sourceRelativePath(projectDir, relativePath) {
  const relative = String(relativePath || "").replace(/^project:\/\//iu, "").replace(/\\/g, "/");
  return relative && moduleRootForTarget(projectDir, relative) ? relative : "";
}

class BuildChangeLedger {
  constructor({ maxPaths = MAX_PENDING_PATHS } = {}) {
    this.maxPaths = maxPaths;
    this.sequence = 0;
    this.projects = new Map();
  }

  // Records committed project-relative paths; non-Source paths are ignored.
  record(projectFile, relativePaths) {
    if (!projectFile) return;
    const projectDir = path.dirname(path.resolve(projectFile));
    const key = projectKey(projectFile);
    const entry = this.projects.get(key) || { paths: new Map(), overflowed: false };
    for (const item of relativePaths || []) {
      const relative = sourceRelativePath(projectDir, item);
      if (!relative) continue;
      entry.paths.delete(relative);
      entry.paths.set(relative, ++this.sequence);
      if (entry.paths.size > this.maxPaths) {
        entry.paths.delete(entry.paths.keys().next().value);
        entry.overflowed = true;
      }
    }
    if (entry.paths.size) this.projects.set(key, entry);
  }

  pending(projectFile) {
    const entry = this.projects.get(projectKey(projectFile));
    return {
      paths: entry ? [...entry.paths.keys()] : [],
      sequence: this.sequence,
      overflowed: entry?.overflowed === true,
    };
  }

  // A successful build covers changes recorded up to the sequence observed
  // when it started; edits committed while it ran stay pending.
  settle(projectFile, sequence) {
    const key = projectKey(projectFile);
    const entry = this.projects.get(key);
    if (!entry) return;
    for (const [relative, recordedAt] of entry.paths) {
      if (recordedAt <= sequence) entry.paths.delete(relative);
    }
    entry.overflowed = false;
    if (!entry.paths.size) this.projects.delete(key);
  }
}

module.exports = { BuildChangeLedger, MAX_PENDING_PATHS };
