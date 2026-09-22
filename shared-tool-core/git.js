"use strict";
// Read-only evidence; immutable bounded pages, no shell, planner or index writes.
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const { bounded, fail, hash } = require("./files");
const { resolveCanonicalAbsolutePath: canonical, canonicalAbsolutePathIdentity: identity } = require("../lmstudio-unreal-agent-mcp/src/filesystem-path-identity");
const TTL = 300000, CACHE_BYTES = 16 * 1024 * 1024;
function recordPhase(timing, phase, elapsedMs, outputBytes = 0, subprocess = true) {
  if (!timing) return;
  if (subprocess) timing.subprocessMs += elapsedMs;
  timing.phaseMs[phase] = Number(timing.phaseMs[phase] || 0) + elapsedMs;
  if (subprocess) timing.subprocessOutputBytes += outputBytes;
  else timing.serializationBytes = Number(timing.serializationBytes || 0) + outputBytes;
}
function recordTiming(timing, phase, startedAt, outputBytes = 0, failed = false) {
  if (!timing) return;
  const elapsedMs = Math.max(0, Date.now() - startedAt);
  timing.commandCount += 1;
  recordPhase(timing, phase, elapsedMs, outputBytes);
  timing.phaseCommands[phase] = Number(timing.phaseCommands[phase] || 0) + 1;
  if (failed) timing.failedCommandCount += 1;
}
function newTiming(queryHash, cacheHit = false) {
  return { queryHash, cacheHit, commandCount: 0, failedCommandCount: 0,
    subprocessMs: 0, phaseMs: {}, phaseCommands: {}, subprocessOutputBytes: 0, serializationBytes: 0,
    returnedRows: 0, payloadBytes: 0, envelopeBytes: 0 };
}
function run(cwd, args, options = {}) {
  const timing = options.timing, phase = options.phase || "git";
  const startedAt = Date.now();
  try {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith("GIT_")));
    const output = execFileSync("git", ["--literal-pathspecs", "--no-pager", "-c", "color.ui=false", "-c", "core.fsmonitor=false", ...args], {
      cwd, input: options.input, timeout: 10000, maxBuffer: 4 * 1024 * 1024, windowsHide: true,
      env: { ...env, GIT_OPTIONAL_LOCKS: "0", GIT_TERMINAL_PROMPT: "0" },
      stdio: [options.input === undefined ? "ignore" : "pipe", "pipe", "pipe"],
    });
    recordTiming(timing, phase, startedAt, output?.length || 0);
    return output;
  } catch (e) {
    recordTiming(timing, phase, startedAt, 0, true);
    if (options.optional && e.status === 1) return null;
    if (e.code === "ENOENT") fail("git_unavailable", "Git executable was not found");
    if (e.code === "ENOBUFS") fail("git_output_too_large", "Evidence exceeds 4 MiB; narrow paths or request one file");
    fail("git_command_failed", String(e.stderr || e.message).trim().slice(0, 1000));
  }
}
function text(buffer) {
  try { return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(buffer); }
  catch { fail("unsupported_encoding", "Evidence is not UTF-8; no lossy path or content returned"); }
}
const line = b => text(b).replace(/\r?\n$/, "");
function boundedQueryPaths(values, prefix) {
  const retained = [];
  let chars = 0;
  for (const value of values) {
    if (retained.length >= 8 || chars + value.length > 4096) break;
    retained.push(value); chars += value.length;
  }
  return {
    [prefix]: retained,
    [`${prefix}Count`]: values.length,
    [`${prefix}Omitted`]: values.length - retained.length,
    [`${prefix}Sha256`]: hash(JSON.stringify(values)),
  };
}
function validRevision(value, name = "revision") {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._/@{}~^:-]{0,199}$/u.test(value))
    fail("invalid_revision", `${name} must be an explicit revision without options or whitespace`);
  return value;
}
function validDateFilter(value, name) {
  if (typeof value !== "string" || value.length > 64
    || !/^\d{4}-\d{2}-\d{2}(?:[Tt][0-2]\d:[0-5]\d(?::[0-6]\d(?:\.\d{1,9})?)?(?:[Zz]|[+-][0-2]\d:[0-5]\d))?$/u.test(value)
    || Number.isNaN(Date.parse(value))) {
    fail("invalid_arguments", `${name} must be YYYY-MM-DD or an RFC 3339 timestamp with timezone`);
  }
  return value;
}
function validAuthorQuery(value) {
  if (typeof value !== "string" || !value.trim() || value.length > 320 || /[\0\r\n]/u.test(value))
    fail("invalid_arguments", "authorQuery must be a non-empty bounded author name or email fragment");
  return value;
}
function comparisonArgs(a) {
  if (!["range", "worktree", "staged", "last_commit"].includes(a.comparison))
    fail("invalid_arguments", "Set comparison explicitly; range requires base/head, worktree and staged forbid them");
  if (a.comparison === "range") {
    if (!a.base || !a.head) fail("invalid_arguments", "comparison=range requires both base and head");
    validRevision(a.base, "base"); validRevision(a.head, "head");
  } else if (a.base !== undefined || a.head !== undefined) fail("invalid_arguments", "base/head require comparison=range; nothing executed");
}
class VersionControl {
  constructor(policy) { this.policy = policy; this.cache = new Map(); this.secret = crypto.randomBytes(32); }
  scope(timing) {
    const root = canonical(this.policy.root), gitRoot = canonical(line(run(root, ["rev-parse", "--show-toplevel"], { timing, phase: "scope" })));
    const relative = path.relative(gitRoot, root);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) fail("git_scope_mismatch", "Project is outside Git worktree");
    const gitDir = canonical(line(run(root, ["rev-parse", "--absolute-git-dir"], { timing, phase: "scope" })));
    const objectFormat = line(run(root, ["rev-parse", "--show-object-format"], { timing, phase: "scope" }));
    return { gitRoot, projectPrefix: relative.split(path.sep).join("/"), workspaceRoot: root,
      repositoryRoot: gitRoot, workspaceIdentity: hash(identity(root)),
      repositoryIdentity: hash(JSON.stringify([identity(gitRoot), identity(gitDir), objectFormat])), objectFormat };
  }
  pathspecs(gitRoot, prefix, paths) {
    if (paths === undefined) return [prefix || "."];
    if (!Array.isArray(paths) || paths.length > 16) fail("invalid_path", "Use up to 16 relative literal paths");
    if (!paths.length) return [prefix || "."];
    return paths.map(p => {
      if (typeof p !== "string" || !p || /[\\:\0]/u.test(p) || path.isAbsolute(p) || p.split("/").some(v => !v || v === "." || v === ".."))
        fail("invalid_path", "Use workspace-relative slash paths without traversal or pathspec magic");
      return prefix ? `${prefix}/${p}` : p;
    });
  }
  projectPath(p, prefix) { return !prefix ? p : p.startsWith(`${prefix}/`) ? p.slice(prefix.length + 1) : null; }
  resolve(s, ref, timing) { return line(run(s.gitRoot, ["rev-parse", "--verify", "--end-of-options", `${validRevision(ref)}^{commit}`], { timing, phase: "resolve" })); }
  comparison(s, a, timing) {
    const h = run(s.gitRoot, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], { optional: true, timing, phase: "resolve" });
    const currentHead = h ? line(h) : null;
    if (a.comparison === "range") {
      const base = this.resolve(s, a.base, timing), head = this.resolve(s, a.head, timing);
      return { args: [base, head], facts: { comparison: "range", base, head, currentHead } };
    }
    if (a.comparison === "last_commit") {
      if (!currentHead) fail("invalid_revision", "No commit exists");
      const parents = line(run(s.gitRoot, ["rev-list", "--parents", "-n1", currentHead], { timing, phase: "resolve" })).split(" ").slice(1);
      if (parents.length > 1) fail("invalid_arguments", "Use comparison=range with an explicit merge parent");
      const base = parents[0] || line(run(s.gitRoot, ["hash-object", "-t", "tree", "--stdin"], { input: Buffer.alloc(0), timing, phase: "resolve" }));
      return { args: [base, currentHead], facts: { comparison: "last_commit", base, head: currentHead, currentHead } };
    }
    return { args: a.comparison === "staged" ? ["--cached"] : [],
      facts: { comparison: a.comparison, base: a.comparison === "staged" ? currentHead : null, head: null, currentHead } };
  }
  cursor(id, offset) { const body = `${id}.${offset}`; return `${body}.${crypto.createHmac("sha256", this.secret).update(body).digest("hex")}`; }
  lookup(cursor, query, timing) {
    const [id, offsetString, signature, extra] = cursor.split(".");
    const expected = this.cursor(id, offsetString).split(".")[2];
    if (extra || !signature || signature.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) fail("invalid_cursor", "Invalid signed cursor");
    const item = this.cache.get(id), offset = Number(offsetString);
    if (timing) timing.cacheHit = Boolean(item);
    if (!item || Date.now() - item.created > TTL) fail("snapshot_expired", "Snapshot expired; repeat original query");
    if (item.query !== query) fail("invalid_cursor", "Cursor belongs to another query");
    if (!Number.isInteger(offset) || offset < 0 || offset > item.rows.length) fail("invalid_cursor", "Invalid offset");
    return { item, offset };
  }
  save(query, facts, rows, isText) {
    const size = Buffer.byteLength(JSON.stringify([facts, rows]));
    if (size > CACHE_BYTES) fail("git_output_too_large", "Snapshot exceeds cache limit");
    for (const [id, x] of this.cache) if (Date.now() - x.created > TTL) this.cache.delete(id);
    while (this.cache.size >= 8 || [...this.cache.values()].reduce((n, x) => n + x.size, 0) + size > CACHE_BYTES) this.cache.delete(this.cache.keys().next().value);
    const id = crypto.randomBytes(16).toString("hex"), item = { id, query, facts, rows, isText, size, created: Date.now() };
    this.cache.set(id, item); return item;
  }
  page(item, a, offset, timing) {
    const limit = a.limit ?? (item.isText ? 200 : 100);
    // Large immutable changed-file lists already expose signed cursors and
    // pageHasMore. Keep those results small enough for a following model
    // input without changing the default budget for source text or diffs.
    const budget = a.byteBudget ?? (item.facts.action === "changed_files" ? 4096 : 32768);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) fail("invalid_arguments", "limit must be 1..200");
    let count = Math.min(limit, item.rows.length - offset);
    let includeTiming = true;
    const result = () => {
      const rows = item.rows.slice(offset, offset + count), hasMore = offset + count < item.rows.length;
      const timingValue = timing || item.facts.gitTiming;
      return { ...item.facts, ...(includeTiming && timingValue ? { gitTiming: timingValue } : {}),
        snapshotId: item.id, consistency: "immutable_returned_snapshot", hasMore,
        pageStart: offset + 1, pageEnd: offset + count, pageHasMore: hasMore,
        sourceResultComplete: !item.facts.incomplete,
        nextCursor: hasMore ? this.cursor(item.id, offset + count) : null, truncated: hasMore, complete: !hasMore && !item.facts.incomplete,
        ...(item.isText ? { text: rows.join("\n"), startLine: offset + 1, endLine: count ? offset + count : null,
          returnedLineCount: count, totalLines: item.rows.length, nextStartLine: hasMore ? offset + count + 1 : null }
          : { items: rows, returnedCount: count, total: item.facts.incomplete ? null : item.rows.length }) };
    };
    // Reserve space for the engine transport's project binding envelope.
    const serialize = () => {
      const startedAt = Date.now();
      const value = result();
      const bytes = Buffer.byteLength(JSON.stringify(value));
      if (timing) recordPhase(timing, "serialization", Math.max(0, Date.now() - startedAt), bytes, false);
      return { value, bytes };
    };
    while (count > 1 && serialize().bytes > budget - 512) count = Math.max(1, Math.floor(count / 2));
    let serialized = serialize();
    // Diagnostic timing is measured for every call, but it must not make a
    // small, otherwise-valid observation fail its established byte contract.
    // The model-facing evidence remains intact; callers using a larger budget
    // still receive the full timing record for correlation.
    if (includeTiming && serialized.bytes > budget - 512) {
      includeTiming = false;
      serialized = serialize();
    }
    if (timing) {
      timing.returnedRows = count;
      timing.payloadBytes = serialized.bytes;
      // The shared capability returns a structured object. The MCP/direct
      // wrapper adds no hidden cursor or source content; expose the exact
      // serialized envelope boundary separately for host correlation.
      timing.envelopeBytes = Buffer.byteLength(JSON.stringify({ content: serialized.value }));
    }
    return bounded(serialized.value, budget - 512);
  }
  call(a) {
    const timing = newTiming(null);
    if (a.byteBudget !== undefined && (!Number.isInteger(a.byteBudget) || a.byteBudget < 1024 || a.byteBudget > 65536))
      fail("invalid_arguments", "byteBudget must be an integer from 1024 to 65536");
    if (a.cursor !== undefined && (typeof a.cursor !== "string" || !a.cursor || a.cursor.length > 4096))
      fail("invalid_cursor", "cursor must be a bounded string");
    if (!["status", "log", "diff", "changed_files", "diff_file", "read_file"].includes(a.action)) fail("invalid_arguments", "Unknown Git operation");
    const fields = { status: ["paths"], log: ["paths", "revision", "since", "until", "authorQuery"],
      diff: ["paths", "comparison", "base", "head", "startLine"],
      changed_files: ["paths", "comparison", "base", "head"],
      diff_file: ["path", "comparison", "base", "head", "startLine"],
      read_file: ["path", "revision", "startLine"] };
    const allowed = new Set(["action", "cursor", "limit", "byteBudget", ...fields[a.action]]);
    const invalid = Object.keys(a).filter(k => !allowed.has(k));
    if (invalid.length) fail("invalid_arguments", `${a.action} does not accept: ${invalid.join(", ")}`);
    if (a.cursor !== undefined && a.startLine !== undefined) fail("invalid_arguments", "Use cursor or startLine, not both");
    if (["diff", "changed_files", "diff_file"].includes(a.action)) comparisonArgs(a);
    else if (a.comparison !== undefined || a.base !== undefined || a.head !== undefined) fail("invalid_arguments", "This operation forbids comparison/base/head");
    const s = this.scope(timing), paths = this.pathspecs(s.gitRoot, s.projectPrefix, a.path ? [a.path] : a.paths);
    if (a.action === "log") {
      if (a.since !== undefined) validDateFilter(a.since, "since");
      if (a.until !== undefined) validDateFilter(a.until, "until");
      if (a.authorQuery !== undefined) validAuthorQuery(a.authorQuery);
    }
    if (["diff_file", "read_file"].includes(a.action) && !a.path) fail("invalid_arguments", "Exact path required");
    const { cursor, limit, byteBudget, startLine, ...semantic } = a;
    const query = JSON.stringify([s.repositoryIdentity, s.workspaceIdentity, Object.fromEntries(Object.entries(semantic).sort(([a], [b]) => a.localeCompare(b)))]);
    timing.queryHash = hash(query);
    if (cursor) { const hit = this.lookup(cursor, query, timing); return this.page(hit.item, a, hit.offset, timing); }
    const { gitRoot, projectPrefix, ...binding } = s;
    // Worktree conversion can execute clean/process filters even with no-textconv.
    // Do not silently change Git semantics by disabling content filters instead.
    if (a.action === "status" || a.comparison === "worktree") {
       const filters = run(gitRoot, ["config", "--name-only", "--get-regexp", "^filter\\..*\\.(clean|process)$"], { optional: true, timing, phase: "filter_check" });
      if (filters?.length) {
        const configured = new Set(text(filters).trim().split("\n").map(k => k.replace(/^filter\./, "").replace(/\.(clean|process)$/, "")));
         const candidates = run(gitRoot, ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ...paths], { timing, phase: "filter_check" });
        if (candidates.length) {
           const attributes = text(run(gitRoot, ["check-attr", "-z", "--stdin", "filter"], { input: candidates, timing, phase: "filter_check" })).split("\0");
          for (let i = 2; i < attributes.length; i += 3) if (configured.has(attributes[i]))
            fail("external_filter_unsupported", "Selected worktree paths use an external Git filter; inspect committed range evidence or adjust trusted configuration");
        }
      }
    }
    const requestedPaths = a.path ? [a.path] : (Array.isArray(a.paths) ? [...a.paths] : []);
    const facts = { schemaVersion: 1, kind: "git_observation", status: "observed", action: a.action, ...binding,
      queryPathBase: "workspace_root", ...boundedQueryPaths(requestedPaths, "requestedPaths"),
      ...boundedQueryPaths(paths, "resolvedRepositoryPaths"),
       observedAt: new Date().toISOString(), sourceConsistency: "bounded_collection_not_atomic_filesystem_snapshot" };
    let rows = [], isText = false;
    if (a.action === "status") {
      facts.submoduleWorktrees = "not_inspected";
       const tokens = text(run(gitRoot, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all", "--", ...paths], { timing, phase: "status" })).split("\0");
      for (let i = 0; i < tokens.length && tokens[i]; i++) {
        const status = tokens[i].slice(0, 2), p = this.projectPath(tokens[i].slice(3), projectPrefix);
        const oldPath = /[RC]/.test(status) ? this.projectPath(tokens[++i], projectPrefix) : undefined;
        if (p !== null) rows.push({ status, path: p, ...(oldPath !== undefined ? { oldPath } : {}) });
      }
       const head = run(gitRoot, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], { optional: true, timing, phase: "resolve" }); facts.head = head ? line(head) : null;
    } else if (a.action === "log") {
       facts.head = this.resolve(s, a.revision || "HEAD", timing);
      const filters = [
        ...(a.since ? [`--since=${a.since}`] : []),
        ...(a.until ? [`--until=${a.until}`] : []),
        ...(a.authorQuery ? ["--fixed-strings", `--author=${a.authorQuery}`] : []),
      ];
      Object.assign(facts, {
        ...(a.since ? { since: a.since } : {}),
        ...(a.until ? { until: a.until } : {}),
        ...(a.authorQuery ? { authorQuery: a.authorQuery,
          authorQuerySemantics: "literal_fixed_string_name_or_email_fragment" } : {}),
        identitySemantics: "author_and_committer_metadata_only_not_code_ownership_or_work_responsibility",
      });
       const tokens = text(run(gitRoot, ["log", "--no-show-signature", "-n5001", ...filters,
         "--format=%H%x00%aI%x00%an%x00%ae%x00%cI%x00%cn%x00%ce%x00%s%x00", facts.head, "--", ...paths], { timing, phase: "log" })).split("\0");
      for (let i = 0; i + 7 < tokens.length; i += 8) rows.push({
        commit: tokens[i].replace(/^\n/, ""),
        authoredAt: tokens[i + 1],
        authorName: tokens[i + 2],
        authorEmail: tokens[i + 3],
        committedAt: tokens[i + 4],
        committerName: tokens[i + 5],
        committerEmail: tokens[i + 6],
        subject: tokens[i + 7],
      });
      facts.incomplete = rows.length > 5000; rows = rows.slice(0, 5000);
    } else if (a.action === "read_file") {
       facts.head = this.resolve(s, a.revision, timing); facts.path = a.path;
       const entry = text(run(gitRoot, ["ls-tree", "-z", facts.head, "--", paths[0]], { timing, phase: "ls_tree" })).split("\0").filter(Boolean);
      if (entry.length !== 1) fail("not_file", "Path is not one regular committed file");
      const match = /^(100644|100755) blob ([a-f0-9]+)\t([\s\S]+)$/.exec(entry[0]);
      if (!match || match[3] !== paths[0]) fail("not_file", "Only regular committed blobs may be read; trees, symlinks and gitlinks are not file content");
      facts.blobOid = match[2];
       const bytes = run(gitRoot, ["cat-file", "blob", match[2]], { timing, phase: "read_body" });
      if (bytes.includes(0)) fail("unsupported_encoding", "Blob is binary");
      facts.sha256 = hash(bytes); facts.hashSource = "git_blob_bytes";
      facts.encoding = "utf-8"; facts.renderedLineEndings = "LF";
      rows = text(bytes).split(/\r\n|\n|\r/); isText = true;
    } else {
       const comparison = this.comparison(s, a, timing); Object.assign(facts, comparison.facts);
      if (a.action === "diff_file") {
        if (["range", "last_commit"].includes(a.comparison)) {
           const modes = comparison.args.flatMap(ref => text(run(gitRoot, ["ls-tree", "-z", ref, "--", paths[0]], { timing, phase: "ls_tree" })).split("\0").filter(Boolean));
          if (!modes.length || modes.some(entry => !/^(100644|100755) blob [a-f0-9]+\t/.test(entry))) fail("not_file", "diff_file requires one regular file, not a directory, symlink or gitlink");
        } else {
          const target = path.join(gitRoot, paths[0]);
          if (fs.existsSync(target) && !fs.lstatSync(target).isFile()) fail("not_file", "diff_file requires one regular file");
           const indexed = text(run(gitRoot, ["ls-files", "--stage", "-z", "--", paths[0]], { timing, phase: "ls_tree" })).split("\0").filter(Boolean);
          if (!fs.existsSync(target) && !indexed.length) fail("not_file", "Path is not a worktree or indexed file");
          if (indexed.length > 1 || indexed.some(entry => !/^(100644|100755) /.test(entry))) fail("not_file", "diff_file requires one regular indexed file");
        }
      }
      const command = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames=50%"];
      if (a.comparison === "worktree") { command.push("--ignore-submodules=all"); facts.submoduleWorktrees = "not_inspected"; }
      if (a.action === "changed_files") {
         const tokens = text(run(gitRoot, [...command, "--name-status", "-z", ...comparison.args, "--", ...paths], { timing, phase: "diff" })).split("\0");
        for (let i = 0; i < tokens.length && tokens[i];) {
          const code = tokens[i++], first = this.projectPath(tokens[i++], projectPrefix), rename = /^[RC]/.test(code);
          const p = rename ? this.projectPath(tokens[i++], projectPrefix) : first;
          rows.push({ status: ({ M: "modified", A: "added", D: "deleted", R: "renamed", C: "copied", T: "type_changed", U: "unmerged" })[code[0]] || code,
            path: p, ...(rename ? { oldPath: first, similarity: Number(code.slice(1)) } : {}),
            ...(p === null || first === null ? { endpointOutOfScope: true } : {}) });
        }
      } else {
         const output = text(run(gitRoot, [...command, "--unified=3", ...comparison.args, "--", ...paths], { timing, phase: "diff" }));
        rows = output ? output.replace(/\n$/, "").split("\n") : []; isText = true;
      }
    }
    const item = this.save(query, facts, rows, isText), offset = (startLine ?? 1) - 1;
    if (!Number.isInteger(offset) || offset < 0 || (offset > rows.length && rows.length)) fail("invalid_arguments", "startLine outside result");
    return this.page(item, a, Math.min(offset, rows.length), timing);
  }
}
module.exports = { VersionControl, validRevision };
