"use strict";
const { createHash } = require("node:crypto");
function ordered(value) {
  if (Array.isArray(value)) return value.map(ordered);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, ordered(v)]));
}
const digest = value => createHash("sha256").update(JSON.stringify(ordered(value))).digest("hex");
const scopeFields = ["provider", "repositoryIdentity", "workspaceIdentity", "projectIdentity",
  "canonicalProjectRoot", "canonicalProject", "workspaceRoot"];
const rangeFields = new Set(["cursor", "nextCursor", "startLine", "endLine", "lineCount", "offsetBytes", "startOffset",
  "offset", "limit", "maxBytes", "maxChars", "byteBudget"]);
function sourceIdentity(value) {
  if (value.sourceIdentityDigest) return String(value.sourceIdentityDigest);
  if (value.sourceIdentity && typeof value.sourceIdentity === "object") return digest(value.sourceIdentity);
  const scope = Object.fromEntries(scopeFields.filter(k => value[k] !== undefined).map(k => [k, value[k]]));
  return Object.keys(scope).length ? digest(scope) : "unknown-source";
}
function sourceVersion(value) {
  const explicit = value.sourceVersion || value.sha256 || value.hash || value.blobOid || value.version;
  if (explicit) return String(explicit);
  if (value.kind === "git_observation" && ["worktree", "staged", "working_tree"].includes(String(value.comparison))) {
    return `observed:${digest({base:value.base,head:value.head,comparison:value.comparison,content:value.content ?? value.text ?? value.items})}`;
  }
  if (value.base || value.head || value.comparison || value.revision)
    return `observed:${digest({base:value.base,head:value.head,comparison:value.comparison,revision:value.revision})}`;
  return "unknown-version";
}
function queryKey(value) {
  if (value.sourceQueryKey) return String(value.sourceQueryKey);
  return digest({ sourceIdentity: sourceIdentity(value), sourceVersion: sourceVersion(value),
    action: value.sourceAction || value.action || value.toolName || value.kind || "observation",
    path: value.path || value.sourcePath || value.workspaceRelativePath || "",
    comparison: value.comparison, base:value.base, head:value.head, revision:value.revision,
    since:value.sourceSince ?? value.since, until:value.sourceUntil ?? value.until,
    authorQuery:value.sourceAuthorQuery ?? value.authorQuery, paths:value.paths || value.requestedPaths || value.queryPath,
    semanticQueryDigest:value.semanticQueryDigest, query:value.query,
    symbol:value.symbol || value.symbolName, object:value.object || value.objectId || value.objectPath });
}
function normalizeObservation(value, request, provider) {
  const normalized = { ...value, provider, toolName:request.name,
    semanticQueryDigest:digest(Object.fromEntries(Object.entries(request.arguments || {}).filter(([k]) => !rangeFields.has(k)))) };
  // Caller verifies the paired request/provider authority. Payload identity
  // remains evidence, never a capability or proof of mutation permission.
  delete normalized.sourceQueryKey;
  return { ...normalized, sourceIdentityDigest:sourceIdentity(normalized),
    sourceVersion:sourceVersion(normalized), sourceQueryKey:queryKey(normalized) };
}
module.exports = { sourceIdentity, sourceVersion, queryKey, normalizeObservation };
