import { UNITY_OBSERVATION_TOOL_NAMES, UNITY_READ_OPERATIONS } from "./generated-unity-tool-contract";
import { ENGINE_PROFILES, ENGINE_PROVIDERS } from "./engine-profiles.js";
import {
  type Tool,
  type ToolCallRequest
} from "@lmstudio/sdk";
import { UNREAL_AGENT_MUTATION_TOOL_NAMES, UNREAL_AGENT_OBSERVATION_TOOL_NAMES, UNREAL_RAG_MUTATION_TOOL_NAMES,
  UNREAL_RAG_OBSERVATION_TOOL_NAMES } from "./generated-tool-contract";

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
// Mixed tools: only the listed actions are observations. unity_references collect/release,
// unity_snapshot capture/record_start/record_stop/release and unity_symbols index start worker
// processes or change retained state, so they are not read operations.
const readOperations: Readonly<Record<string, readonly string[]>> = UNITY_READ_OPERATIONS;
export const readOnlyOperationProfiles = new WeakSet<object>();
// Generated from config/stable_tool_manifest.json (the servers' effect SSOT);
// never inferred from an arbitrary remote server's readOnlyHint.
export const UNREAL_RAG_OBSERVATION_TOOLS = new Set(UNREAL_RAG_OBSERVATION_TOOL_NAMES);

export function hasReadCapability(tool: RemoteToolLike): boolean {
  return isObservationOnlyToolCall(tool, { name: tool.name, arguments: {} })
    || (tool.pluginIdentifier === ENGINE_PROFILES.unity.fileProvider && Boolean(readOperations[tool.name]));
}

export const UNITY_OBSERVATION_TOOLS = new Set(UNITY_OBSERVATION_TOOL_NAMES);

export const UNREAL_OBSERVATION_TOOLS = new Set(UNREAL_AGENT_OBSERVATION_TOOL_NAMES);
const UNREAL_AGENT_MUTATIONS = new Set(UNREAL_AGENT_MUTATION_TOOL_NAMES);
const UNREAL_RAG_MUTATIONS = new Set(UNREAL_RAG_MUTATION_TOOL_NAMES);

/**
 * Known observations do not require confirmation. Mutations and long-running
 * work use the host's saved approval policy or its native confirmation card;
 * that decision must not change a tool's read/recovery classification.
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
  if (plugin === ENGINE_PROFILES.unity.fileProvider) {
    if (UNITY_OBSERVATION_TOOLS.has(name)) return true;
    if (readOperations[name]) return readOperations[name].includes(String(args.action || ""));
    return false;
  }
  if (plugin === "mcp/unreal-rag") {
    return UNREAL_RAG_OBSERVATION_TOOLS.has(name);
  }
  if (plugin === ENGINE_PROFILES.unreal.fileProvider) {
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
  return provider === ENGINE_PROFILES.unity.fileProvider ? "unity"
    : ENGINE_PROFILES.unreal.providers.includes(provider) ? "unreal" : "common";
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
const REVIEW_REQUEST = /검토|감사|리뷰|분석|점검|조사|찾아|확인해|설명|평가|진단|\b(?:review|audit|analy[sz]e|inspect|find|explain|assess|diagnose|investigate)\b/iu;
const CHANGE_REQUEST = /수정|고쳐|고치|구현|추가해|추가하|변경|바꿔|바꾸|적용|리팩|삭제|지워|만들어|작성해|생성|패치|개선해|빌드|컴파일|실행|돌려|커밋|푸시|\b(?:fix|implement|change|modify|edit|apply|refactor|delete|remove|create|write|add|patch|build|compile|run|execute|commit|push|rename)\b/iu;
const NO_CHANGE = /(?:코드\s*)?(?:수정|변경)하지\s*(?:말고|마|않고)|바꾸지\s*(?:말고|마|않고)|(?:do not|don't)\s+(?:edit|change|modify)\b[^.,]*|without\s+(?:changing|editing|modifying)\b[^.,]*|no\s+(?:code\s+)?changes/giu;

/** A request that asks only to review, find or explain, naming no change,
 * build or run: its rounds need no file/project mutation schemas.
 * Ambiguous or elliptical requests keep the full surface. */
export function readOnlyReviewRequest(text: string): boolean {
  const request = String(text || "").replace(NO_CHANGE, " ");
  return REVIEW_REQUEST.test(request) && !CHANGE_REQUEST.test(request);
}

export class ToolCapabilityRegistry {
  readonly tools: Array<RemoteToolLike>;
  readonly collisions: Array<string>;
  constructor(tools: Array<RemoteToolLike>) {
    this.collisions = [...new Set(tools.filter(t => tools.filter(other => other.name === t.name).length > 1).map(t => t.name))];
    this.tools = tools.filter(t => !this.collisions.includes(t.name));
  }
  resolve(name: string) { return this.tools.find(t => t.name === name); }
  /** Every tool except the manifest's known file/project mutations, for a
   * review-only request. Validators, long-running checks and tools of other
   * providers stay: only what the request ruled out is withheld. */
  reviewProfile() {
    return this.tools.filter(tool => {
      const plugin = String(tool.pluginIdentifier || "").trim().toLowerCase();
      return !(plugin === ENGINE_PROFILES.unreal.fileProvider && UNREAL_AGENT_MUTATIONS.has(tool.name))
        && !(plugin === "mcp/unreal-rag" && UNREAL_RAG_MUTATIONS.has(tool.name));
    });
  }
  readProfile() {
    return this.tools.filter(hasReadCapability).map(tool => {
      const actions = tool.pluginIdentifier === ENGINE_PROFILES.unity.fileProvider ? readOperations[tool.name] : undefined;
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
