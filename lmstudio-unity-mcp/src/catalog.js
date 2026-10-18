"use strict";
const str = { type: "string", minLength: 1, maxLength: 4096 };
const text = { type: "string", maxLength: 65536 };
const integer = (min, max) => ({ type: "integer", minimum: min, maximum: max });
const enumeration = (...values) => ({ type: "string", enum: values });
const object = (properties, required = []) => ({ type: "object", properties, required, additionalProperties: false });
const array = (items, maxItems = 100) => ({ type: "array", items, maxItems });
const paging = { limit: integer(1, 200), cursor: str, byteBudget: integer(1024, 65536) };
const ref = object({ kind: enumeration("asset", "scene", "temporary", "runtime"), projectIdentity: str,
  guid: str, localFileId: str, globalObjectId: str, scenePath: str, handle: str, editorSessionId: str, domainGeneration: integer(1, 2147483647), playSessionId: str }, ["kind", "projectIdentity"]);
const mutation = { operationId: { type: "string", pattern: "^[A-Za-z0-9_-]{8,100}$" } };
const patches = { ...array(object({ op: enumeration("set", "array_insert", "array_remove", "array_move"), propertyPath: str, value: {}, index: integer(0, 100000), toIndex: integer(0, 100000) }, ["op", "propertyPath"]), 32), minItems: 1 };
const csv = object({ header: { const: true }, delimiter: enumeration(",", ";", "\t", "|"), keyColumn: str, duplicateKey: { const: "error" }, missingKey: { const: "error" } }, ["header", "delimiter"]);
const selector = array({ anyOf: [str, integer(0, 1000000)] }, 32);
const tool = (name, description, properties, required = []) => ({ name, description, inputSchema: object(properties, required) });
// Per-action requirements live in the validated inputSchema, not beside it.
const withConditions = (definition, conditions) => ({ ...definition, inputSchema: { ...definition.inputSchema, allOf: conditions } });
const tools = [
  tool("unity_references", "Collect/read an immutable scoped reference index. Asset dependencies are not property references. Runtime edges require actual instrumentation. No unused conclusion or closed-scene opening.", { action: enumeration("collect", "query", "release"), kind: enumeration("asset_dependency", "serialized_reference", "observed_runtime_reference"), paths: array(str, 100), targets: array(ref, 32), recursive: { type: "boolean" }, indexId: str, endpoint: { anyOf: [str, ref] }, direction: enumeration("forward", "reverse"), ...paging }, ["action"]),
  tool("unity_snapshot", "Capture selected values, read retained fields/metadata pages, diff, release, or bounded record. No automatic pause, rollback, name matching or causal inference.", { action: enumeration("capture", "read", "diff", "release", "record_start", "record_status", "record_stop"), section: enumeration("metadata", "scope", "failures"), targets: array(object({ target: ref, propertyPaths: { ...array(str, 32), minItems: 1 }, descendantDepth: integer(1, 6) }, ["target", "propertyPaths"]), 32), snapshotId: str, targetIndex: integer(0, 31), propertyPaths: array(str, 32), a: str, b: str, recordingId: str, maxDurationMs: integer(1, 60000), maxFrames: integer(1, 3600), maxSamples: integer(1, 32), maxBytes: integer(1024, 4194304), intervalMs: integer(50, 60000), ...paging }, ["action"]),
  tool("unity_debug_query", "Query an explicitly registered adapter, catalog, object.state or events. Never invokes an arbitrary getter/method.", { adapter: str, input: { type: "object" }, ...paging }, ["adapter", "input"]),
  tool("unity_debug_action", "Execute one named registered Play-mode action, or explicitly start/stop bounded event collection. Execute permission and operationId required.", { adapter: str, input: { type: "object" }, ...mutation }, ["adapter", "input", "operationId"]),
  tool("unity_symbols", "Scoped Roslyn index; no generators/analyzers. index needs Editor; retained index works offline. find supports simple/qualified exact|prefix|contains; source reads declaration. Check uri and stale.", { action: enumeration("assemblies", "index", "status", "find", "source", "usages", "relations", "diagnostics", "omissions", "versions"), assemblies: array(str, 32), indexVersion: str, name: str, match: enumeration("exact", "prefix", "contains"), symbolId: str, direction: enumeration("forward", "reverse"), offset: integer(0, 1000000), limit: paging.limit, byteBudget: paging.byteBudget }, ["action"]),
  tool("unity_static_validate", "Warning-only lexical checks of Assets C# files: event cleanup, async lifetime, hot-loop lookups and coroutine activation. No semantic proof. Follow nextCursor; zero findings is not proof of correctness.", { path: str, ...paging }),
  tool("unity_status", "Observe the bound project, Editor session, independent states and capabilities. Works offline.", {}),
  tool("unity_git", "Read-only Git status/log/changed_files/diff_file/read_file/diff. Explicit comparison required; range needs base/head, other comparisons forbid them. Continue the snapshot with nextCursor.",
    { action: enumeration("status", "log", "diff", "changed_files", "diff_file", "read_file"), comparison: enumeration("worktree", "staged", "last_commit", "range"), base: str, head: str, revision: str, path: str,
      since: { type: "string", minLength: 10, maxLength: 64 }, until: { type: "string", minLength: 10, maxLength: 64 },
      authorQuery: { type: "string", minLength: 1, maxLength: 320 },
      paths: array(str, 16), startLine: integer(1, 10000000), ...paging }, ["action"]),
  tool("list_directory", "Direct children only under Assets/Packages/ProjectSettings or package://<name>. Dot aliases Assets. Filter kind; pass nextCursor as cursor when partial.",
    { path: str, kind: enumeration("all", "files", "directories"), ...paging }, ["path"]),
  { ...tool("search_files", "Bounded path/content search under Assets/Packages/ProjectSettings or package://<name>. Dot/default means Assets. Extensions are OR, combined with query by AND. total counts collected rows; incomplete means scan stopped. Follow nextCursor for collected pages. No match is not absence.",
    { path: str, query: str, extensions: { type: "array", items: { type: "string", pattern: "^\\.[A-Za-z0-9_-]+$" }, minItems: 1, maxItems: 16 }, content: { type: "boolean" }, matchFileNames: { type: "boolean", description: "Match basename only; ignore content." }, ...paging }),
    anyOf: [{ required: ["query"] }, { required: ["extensions"] }],
    not: { required: ["content", "extensions"], properties: { content: { const: true } }, not: { required: ["query"] } } },
  tool("read_file", "Read UTF-8 lines under Assets, Packages or ProjectSettings or package://<name>/path. Whole-line pages fit byteBudget including metadata; follow nextStartLine while hasMore. truncated cuts requested lines; moreLinesInFile only means file continues. Oversized lines fail. Full-file hash/receipt; numbered display.", { path: str, startLine: integer(1, 10000000), limit: paging.limit, lineNumbers: { type: "boolean" }, byteBudget: paging.byteBudget }, ["path"]),
  tool("patch_file", "Explicit exact-once substring replacements, guarded by a file receipt. No import/compile initiated.", { path: str, receipt: str, edits: { ...array(object({ oldText: str, newText: text }, ["oldText", "newText"]), 50), minItems: 1 } }, ["path", "receipt", "edits"]),
  tool("create_file", "Create an allowed text file under an existing Assets directory; target must be absent.", { path: str, content: text, mustNotExist: { const: true } }, ["path", "content", "mustNotExist"]),
  tool("structured_data_read", "Read bounded JSON paths or CSV string rows. CSV format is explicit.", { path: str, format: enumeration("json", "csv"), selector, depth: integer(0, 4), schemaPath: str, csv, filter: object({ column: str, equals: text }, ["column", "equals"]), ...paging }, ["path", "format"]),
  tool("structured_data_patch", "Receipt-guarded JSON replace/remove or lossless CSV cell updates. No schema/type inference.", { path: str, format: enumeration("json", "csv"), receipt: str, schemaPath: str, csv,
    changes: array(object({ op: enumeration("replace", "remove"), path: selector, value: {} }, ["op", "path"]), 50),
    cells: array(object({ key: text, column: str, value: text }, ["key", "column", "value"]), 100) }, ["path", "format", "receipt"]),
  tool("unity_find", "Find loaded objects/components, assets or compiled types. Names only filter; edits require ObjectRef.", { kind: enumeration("objects", "assets", "types"), query: text, type: str, ...paging }, ["kind"]),
  tool("unity_object_read", "Read serialized properties and a receipt; no arbitrary getters. Runtime is read-only.", { target: ref, propertyPaths: array(str, 32), depth: integer(0, 6), ...paging }, ["target"]),
  tool("unity_object_patch", "Patch exact serialized fields. Scope must be explicit. Assets/SO remain dirty until saved.", { target: ref, scope: enumeration("sceneInstance", "asset"), receipt: str, ...mutation,
    patches: { ...array(object({ op: enumeration("set", "array_insert", "array_remove", "array_move"), propertyPath: str, value: {}, index: integer(0, 100000), toIndex: integer(0, 100000) }, ["op", "propertyPath"]), 32), minItems: 1 } }, ["target", "scope", "receipt", "operationId", "patches"]),
  tool("unity_scene", "Explicit authoring scene edits. Delete/remove_component require one-use user approval in the Editor; no automatic scene saving.", { action: enumeration("list", "create", "rename", "reparent", "activate", "add_component", "save", "delete", "remove_component"), ...mutation, approvalId: str,
    target: ref, parent: ref, name: str, type: str, active: { type: "boolean" }, receipt: str, scenePath: str,
    acknowledgeExistingDirty: { const: true }, ...paging }, ["action"]),
  tool("unity_asset", "Create/save one asset; delete moves one main .asset/.prefab/.mat to OS Trash with exact Editor approval.", { action: enumeration("create_so", "save", "delete"), ...mutation, approvalId: str, type: str, path: str, mustNotExist: { const: true }, target: ref, receipt: str, acknowledgeExistingDirty: { const: true } }, ["action", "operationId"]),
  tool("unity_prefab", "Read/patch isolated prefab source with file receipt and explicit save; create/instantiate; apply exact scalar overrides to an explicit source or revert with user approval. No inferred nested/variant destination.", { action: enumeration("read", "contents", "overrides", "patch", "create", "instantiate", "apply", "revert"), target: ref, ...mutation, receipt: str, sourceReceipt: str, sourceTarget: ref, approvalId: str, assetPath: str, scenePath: str, save: { const: true }, mustNotExist: { const: true }, propertyPaths: array(str, 32), patches, ...paging }, ["action", "target"]),
  tool("unity_approval", "Request/status only: user reviews exact destructive operation in Unity Editor. No MCP action can approve. Expires in 2 minutes, one use, target-state and session bound.", { action: enumeration("request", "status"), method: enumeration("unity_scene", "unity_asset", "unity_prefab"), arguments: { type: "object" }, approvalId: str }, ["action"]),
  tool("unity_logs", "Read bounded Console/compiler events collected since Bridge subscription, not the historical Console.", { source: enumeration("console", "compiler"), afterSequence: integer(0, Number.MAX_SAFE_INTEGER), compilationId: str, ...paging }, []),
  tool("unity_editor", "Explicit Play/Stop/Pause/Step, path-scoped import, or script recompile. Never a player build.", { action: enumeration("play", "stop", "pause", "resume", "step", "import", "compile"), ...mutation, path: str }, ["action", "operationId"]),
  tool("unity_operation", "Retrieve a previously submitted operation. Unknown outcomes are never re-executed automatically. A compile request resolves to completed, failed or no_compilation_observed once the Bridge observes the next compilation; accepted alone is not success.", { operationId: mutation.operationId, action: enumeration("get", "cancel") }, ["operationId", "action"]),
  withConditions(tool("unity_tests", "List runnable test names (read-only; status pending means retry), run exact named/category EditMode or PlayMode tests using installed Test Framework, poll status/results or request cooperative cancellation. run requires operationId, mode, maxDurationMs, maxTests and acknowledgeSceneChanges. completed is not passed: read testStatus and failed. Never auto-installs, retries or selects follow-up tests. Dirty scenes must be resolved explicitly.", { action: enumeration("list", "run", "status", "results", "cancel", "release"), ...mutation, mode: enumeration("EditMode", "PlayMode"), testNames: array(str, 32), categories: array(str, 32), acknowledgeSceneChanges: { const: true }, maxDurationMs: integer(100, 600000), maxTests: integer(1, 256), ...paging }, ["action"]), [
    { if: { properties: { action: { const: "run" } }, required: ["action"] },
      then: { required: ["operationId", "mode", "maxDurationMs", "maxTests", "acknowledgeSceneChanges"] } },
    { if: { properties: { action: { const: "list" } }, required: ["action"] }, then: { required: ["mode"] } }]),
];
// Effect data is the authority for the compactor's generated observation profile.
const toolEffects = {
  unity_references: { collect: "mutation", query: "read", release: "mutation" },
  unity_snapshot: { capture: "mutation", read: "read", diff: "read", release: "mutation", record_start: "long_running", record_status: "read", record_stop: "mutation" },
  unity_debug_query: "read", unity_debug_action: "mutation",
  unity_symbols: { assemblies: "read", index: "long_running", status: "read", find: "read", source: "read", usages: "read", relations: "read", diagnostics: "read", omissions: "read", versions: "read" },
  unity_static_validate: "read", unity_status: "read", unity_git: "read",
  list_directory: "read", search_files: "read", read_file: "read", patch_file: "mutation", create_file: "mutation",
  structured_data_read: "read", structured_data_patch: "mutation", unity_find: "read", unity_object_read: "read", unity_object_patch: "mutation",
  unity_scene: { list: "read", create: "mutation", rename: "mutation", reparent: "mutation", activate: "mutation", add_component: "mutation", save: "mutation", delete: "mutation", remove_component: "mutation" },
  unity_asset: "mutation", unity_prefab: { read: "read", contents: "read", overrides: "read", patch: "mutation", create: "mutation", instantiate: "mutation", apply: "mutation", revert: "mutation" },
  unity_approval: { request: "mutation", status: "read" }, unity_logs: "read", unity_editor: "long_running",
  unity_operation: { get: "read", cancel: "mutation" }, unity_tests: { list: "read", run: "long_running", status: "read", results: "read", cancel: "mutation", release: "mutation" },
};
module.exports = { tools, ref, toolEffects };
