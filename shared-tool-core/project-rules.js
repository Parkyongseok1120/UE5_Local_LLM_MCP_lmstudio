"use strict";
const fs = require("node:fs"), path = require("node:path");
const { hash, fail, bounded } = require("./files");
const { stableStatIdentity, snapshotStillCurrent, isBinary, checkReadCancellation } = require("./direct-file-snapshot");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");
const MAX_BYTES = 65536;
const inside = (root, target) => { const r = path.relative(root, target); return r !== ".." && !r.startsWith(`..${path.sep}`) && !path.isAbsolute(r); };
const portable = value => value.split(path.sep).join("/");
function safeChain(root, target) {
  if (!inside(root, target)) fail("invalid_path", "Rule target is outside the allowed root");
  let current = root;
  for (const part of ["", ...path.relative(root, target).split(path.sep).filter(Boolean)]) {
    if (part) current = path.join(current, part);
    try { if (fs.lstatSync(current).isSymbolicLink()) fail("invalid_path", "Rule lookup does not follow symlinks or junctions"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}
function targetDirectory(root, value = ".") {
  if (typeof value !== "string" || path.isAbsolute(value) || /[:\0]/u.test(value)
    || value.replace(/\\/g, "/").split("/").includes("..")) fail("invalid_path", "Use a workspace-relative task path without parent traversal");
  const target = path.resolve(root, value);
  safeChain(root, target);
  let stat;
  try { stat = fs.lstatSync(target); } catch (error) { if (error.code !== "ENOENT") throw error; }
  return stat?.isDirectory() || /[\\/]$/u.test(value) ? target : path.dirname(target);
}
async function snapshot(root, filename, signal) {
  safeChain(root, filename);
  let before;
  try { before = await fs.promises.lstat(filename); } catch (error) { if (error.code === "ENOENT") return null; throw error; }
  if (!before.isFile() || before.isSymbolicLink()) fail("invalid_path", "AGENTS.md must be a regular file");
  checkReadCancellation(signal);
  const handle = await fs.promises.open(filename, "r");
  try {
    if (stableStatIdentity(await handle.stat()) !== stableStatIdentity(before)) fail("snapshot_changed", "Rules changed before reading");
    const buffer = Buffer.alloc(Math.min(MAX_BYTES, before.size));
    let length = 0;
    while (length < buffer.length) {
      checkReadCancellation(signal);
      const read = await handle.read(buffer, length, buffer.length - length, length);
      if (!read.bytesRead) break;
      length += read.bytesRead;
    }
    safeChain(root, filename);
    if (length !== buffer.length || !await snapshotStillCurrent(filename, before, handle)) fail("snapshot_changed", "Rules changed while reading");
    if (isBinary(buffer)) fail("unsupported_encoding", "Rules must be UTF-8 text");
    const truncated = before.size > length;
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(buffer, { stream: truncated }); }
    catch { fail("unsupported_encoding", "Rules must be UTF-8 text"); }
    // A clipped last line is not reported as a complete source line.
    if (truncated) text = text.slice(0, text.lastIndexOf("\n") + 1);
    const lines = text.split(/\r?\n/u);
    if (text.endsWith("\n")) lines.pop();
    const headings = [];
    let fence = null;
    lines.forEach((line, index) => {
      const marker = /^\s*(`{3,}|~{3,})/u.exec(line);
      if (marker) { if (!fence) fence = marker[1][0]; else if (marker[1][0] === fence) fence = null; return; }
      const heading = !fence && /^(#{1,6})\s+(.+?)\s*#*\s*$/u.exec(line);
      if (heading) headings.push({ title: heading[2].slice(0, 160), level: heading[1].length, startLine: index + 1 });
    });
    headings.forEach((heading, index) => {
      heading.endLine = headings.slice(index + 1).find(next => next.level <= heading.level)?.startLine - 1 || lines.length;
    });
    const digest = hash(buffer);
    return { lines, headings, metadata: { path: portable(path.relative(root, filename)),
      ruleId: hash(canonicalAbsolutePathIdentity(filename)), hash: digest, hashScope: truncated ? "first_65536_bytes" : "whole_file",
      version: hash(`${digest}:${stableStatIdentity(before)}`), sizeBytes: before.size, observedBytes: length, truncated,
      observedLines: lines.length, appliesTo: portable(path.relative(root, path.dirname(filename))) || "." } };
  } finally { await handle.close(); }
}
async function projectRules(binding, git, args = {}, request = {}) {
  checkReadCancellation(request.signal);
  const workspaceRoot = fs.realpathSync(binding.root);
  const target = targetDirectory(workspaceRoot, args.path);
  let root = workspaceRoot, scopeSource = "git";
  try { root = (await git.scope(undefined, request.signal)).gitRoot; }
  catch (error) {
    // No Git boundary is proved: restrict lookup to the workspace and disclose it.
    if (!["git_command_failed", "git_unavailable"].includes(error.code)) throw error;
    scopeSource = "workspace_fallback";
  }
  safeChain(root, target);
  const directories = [], snapshots = [];
  let directory = target;
  while (true) {
    directories.unshift(directory);
    if (directory === root) break;
    if (directories.length >= 64 || !inside(root, path.dirname(directory))) fail("invalid_path", "Rule scope exceeds the root or depth limit");
    directory = path.dirname(directory);
  }
  for (const dir of directories) {
    const value = await snapshot(root, path.join(dir, "AGENTS.md"), request.signal);
    if (value) snapshots.push(value);
  }
  const setHash = hash(JSON.stringify(snapshots.map(s => [s.metadata.ruleId, s.metadata.version])));
  const base = { status: "observed", schemaVersion: 1, kind: "project_rules", projectIdentity: binding.projectIdentity,
    workspaceIdentity: hash(canonicalAbsolutePathIdentity(workspaceRoot)), canonicalProjectRoot: workspaceRoot,
    repositoryRoot: root, scopeSource, taskPath: args.path || ".", setHash, totalRules: snapshots.length,
    authority: "project_documents_not_execution_authority", effectiveByteBudget: args.byteBudget || 8192, budgetScope: "whole_response" };
  const budget = base.effectiveByteBudget;
  if (args.action === "read") {
    const rule = snapshots.find(s => s.metadata.ruleId === args.ruleId);
    if (!rule) fail("rule_not_in_scope", "Rule is not applicable to this task path; request a fresh index");
    if (args.version !== rule.metadata.version) fail("snapshot_changed", "Rule version changed; request a fresh index");
    let start = args.startLine || 1, end = args.endLine || rule.lines.length;
    if (args.section !== undefined) {
      const matches = rule.headings.filter(h => h.title === args.section);
      if (matches.length !== 1) fail("invalid_arguments", "Section must identify exactly one indexed heading; otherwise use lines");
      start = matches[0].startLine; end = matches[0].endLine;
    }
    if (end < start || start > Math.max(1, rule.lines.length)) fail("invalid_arguments", "Requested lines are outside the observed document");
    end = Math.min(end, rule.lines.length);
    let last = start - 1, content = "";
    const render = () => ({ ...base, action: "read", rule: rule.metadata, startLine: start, endLine: last, content,
      hasMore: last < end, nextStartLine: last < end ? last + 1 : null, requestedEndLine: end });
    for (let i = start; i <= end; i++) {
      const previous = content; content += `${i}: ${rule.lines[i - 1]}\n`; last = i;
      if (Buffer.byteLength(JSON.stringify(render())) > budget) { content = previous; last = i - 1; break; }
    }
    if (last < start && end >= start) fail("response_budget_exceeded", "One rule line does not fit; increase byteBudget");
    return bounded(render(), budget);
  }
  if (args.setHash !== undefined && args.setHash !== setHash) fail("snapshot_changed", "Rules changed between index pages");
  const offset = args.offset || 0;
  if (offset > snapshots.length) fail("invalid_arguments", "Index offset exceeds the rule count");
  const rules = [];
  const render = () => ({ ...base, action: "index", rules, hasMore: offset + rules.length < snapshots.length,
    nextOffset: offset + rules.length < snapshots.length ? offset + rules.length : null });
  for (const rule of snapshots.slice(offset, offset + (args.limit || 16))) {
    const row = { ...rule.metadata, headings: rule.headings.slice(0, 64), outlineTruncated: rule.headings.length > 64 };
    rules.push(row);
    while (Buffer.byteLength(JSON.stringify(render())) > budget && row.headings.length) { row.headings.pop(); row.outlineTruncated = true; }
    if (Buffer.byteLength(JSON.stringify(render())) > budget) { rules.pop(); break; }
  }
  if (!rules.length && offset < snapshots.length) fail("response_budget_exceeded", "Rule metadata does not fit; increase byteBudget");
  return bounded(render(), budget);
}
module.exports = { projectRules, MAX_BYTES };
