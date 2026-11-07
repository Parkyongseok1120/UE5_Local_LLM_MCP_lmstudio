"use strict";

// One reading of a directory listing result (revised plan 2026-11-07 §B).
// The archive body view, the read ledger inventory and size hints all take
// their structure from here. This is structure only: whether the result is a
// trusted, uniquely paired observation is the caller's check.

/** A single entry name that cannot be a path or carry a line break. */
function validName(name) {
  return typeof name === "string" && name.length > 0 && name !== "." && name !== ".."
    && !/[\\/\r\n\u0000]/u.test(name);
}

/** The listing's names and paging, or null when the value is not a listing.
 * `complete` means this response is the whole directory: the first page, no
 * next page, every entry named, and any reported total matched. A last page
 * (`offset > 0`) is never the whole directory on its own. */
function listingFacts(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.ok === false
    || typeof value.path !== "string" || !value.path || !Array.isArray(value.entries)) return null;
  const files = [], directories = [], others = [];
  let rejected = 0;
  for (const entry of value.entries) {
    if (!entry || typeof entry !== "object" || !validName(entry.name)) { rejected++; continue; }
    if (entry.type === "file") files.push({ name: entry.name,
      size: Number.isSafeInteger(entry.size) && entry.size >= 0 ? entry.size : null,
      modifiedAt: typeof entry.modifiedAt === "string" ? entry.modifiedAt : null });
    else if (entry.type === "directory") directories.push(entry.name);
    else others.push(entry.name);
  }
  const offset = Number.isSafeInteger(value.offset) && value.offset > 0 ? value.offset : 0;
  const nextOffset = Number.isSafeInteger(value.nextOffset) ? value.nextOffset : null;
  const totalEntries = Number.isSafeInteger(value.totalEntries) ? value.totalEntries : null;
  const complete = value.truncated === false && offset === 0 && nextOffset === null && rejected === 0
    && !(Number(value.omittedByPathPolicy) > 0)
    && (totalEntries === null || totalEntries === value.entries.length);
  return { path: String(value.path).replace(/[\\/]+$/u, ""), files, directories, others,
    offset, nextOffset, totalEntries, complete, rejected };
}

/** The names of a listing as an archive body: the names a model needs to
 * plan reads, without per-entry size and time. JSON keeps any name literal. */
function listingBody(facts) {
  return JSON.stringify({ path: facts.path, directories: facts.directories, files: facts.files.map(file => file.name),
    ...(facts.others.length ? { others: facts.others } : {}),
    offset: facts.offset, nextOffset: facts.nextOffset, totalEntries: facts.totalEntries, complete: facts.complete });
}

/** Source files an audit can be asked to cover. This scope filter is not
 * applied to the body: every returned name stays visible there. */
const AUDIT_SOURCE_EXTENSIONS = new Set([".h", ".hpp", ".inl", ".cpp", ".cc", ".cs", ".py", ".ts", ".js"]);
const NON_SOURCE_DIRECTORIES = new Set(["intermediate", "binaries", "saved", "deriveddatacache", "thirdparty", ".git", "node_modules"]);
function isAuditSource(path) {
  const parts = String(path).replace(/\\/gu, "/").toLowerCase().split("/");
  const name = parts.at(-1) || "";
  const dot = name.lastIndexOf(".");
  return dot > 0 && AUDIT_SOURCE_EXTENSIONS.has(name.slice(dot)) && !parts.some(part => NON_SOURCE_DIRECTORIES.has(part));
}

module.exports = { listingFacts, listingBody, isAuditSource };
