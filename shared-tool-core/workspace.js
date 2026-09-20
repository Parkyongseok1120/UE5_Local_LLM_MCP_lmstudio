"use strict";
// Workspace capabilities can share an engine transport without a second writer.
const path = require("node:path");
const { VersionControl } = require("./git");
const { hash, fail } = require("./files");
const { canonicalAbsolutePathIdentity } = require("../lmstudio-unreal-agent-mcp/src/filesystem-path-identity");
const string = { type: "string", minLength: 1, maxLength: 4096 };
const paging = { limit: { type: "integer", minimum: 1, maximum: 200 }, cursor: string,
  byteBudget: { type: "integer", minimum: 1024, maximum: 65536 } };
const paths = { type: "array", items: string, maxItems: 16 };
const comparison = { comparison: { type: "string", enum: ["range", "worktree", "staged"] }, base: string, head: string };
const spec = (name, description, properties, required = []) => ({ name, description,
  inputSchema: { type: "object", properties: { ...properties, project: string }, required, additionalProperties: false } });
const GIT_ACTIONS = Object.freeze({ git_status: "status", git_log: "log", git_changed_files: "changed_files", git_diff_file: "diff_file", git_read_file: "read_file" });
function workspaceToolDefinitions() {
  return [
    spec("workspace_status", "Observe the actual bound project root, engine and generic capability ownership. Does not change projects or plan work.", {}),
    spec("git_status", "Read scoped index/worktree status. Does not modify the index. Continue with nextCursor from the same snapshot.", { paths, ...paging }),
    spec("git_log", "Read commit metadata at a revision (default HEAD). Returns full commit IDs; bounded immutable pages.", { revision: string, paths, ...paging }),
    spec("git_changed_files", "List changed paths without a full diff. Explicit comparison required: range requires base/head; worktree/staged forbid both. Paths are workspace-relative literals. Continue with nextCursor.", { ...comparison, paths, ...paging }, ["comparison"]),
    spec("git_diff_file", "Read the diff of one exact regular file. range requires base/head; worktree/staged forbid both. Continue with cursor OR startLine. Never accepts directories.", { ...comparison, path: string, startLine: { type: "integer", minimum: 1 }, ...paging }, ["comparison", "path"]),
    spec("git_read_file", "Read source from a pinned commit rather than the current worktree. Raw blob hash is evidence, not a mutation receipt. Only regular UTF-8 blobs. Continue with cursor OR startLine.", { revision: string, path: string, startLine: { type: "integer", minimum: 1 }, ...paging }, ["revision", "path"]),
  ];
}
function createWorkspaceCapabilities({ resolveBinding }) {
  const repositories = new Map();
  const schemas = new Map(workspaceToolDefinitions().map(tool => [tool.name, tool.inputSchema]));
  return async (name, args) => {
    // The two transports must enforce the same declared arguments before root
    // resolution. This small validator covers only this closed schema family.
    const schema = schemas.get(name);
    const valid = (value, rule) => {
      if (rule.type === "string") return typeof value === "string" && value.length >= (rule.minLength || 0)
        && value.length <= (rule.maxLength || Infinity) && (!rule.enum || rule.enum.includes(value));
      if (rule.type === "integer") return Number.isInteger(value) && value >= (rule.minimum ?? -Infinity) && value <= (rule.maximum ?? Infinity);
      if (rule.type === "array") return Array.isArray(value) && value.length <= rule.maxItems && value.every(item => valid(item, rule.items));
      return false;
    };
    if (!schema || !args || typeof args !== "object" || Array.isArray(args)
      || schema.required.some(key => args[key] === undefined)
      || Object.entries(args).some(([key, value]) => !schema.properties[key] || !valid(value, schema.properties[key])))
      fail("invalid_arguments", `${name} arguments must match its declared schema`);
    const binding = await resolveBinding(args.project);
    const root = binding.root;
    const workspaceIdentity = hash(canonicalAbsolutePathIdentity(root));
    if (name === "workspace_status") return { status: "observed", schemaVersion: 1, kind: "workspace_status",
      workspaceRoot: root, workspaceIdentity, canonicalProjectRoot: root, ...binding,
      ...(binding.engine === "unreal" && binding.projectIdentity ? { canonicalProject: binding.projectIdentity } : {}),
      mutationOwner: "existing_engine_runtime", independentWriter: false };
    if (!GIT_ACTIONS[name]) throw new Error("Unknown Workspace capability");
    const key = workspaceIdentity;
    let git = repositories.get(key);
    if (!git) {
      if (repositories.size >= 8) repositories.delete(repositories.keys().next().value);
      git = new VersionControl({ root }); repositories.set(key, git);
    }
    const { project, ...query } = args;
    return { ...git.call({ action: GIT_ACTIONS[name], ...query }), projectIdentity: binding.projectIdentity,
      ...(binding.engine === "unity" ? { canonicalProjectRoot: root } : { canonicalProject: binding.projectIdentity }) };
  };
}
function fileObservation(payload) {
  if (!payload || payload.ok === false || payload.errorCode || payload.kind === "git_observation") return payload;
  const digest = payload.sha256 || payload.hash;
  if (payload.resolvedRootType === "workspace" || /^workspace:\/\//i.test(payload.path || "")) return payload;
  const project = payload.canonicalProject || (payload.resolvedRootType === "active_project" ? payload.activeProject : null);
  const root = payload.canonicalProjectRoot || (project?.toLowerCase().endsWith(".uproject") ? path.dirname(project) : null);
  if (!digest || !payload.path || !root) return payload;
  const deleted = ["deleted", "moved_to_trash"].includes(payload.operation);
  const mutation = deleted || ["modified", "created", "replaced"].includes(payload.operation);
  const lineRange = Number.isInteger(payload.startLine) && Number.isInteger(payload.totalLines);
  const byteRange = Number.isInteger(payload.offsetBytes) && Number.isInteger(payload.nextOffsetBytes) && Number.isInteger(payload.size);
  return { ...payload, schemaVersion: 1, kind: mutation ? "workspace_mutation_observation" : "workspace_file_observation",
    workspaceIdentity: hash(canonicalAbsolutePathIdentity(root)), sha256: digest,
    ...(project ? { canonicalProject: project } : {}),
    ...(deleted ? { observationState: "deleted" } : {}),
    ...(byteRange ? { range: { unit: "byte", start: payload.offsetBytes, endExclusive: payload.nextOffsetBytes, total: payload.size },
      coverage: payload.offsetBytes === 0 && payload.nextOffsetBytes === payload.size ? "complete" : "partial" } : {}),
    ...(lineRange ? { range: { unit: "line", start: payload.startLine, end: payload.endLine, total: payload.totalLines },
      coverage: payload.startLine === 1 && payload.endLine === payload.totalLines ? "complete" : "partial" } : {}) };
}
module.exports = { workspaceToolDefinitions, createWorkspaceCapabilities, fileObservation, GIT_ACTIONS };
