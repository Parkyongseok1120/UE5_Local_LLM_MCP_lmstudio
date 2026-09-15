"use strict";

// Engine independent. The caller supplies a path policy, never a project type.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { readStableTextFile } = require("../lmstudio-unreal-agent-mcp/src/direct-file-snapshot");
const { withPathLock } = require("../lmstudio-unreal-agent-mcp/src/write-locks");
const { atomicWriteText, uniqueTempPath } = require("../lmstudio-unreal-agent-mcp/src/atomic-io");

function fail(code, message) { throw Object.assign(new Error(message), { code, status: "not_applied" }); }
function hash(value) { return crypto.createHash("sha256").update(value).digest("hex"); }
function bounded(payload, budget = 32768) {
  if (Buffer.byteLength(JSON.stringify(payload)) > budget) fail("response_budget_exceeded", "Narrow the query or request a smaller page; values were not silently clipped.");
  return payload;
}
function page(items, args, revision) {
  let offset = 0;
  if (args.cursor) {
    let cursor;
    try { cursor = JSON.parse(Buffer.from(args.cursor, "base64url").toString()); } catch { fail("invalid_cursor", "Invalid page cursor"); }
    if (cursor.revision !== revision) fail("snapshot_changed", "State or query changed between pages");
    offset = cursor.offset;
    if (!Number.isSafeInteger(offset) || offset < 0 || offset > items.length) fail("invalid_cursor", "Invalid offset");
  }
  const limit = args.limit ?? 50;
  const result = items.slice(offset, offset + limit);
  return { items: result, total: items.length, revision, truncated: offset + result.length < items.length,
    nextCursor: offset + result.length < items.length ? Buffer.from(JSON.stringify({ revision, offset: offset + result.length })).toString("base64url") : null };
}

