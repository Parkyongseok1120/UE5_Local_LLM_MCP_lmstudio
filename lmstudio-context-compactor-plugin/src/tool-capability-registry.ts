import { UNITY_OBSERVATION_TOOL_NAMES } from "./generated-unity-tool-contract";
import { capabilityFor, operationCapabilities, hasRole, needsReadReservation, localObservationTools } from "./tool-capability-core.js";
export { capabilityFor, hasRole, needsReadReservation, localObservationTools };
import type { TaskObjective } from "./execution-contracts";
import { resultSemantics } from "./tool-result-semantics.js";
import type { ChatMessage } from "@lmstudio/sdk";
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
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
// Mixed tools: only the listed actions are observations. unity_references collect/release,
// unity_snapshot capture/record_start/record_stop/release and unity_symbols index start worker
// processes or change retained state, so they are not read operations.
function readActions(tool: RemoteToolLike): string[] { return operationCapabilities(tool).filter(op => op.action && op.effect === "read").map(op => op.action); }
export const readOnlyOperationProfiles = new WeakSet<object>();
export const restrictedOperationProfiles = new WeakMap<object, readonly string[]>();
// Generated from config/stable_tool_manifest.json (the servers' effect SSOT);
// never inferred from an arbitrary remote server's readOnlyHint.
export const UNREAL_RAG_OBSERVATION_TOOLS = new Set(UNREAL_RAG_OBSERVATION_TOOL_NAMES);

export function hasReadCapability(tool: RemoteToolLike): boolean {
  return isObservationOnlyToolCall(tool, { name: tool.name, arguments: {} })
    || readActions(tool).length > 0;
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
  return capabilityFor(tool, request)?.effect === "read";
}

export type ToolCapability = {
  provider: string; name: string; scope: "unity" | "unreal" | "common";
  effect: "read" | "ephemeral" | "approval_required"; approval: "none" | "host";
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
  const capability = capabilityFor(tool, request);
  const ephemeral = capability?.effect === "ephemeral";
  return {
provider: tool.pluginIdentifier || "local", name: tool.name, scope: capabilityScope(tool),
    effect: read ? "read" : ephemeral ? "ephemeral" : "approval_required", approval: read || ephemeral ? "none" : "host", recoveryEligible: read,
    resultClass: read ? "observation" : "operation", rehydratable: read,
    continuation: read ? (capability?.resultKind === "archive_page" ? "archive_offset" : "published_schema") : "none"
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
  return classifyObjective(text).kind === "review";
}

export function classifyObjective(text: string): TaskObjective {
  const request = String(text || "").replace(NO_CHANGE, " ");
  const kind = CHANGE_REQUEST.test(request)
    ? /빌드|컴파일|실행|돌려|\b(?:build|compile|run|execute)\b/iu.test(request) ? "build" : "mutate"
    : REVIEW_REQUEST.test(request) ? "review" : "explain";
  const requiredEffects = new Set<"read" | "mutation" | "long_running">(["read"]);
  if (kind === "mutate" || kind === "build") requiredEffects.add("mutation");
  if (kind === "build") requiredEffects.add("long_running");
  return { kind, requiredEffects, mutationState: "none" };
}

export class ToolCapabilityRegistry {
  readonly tools: Array<RemoteToolLike>;
  readonly collisions: Array<string>;
  constructor(tools: Array<RemoteToolLike>) {
    this.collisions = [...new Set(tools.filter(t => tools.filter(other => other.name === t.name).length > 1).map(t => t.name))];
    this.tools = tools.filter(t => !this.collisions.includes(t.name));
  }
  resolve(name: string) { return this.tools.find(t => t.name === name); }
  mutationProfile() {
    return this.tools.flatMap(tool => {
      const actions = operationCapabilities(tool).filter(op => op.effect === "mutation" && op.action).map(op => op.action);
      if (!actions.length) return this.isMutation(tool, {name:tool.name,arguments:{}}) ? [tool] : [];
      const schema = tool.parametersJsonSchema as {properties?:Record<string,unknown>;required?:string[]};
      const narrowed = Object.create(tool) as RemoteToolLike;
      Object.defineProperty(narrowed,"parametersJsonSchema",{enumerable:true,value:{...schema,
        properties:{...schema?.properties,action:{type:"string",enum:actions}},required:[...new Set([...(schema?.required||[]),"action"])]}});
      restrictedOperationProfiles.set(narrowed,actions);
      return [narrowed];
    });
  }
  resultKind(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">) {
    return this.resolve(tool.name) === tool ? capabilityFor(tool, request)?.resultKind ?? null : null;
  }
  needsReadReservation(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">) {
    return this.resolve(tool.name) === tool && needsReadReservation(tool, request);
  }
  isSourceBody(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">) { return this.resultKind(tool, request) === "source_body"; }
  isMutation(tool: RemoteToolLike, request: Pick<ToolCallRequest, "name" | "arguments">): boolean {
    if (request.name !== tool.name || this.resolve(tool.name) !== tool) return false;
    return capabilityFor(tool, request)?.effect === "mutation";
  }
  mutationOutcomes(messages: readonly ChatMessage[]): readonly ("applied" | "failed")[] {
    const requests = new Map<string, ToolCallRequest | null>();
    for (const message of messages) for (const request of message.getToolCallRequests()) {
      const id = String(request.id || "");
      requests.set(id, requests.has(id) ? null : request);
    }
    const outcomes: ("applied" | "failed")[] = [];
    for (const message of messages) for (const result of message.getToolCallResults()) {
      const request = requests.get(String(result.toolCallId || ""));
      const tool = request && this.resolve(request.name);
      if (!request || !tool || !this.isMutation(tool, request)) continue;
      const value = decodeToolResultRecord(result.content).value;
      const semantics = resultSemantics(value, tool.pluginIdentifier);
      if (semantics.operationStatus === "completed") outcomes.push("applied");
      else if (["failed", "canceled", "outcome_unknown"].includes(semantics.operationStatus)) outcomes.push("failed");
    }
    return Object.freeze(outcomes);
  }
  /** Every tool except the manifest's known file/project mutations, for a
   * review-only request. Validators, long-running checks and tools of other
   * providers stay: only what the request ruled out is withheld. */
  reviewProfile() {
    return this.tools.filter(tool => {
      const capability = capabilityFor(tool);
      return !(capability?.scope === "unreal" && capability.effect === "mutation");
    });
  }
  readProfile() {
    return this.tools.filter(hasReadCapability).map(tool => {
      const actions = readActions(tool);
      if (!actions.length) return tool;
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
