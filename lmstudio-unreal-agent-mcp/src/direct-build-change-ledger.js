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

/** A quoted or angled include as compilers compare it: one slash style, no
 * empty or "." segments. */
function normalizeInclude(value) {
  const parts = [];
  for (const part of String(value || "").replace(/\\/g, "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === ".." && parts.length && parts.at(-1) !== "..") { parts.pop(); continue; }
    parts.push(part);
  }
  return parts.join("/");
}

/** The includes a file had before this runtime first changed it. null: unknown. */
function includeSet(content) {
  if (content === null || content === undefined) return null;
  const text = Buffer.isBuffer(content) ? content.toString("utf8") : String(content);
  return new Set([...text.matchAll(/^[ \t]*#[ \t]*include[ \t]+[<"]([^>"\r\n]+)[>"]/gmu)].map(match => normalizeInclude(match[1])));
}

class BuildChangeLedger {
  constructor({ maxPaths = MAX_PENDING_PATHS } = {}) {
    this.maxPaths = maxPaths;
    this.sequence = 0;
    this.projects = new Map();
  }

  // Records committed project-relative paths; non-Source paths are ignored.
  // priorContents maps a given path to its content before this commit ("" for a
  // new file). Only the first commit since the last good build sets the
  // baseline: an include present then is not one this runtime introduced.
  record(projectFile, relativePaths, priorContents = null) {
    if (!projectFile) return;
    const projectDir = path.dirname(path.resolve(projectFile));
    const key = projectKey(projectFile);
    const entry = this.projects.get(key) || { paths: new Map(), baselines: new Map(), overflowed: false };
    for (const item of relativePaths || []) {
      const relative = sourceRelativePath(projectDir, item);
      if (!relative) continue;
      if (!entry.paths.has(relative)) {
        const prior = priorContents ? priorContents[item] ?? priorContents[relative] : undefined;
        entry.baselines.set(relative, includeSet(prior));
      }
      entry.paths.delete(relative);
      entry.paths.set(relative, ++this.sequence);
      if (entry.paths.size > this.maxPaths) {
        const oldest = entry.paths.keys().next().value;
        entry.paths.delete(oldest);
        entry.baselines.delete(oldest);
        entry.overflowed = true;
        entry.overflowSequence = this.sequence;
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

  /** Includes of a pending file before its first change since the last good
   * build; null when that content was not recorded. */
  baselineIncludes(projectFile, relativePath) {
    const value = this.projects.get(projectKey(projectFile))?.baselines.get(String(relativePath || "").replace(/\\/g, "/"));
    return value ? new Set(value) : null;
  }

  // A successful build covers changes recorded up to the sequence observed
  // when it started; edits committed while it ran stay pending.
  settle(projectFile, sequence) {
    const key = projectKey(projectFile);
    const entry = this.projects.get(key);
    if (!entry) return;
    for (const [relative, recordedAt] of entry.paths) {
      if (recordedAt <= sequence) { entry.paths.delete(relative); entry.baselines?.delete(relative); }
    }
    entry.overflowed = entry.overflowSequence > sequence;
    if (!entry.paths.size) this.projects.delete(key);
  }
}

module.exports = { BuildChangeLedger, MAX_PENDING_PATHS, includeSet, normalizeInclude };
