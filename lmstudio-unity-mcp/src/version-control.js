"use strict";

const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { bounded, fail } = require("../../shared-tool-core/files");

const MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024;

function runGit(cwd, args) {
  try {
    return execFileSync("git", ["-c", "core.quotepath=false", ...args], {
      cwd,
      encoding: "utf8",
      timeout: 10000,
      maxBuffer: MAX_GIT_OUTPUT_BYTES,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    }).replace(/\r\n/g, "\n").trimEnd();
  } catch (error) {
    if (error.code === "ENOENT") fail("git_unavailable", "Git executable was not found");
    if (error.code === "ENOBUFS") fail("git_output_too_large", "Git output exceeded the bounded read limit");
    const message = String(error.stderr || error.message || "Git command failed").trim().slice(0, 1000);
    fail("git_command_failed", message);
  }
}

function validRevision(value, name) {
  const revision = String(value || "").trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._/@{}~^:-]{0,199}$/u.test(revision)) {
    fail("invalid_revision", `${name} must be an explicit Git revision without options or whitespace`);
  }
  return revision;
}

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

class VersionControl {
  constructor(policy) {
    this.policy = policy;
  }

  scope() {
    const gitRoot = path.resolve(runGit(this.policy.root, ["rev-parse", "--show-toplevel"]));
    if (!contained(gitRoot, this.policy.root)) fail("git_scope_mismatch", "Unity project is outside the detected Git worktree");
    const projectPrefix = path.relative(gitRoot, this.policy.root).split(path.sep).join("/");
    return { gitRoot, projectPrefix };
  }

  pathspecs(gitRoot, projectPrefix, paths) {
    if (!Array.isArray(paths) || paths.length === 0) return [projectPrefix || "."];
    return paths.map((value) => {
      const display = String(value || "").trim().replace(/\\/g, "/");
      if (!display || path.posix.isAbsolute(display) || display.split("/").some(part => !part || part === "." || part === "..")) {
        fail("invalid_path", "Git paths must be project-relative slash-separated paths without traversal");
      }
      const candidate = path.resolve(this.policy.root, ...display.split("/"));
      if (!contained(this.policy.root, candidate)) fail("path_denied", "Git path escapes the Unity project");
      return projectPrefix ? `${projectPrefix}/${display}` : display;
    });
  }

  projectPath(repositoryPath, projectPrefix) {
    const normalized = String(repositoryPath || "").replace(/\\/g, "/");
    if (!projectPrefix) return normalized;
    return normalized === projectPrefix ? "."
      : normalized.startsWith(`${projectPrefix}/`) ? normalized.slice(projectPrefix.length + 1) : normalized;
  }

  call(args) {
    const { gitRoot, projectPrefix } = this.scope();
    const pathspecs = this.pathspecs(gitRoot, projectPrefix, args.paths);
    const head = runGit(gitRoot, ["rev-parse", "HEAD"]);
    const branch = runGit(gitRoot, ["branch", "--show-current"]);
    if (args.action === "status") {
      const lines = runGit(gitRoot, ["status", "--porcelain=v1", "--untracked-files=all", "--", ...pathspecs])
        .split("\n").filter(Boolean);
      const limit = args.limit ?? 100;
      const items = lines.slice(0, limit).map(line => ({
        status: line.slice(0, 2),
        path: this.projectPath(line.slice(3), projectPrefix),
      }));
      return bounded({ status: "observed", action: "status", branch: branch || null, head,
        items, total: lines.length, truncated: items.length < lines.length }, args.byteBudget);
    }
    if (args.action === "log") {
      const limit = args.limit ?? 10;
      const output = runGit(gitRoot, ["log", `-n${limit}`, "--format=%H%x09%h%x09%aI%x09%s", "--", ...pathspecs]);
      const items = output.split("\n").filter(Boolean).map(line => {
        const [commit, shortCommit, authoredAt, ...subject] = line.split("\t");
        return { commit, shortCommit, authoredAt, subject: subject.join("\t") };
      });
      return bounded({ status: "observed", action: "log", branch: branch || null, head, items }, args.byteBudget);
    }
    if (args.action !== "diff") fail("invalid_arguments", "Unknown version-control action");

    const comparison = args.comparison || "worktree";
    const command = ["diff", "--no-ext-diff", "--unified=3"];
    if (comparison === "staged") command.push("--cached");
    else if (comparison === "last_commit") command.push("HEAD^", "HEAD");
    else if (comparison === "range") {
      command.push(validRevision(args.base, "base"), validRevision(args.head, "head"));
    } else if (comparison !== "worktree") fail("invalid_arguments", "Unknown Git comparison");
    command.push("--", ...pathspecs);
    const output = runGit(gitRoot, command);
    const lines = output ? output.split("\n") : [];
    const startLine = args.startLine ?? 1;
    const limit = args.limit ?? 200;
    const selected = lines.slice(startLine - 1, startLine - 1 + limit);
    const returnedLineCount = selected.length;
    const endLine = returnedLineCount ? startLine + returnedLineCount - 1 : null;
    const hasMore = startLine - 1 + returnedLineCount < lines.length;
    return bounded({ status: "observed", action: "diff", comparison, branch: branch || null, head,
      paths: (args.paths || []).map(String), startLine, endLine, returnedLineCount,
      totalLines: lines.length, hasMore, nextStartLine: hasMore ? startLine + returnedLineCount : null,
      text: selected.join("\n") }, args.byteBudget);
  }
}

module.exports = { VersionControl, validRevision };