class Files {
  constructor(policy, { allowWrite = false, maxBytes = 2 * 1024 * 1024 } = {}) {
    this.policy = policy;
    this.allowWrite = allowWrite;
    this.maxBytes = maxBytes;
    this.secret = crypto.randomBytes(32);
    this.session = crypto.randomUUID();
  }
  receipt(relative, digest) {
    const body = Buffer.from(JSON.stringify({ project: this.policy.root, path: relative, hash: digest, session: this.session, expires: Date.now() + 900000 })).toString("base64url");
    return `${body}.${crypto.createHmac("sha256", this.secret).update(body).digest("hex")}`;
  }
  verify(token, relative, digest) {
    if (typeof token !== "string") fail("receipt_required", "Read this file and supply its receipt");
    const [body, signature] = token.split(".");
    const expected = crypto.createHmac("sha256", this.secret).update(body || "").digest("hex");
    if (signature?.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) fail("invalid_receipt", "Receipt signature mismatch");
    let observed;
    try { observed = JSON.parse(Buffer.from(body, "base64url").toString()); } catch { fail("invalid_receipt", "Invalid receipt"); }
    if (observed.project !== this.policy.root || observed.path !== relative || observed.session !== this.session || observed.expires < Date.now()) fail("expired_receipt", "Receipt is expired or belongs to another target/session");
    if (observed.hash !== digest) fail("receipt_conflict", "File changed; no edit was applied");
  }
  async snapshot(relative) {
    const target = this.policy.resolve(relative, false);
    const read = await readStableTextFile(target, this.maxBytes);
    if (!read.ok) fail(read.errorCode, read.message);
    // Explicitly UTF-8 only. Reject lossy decode instead of changing the encoding.
    try { new TextDecoder("utf-8", { fatal: true }).decode(read.buffer); } catch { fail("unsupported_encoding", "Only UTF-8 (with optional BOM) is supported"); }
    return { ...read, target, receipt: this.receipt(relative, read.hash) };
  }
  async read(args) {
    const s = await this.snapshot(args.path);
    const lines = s.content.split(/\r\n|\n|\r/);
    const start = args.startLine ?? 1;
    return bounded({ status: "observed", path: args.path, receipt: s.receipt, receiptScope: "entire_file_bytes", hash: s.hash,
      observedAt: new Date().toISOString(), startLine: start, totalLines: lines.length,
      text: lines.slice(start - 1, start - 1 + (args.limit ?? 100)).join("\n"),
      truncated: start - 1 + (args.limit ?? 100) < lines.length }, args.byteBudget);
  }
  async mutate(args, transform, create = false) {
    if (!this.allowWrite) fail("edit_disabled", "Edit requires ALLOW_WRITE=1 in the host configuration");
    const target = this.policy.resolve(args.path, true);
    const result = await withPathLock(target, "unity_file_edit", async () => {
      this.policy.resolve(args.path, true);
      let before;
      if (create) {
        if (args.mustNotExist !== true) fail("precondition_required", "Creation requires mustNotExist=true");
        if (fs.existsSync(target)) fail("already_exists", "Target already exists");
      } else {
        before = await this.snapshot(args.path);
        this.verify(args.receipt, args.path, before.hash);
      }
      const content = await transform(before?.content ?? "");
      if (typeof content !== "string" || Buffer.byteLength(content) > this.maxBytes) fail("file_budget_exceeded", "Output exceeds the file limit");
      this.policy.resolve(args.path, true);
      if (before) {
        const current = await this.snapshot(args.path);
        this.verify(args.receipt, args.path, current.hash);
      }
      let applied = false;
      try {
        if (create) {
          // Publish a complete inode exclusively; never truncate a competing create.
          const temporary = uniqueTempPath(target);
          const fd = fs.openSync(temporary, "wx", 0o600);
          try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
          try { this.policy.resolve(args.path, true); fs.linkSync(temporary, target); applied = true; }
          finally { fs.unlinkSync(temporary); }
        } else {
          atomicWriteText(target, content);
          applied = true;
        }
        const after = await this.snapshot(args.path);
        return { status: "applied", changed: before?.hash !== after.hash, saved: true, receipt: after.receipt,
          path: args.path, hash: after.hash, import: "not_requested", compilation: "unknown",
          verification: after.hash === hash(Buffer.from(content)) ? "bytes_match" : "changed_after_write",
          observedAt: new Date().toISOString() };
      } catch (error) { error.status = applied ? "partially_applied" : "not_applied"; throw error; }
    }, { stateRoot: this.policy.stateRoot });
    if (result.locked) fail("write_locked", "Another cooperating process is editing this file");
    return result.result;
  }
  patch(args) {
    return this.mutate(args, text => {
      let result = text;
      for (const edit of args.edits) {
        const index = result.indexOf(edit.oldText);
        if (index < 0 || result.indexOf(edit.oldText, index + 1) >= 0) fail("ambiguous_edit", "oldText must match exactly once");
        result = result.slice(0, index) + edit.newText + result.slice(index + edit.oldText.length);
      }
      return result;
    });
  }
  async search(args) {
    const matches = [];
    let scanned = 0;
    let incomplete = false;
    let nodes = 0, matchBytes = 0;
    const append = row => {
      matchBytes += Buffer.byteLength(JSON.stringify(row));
      if (matchBytes > 2 * 1024 * 1024 || matches.length >= 5000) fail("query_budget_exceeded", "Narrow the search; accumulated matches exceed the scan budget");
      matches.push(row);
    };
    const walk = async relative => {
      if (scanned >= 5000 || ++nodes > 10000) { incomplete = true; return; }
      let target;
      try { target = this.policy.resolve(relative, false); } catch { return; }
      const stat = fs.lstatSync(target);
      if (stat.isDirectory()) {
        for (const name of fs.readdirSync(target).sort()) await walk(`${relative}/${name}`);
      } else if (stat.isFile()) {
        scanned++;
        if (relative.includes(args.query)) append({ path: relative, kind: "path" });
        if (args.content === true && stat.size <= this.maxBytes) {
          try {
            const s = await this.snapshot(relative);
            const lines = s.content.split(/\r\n|\n|\r/);
            for (let i = 0; i < lines.length; i++) if (lines[i].includes(args.query)) append({ path: relative, line: i + 1, text: lines[i], hash: s.hash });
          } catch (error) { if (error.code === "query_budget_exceeded") throw error; /* Non-text/unstable files are skipped. */ }
        }
      }
    };
    await walk(args.path ?? "Assets");
    return bounded({ status: "observed", ...page(matches, args, hash(JSON.stringify(matches))), scanned, incomplete, consistency: "per_file_observations" }, args.byteBudget);
  }
}
module.exports = { Files, fail, hash, page, bounded };
