import {
  type Tool,
  type ToolCallRequest
} from "@lmstudio/sdk";

export function isUnregisteredGitReadIntent(name: string): boolean {
  const normalized = String(name || "").trim();
  if (!/^git_[a-z0-9_:-]+$/iu.test(normalized) || UNSAFE_UNKNOWN_NAME_PATTERN.test(normalized)) return false;
  return /(?:diff|show|status|log|changed|read|file|history|commit)/iu.test(normalized);
}

export const UNSAFE_UNKNOWN_NAME_PATTERN = /(?:write|edit|delete|remove|create|build|test|run|execute|apply|commit|push|merge|rebase|checkout|reset|clean|approve|mutation)/iu;

export type RemoteToolLike = Tool & {
  name: string;
  pluginIdentifier?: string;
  description: string;
  parametersJsonSchema?: unknown;
};

// Only locally constructed tool objects can receive local read authority.
export const localObservationTools = new WeakSet<object>();
const readOperations: Readonly<Record<string, readonly string[]>> = {
  unity_scene: ["list"], unity_prefab: ["read", "contents", "overrides"],
  unity_approval: ["status"], unity_operation: ["get"], unity_tests: ["status", "results"],
};
export const readOnlyOperationProfiles = new WeakSet<object>();

export function hasReadCapability(tool: RemoteToolLike): boolean {
  return isObservationOnlyToolCall(tool, { name: tool.name, arguments: {} })
    || (tool.pluginIdentifier === "mcp/unity-tools" && Boolean(readOperations[tool.name]));
}

export const UNITY_OBSERVATION_TOOLS = new Set([
  "workspace_status", "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file",
  "unity_references",
  "unity_snapshot",
  "unity_debug_query",
  "unity_symbols",
  "unity_status",
  "unity_git",
  "list_directory",
  "search_files",
  "read_file",
  "structured_data_read",
  "unity_find",
  "unity_object_read",
  "unity_logs",
]);

export const UNREAL_OBSERVATION_TOOLS = new Set([
  "workspace_status", "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file",
  "get_workspace_info",
  "list_unreal_projects",
  "get_active_project",
  "detect_unreal_project",
  "list_directory",
  "search_files",
  "read_file",
  "read_file_range",
  "read_symbol",
  "read_unreal_logs",
  "propose_file_deletions",
]);

/**
 * LM Studio 0.4.24 can leave requestConfirmToolCall pending without rendering
 * its approval controls. Observation-only calls must not block the prediction
 * loop on that host UI. Mutations and long-running work still use the host's
 * confirmation flow.
 */
function hasReadAuthority(
  tool: RemoteToolLike,
  request: Pick<ToolCallRequest, "name" | "arguments">,
): boolean {
  const plugin = String(tool.pluginIdentifier || "").trim().toLowerCase();
  const name = String(request.name || tool.name || "").trim();
  const args = request.arguments && typeof request.arguments === "object"
    ? request.arguments as Record<string, unknown> : {};

  if (name !== tool.name) return false;
  if (localObservationTools.has(tool)) return true;
  if (plugin === "mcp/evidence-first"
    && ["evidence_first_contract", "evidence_first_validate", "evidence_first_status"].includes(name)) return true;
  if (plugin === "mcp/unity-tools") {
    if (UNITY_OBSERVATION_TOOLS.has(name)) return true;
    if (readOperations[name]) return readOperations[name].includes(String(args.action || ""));
    return false;
  }
  if (plugin === "mcp/unreal-agent" || plugin === "mcp/unreal-rag") {
    return UNREAL_OBSERVATION_TOOLS.has(name);
  }
  return false;
}

export type ToolCapability = {
  provider: string; name: string; scope: "unity" | "unreal" | "common";
  effect: "read" | "approval_required"; approval: "none" | "host";
  recoveryEligible: boolean; resultClass: "observation" | "operation";
  continuation: "archive_offset" | "published_schema" | "none"; rehydratable: boolean;
};
export function capabilityScope(tool: { pluginIdentifier?: string }): "unity" | "unreal" | "common" {
  const provider = String(tool.pluginIdentifier || "").trim().toLowerCase();
  return provider === "mcp/unity-tools" ? "unity"
    : ["mcp/unreal-agent", "mcp/unreal-rag"].includes(provider) ? "unreal" : "common";
}
export function resolveCapability(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">): ToolCapability {
  const read = hasReadAuthority(tool, request);
  return {
provider: tool.pluginIdentifier || "local", name: tool.name, scope: capabilityScope(tool),
    effect: read ? "read" : "approval_required", approval: read ? "none" : "host", recoveryEligible: read,
    resultClass: read ? "observation" : "operation", rehydratable: read,
    continuation: read ? (tool.name === "evidence_first_read_context" ? "archive_offset" : "published_schema") : "none"
};
}
export function isObservationOnlyToolCall(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">) {
  return resolveCapability(tool, request).effect === "read";
}
export class ToolCapabilityRegistry {
  readonly tools: Array<RemoteToolLike>;
  readonly collisions: Array<string>;
  constructor(tools: Array<RemoteToolLike>) {
    this.collisions = [...new Set(tools.filter(t => tools.filter(other => other.name === t.name).length > 1).map(t => t.name))];
    this.tools = tools.filter(t => !this.collisions.includes(t.name));
  }
  resolve(name: string) { return this.tools.find(t => t.name === name); }
  readProfile() {
    return this.tools.filter(hasReadCapability).map(tool => {
      const actions = tool.pluginIdentifier === "mcp/unity-tools" ? readOperations[tool.name] : undefined;
      if (!actions) return tool;
      const schema = (tool.parametersJsonSchema || {}) as { properties?: Record<string, unknown>; required?: string[] };
      // Preserve the SDK tool implementation/remote session while narrowing the
      // model-facing operation schema. The guard independently enforces it.
      const narrowed = Object.create(tool) as RemoteToolLike;
      Object.defineProperty(narrowed, "parametersJsonSchema", { value: {
        ...schema, properties: { ...schema.properties, action: { type: "string", enum: [...actions] } },
        required: [...new Set([...(schema.required || []), "action"])],
      }, enumerable: true });
      readOnlyOperationProfiles.add(narrowed);
      return narrowed;
    });
  }
}
