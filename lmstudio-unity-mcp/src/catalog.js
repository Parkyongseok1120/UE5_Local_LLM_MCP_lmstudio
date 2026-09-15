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
const csv = object({ header: { const: true }, delimiter: enumeration(",", ";", "\t", "|"), keyColumn: str, duplicateKey: { const: "error" }, missingKey: { const: "error" } }, ["header", "delimiter"]);
const selector = array({ anyOf: [str, integer(0, 1000000)] }, 32);
const tool = (name, description, properties, required = []) => ({ name, description, inputSchema: object(properties, required) });
const tools = [
  tool("unity_status", "Observe the bound project, Editor session, independent states and capabilities. Works offline.", {}),
  tool("search_files", "Bounded literal file/content search within the selected Unity project.", { path: str, query: str, content: { type: "boolean" }, ...paging }, ["query"]),
  tool("read_file", "Read a line range and receive a full-file receipt (UTF-8 only).", { path: str, startLine: integer(1, 10000000), limit: paging.limit, byteBudget: paging.byteBudget }, ["path"]),
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
  tool("unity_scene", "List opened scenes, create/rename/reparent/activate objects, add components, or save one scene explicitly.", { action: enumeration("list", "create", "rename", "reparent", "activate", "add_component", "save"), ...mutation,
    target: ref, parent: ref, name: str, type: str, active: { type: "boolean" }, receipt: str, scenePath: str,
    acknowledgeExistingDirty: { const: true }, ...paging }, ["action"]),
  tool("unity_asset", "Create a ScriptableObject by compiled type or save exactly one asset (including prior dirty changes).", { action: enumeration("create_so", "save"), ...mutation, type: str, path: str, mustNotExist: { const: true }, target: ref, receipt: str, acknowledgeExistingDirty: { const: true } }, ["action", "operationId"]),
  tool("unity_prefab", "Observe instance overrides. Prefab source editing/apply/revert are unavailable in this alpha.", { action: enumeration("overrides"), target: ref, ...paging }, ["action", "target"]),
  tool("unity_logs", "Read bounded Console/compiler events collected since Bridge subscription, not the historical Console.", { source: enumeration("console", "compiler"), afterSequence: integer(0, Number.MAX_SAFE_INTEGER), compilationId: str, ...paging }, []),
  tool("unity_editor", "Explicit Play/Stop/Pause/Step, path-scoped import, or script recompile. Never a player build.", { action: enumeration("play", "stop", "pause", "resume", "step", "import", "compile"), ...mutation, path: str }, ["action", "operationId"]),
  tool("unity_operation", "Retrieve a previously submitted operation. Unknown outcomes are never re-executed automatically.", { operationId: mutation.operationId, action: enumeration("get", "cancel") }, ["operationId", "action"]),
  tool("unity_tests", "Test adapter availability. Execution is unavailable until an optional adapter is installed.", { action: enumeration("status") }, ["action"]),
];
module.exports = { tools, ref };
