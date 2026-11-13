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
/** Telemetry of the offered surface; dispatch still checks the actual request. */
export function hasMutationCapability(tool: RemoteToolLike): boolean {
  if (readOnlyOperationProfiles.has(tool)) return false;
  const allowed = restrictedOperationProfiles.get(tool);
  return operationCapabilities(tool).some(op => op.effect === "mutation"
    && (!op.action || !allowed || allowed.includes(op.action)));
}
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
// "…위반하는 것들 … 찾을것" asked for an audit and was classified explain
// (conversation 1793984074550): every form of 찾-, and the defect nouns.
const REVIEW_REQUEST = /검토|감사|리뷰|분석|점검|조사|찾(?:아|을|기|는|으)|문제점|위반|결함|확인해|설명|평가|진단|\b(?:review|audit|analy[sz]e|inspect|find|explain|assess|diagnose|investigate)\b/iu;
// "옮겨, 그리고 그 이후 단계도 너가 알아서 해봐" (conversation 1794419304890) moved code.
const CHANGE_REQUEST = /수정|고쳐|고치|구현|추가해|추가하|변경|바꿔|바꾸|적용|리팩|삭제|지워|만들어|작성해|생성|패치|개선해|빌드|컴파일|실행|돌려|커밋|푸시|옮겨|옮기자|옮겨줘|이동해|\b(?:fix|implement|change|modify|edit|apply|refactor|delete|remove|create|write|add|patch|build|compile|run|execute|commit|push|rename)\b/iu;
const NO_CHANGE = /(?:코드\s*)?(?:수정|변경)하지\s*(?:말고|마|않고)|바꾸지\s*(?:말고|마|않고)|(?:do not|don't)\s+(?:edit|change|modify)\b[^.,]*|without\s+(?:changing|editing|modifying)\b[^.,]*|no\s+(?:code\s+)?changes/giu;

// "그럼 지금 수정 계획 세워봐. 단계적으로" asked for a plan (conversation
// 1794393906420) but '수정' made it mutate. Writing a plan changes nothing,
// even when it names the change; asking to carry it out does.
const PLAN_AUTHORING = /(?:계획|플랜|로드맵|방안)(?:을|를|만|부터|도|좀)?\s*(?:좀\s*|한번\s*|먼저\s*|다시\s*|단계적으로\s*|단계별로\s*)?(?:세워|세우자|세울래|짜\s*(?:봐|줘|보|주|자)|수립|작성해|작성하|잡아|만들어|정리해|제안해)|\b(?:make|write|draft|create|outline|propose)\s+(?:a\s+|an\s+|the\s+)?(?:step[- ]by[- ]step\s+)?(?:plan|roadmap)\b/iu;
const EXECUTE_NOW = /고쳐|고치|구현|적용해|적용하|진행|실행|반영|수정해|수정하|변경해|바꿔|끝까지|마저|\b(?:implement|apply it|go ahead|proceed|execute)\b/iu;
// After a plan-only request, these run the plan; "계속"/"continue" keeps planning.
// "넘어가" moves on to the plan's next step (conversation 1794419304890).
const APPROVES_PLAN = /진행|쭉|끝까지|계획대로|그대로|실행|구현|적용|넘어가|^\s*(?:그럼|그러면|좋아|네|응|ㅇㅇ|ok|okay)?\s*(?:해|해줘|해주세요|하자|해봐|해줘봐)\s*[.!]*\s*$|\b(?:go ahead|proceed|do it|implement|apply)\b/iu;

/** A request to write a plan, naming no instruction to carry it out now. */
export function planOnlyRequest(text: string): boolean {
  const request = String(text || "").replace(NO_CHANGE, " ");
  return PLAN_AUTHORING.test(request) && !EXECUTE_NOW.test(request);
}

/** A request that asks only to review, find or explain, naming no change,
 * build or run: its rounds need no file/project mutation schemas.
 * Ambiguous or elliptical requests keep the full surface. */
export function readOnlyReviewRequest(text: string): boolean {
  return classifyObjective(text).kind === "review";
}

// Only a build or a request scoped to compiler failures is discharged by
// a successful build. An unspecified change, review, test or run needs its own evidence.
function buildAcceptance(request: string): Pick<TaskObjective, "buildAloneSufficient" | "requestedBuild"> {
  const project = /["']([^"']+\.uproject)["']|([^\s"'()]+\.uproject)\b/iu.exec(request);
  const projectSelector = project?.[1] || project?.[2];
  const target = /\b([A-Za-z_]\w*(?:Editor|Server|Client))\b/u.exec(request)?.[1];
  const configuration = /\b(DebugGame|Debug|Development|Shipping|Test)\b/u.exec(request)?.[1];
  const platform = /\b(Win64|Linux|Mac|Android|IOS)\b/iu.exec(request)?.[1];
  const isBuild = /빌드|컴파일|\b(?:build|compile)\b/iu.test(request);
  // Authorize a positive result only for recognized build-only phrasing.
  // Unrecognized work, including mixed verbs outside the generic classifier,
  // remains unknown. Remove known project/target selectors first.
  let remaining = request;
  for (const selector of [projectSelector, target, platform, configuration])
    if (selector) remaining = remaining.replace(selector, " ");
  const compileRepair = /(?:빌드|컴파일(?:러)?|링크)\s*(?:오류|에러|실패)(?:들)?(?:만)?(?:를|을)?\s*(?:수정|고쳐|고치|해결|고친|잡아)(?:하고|해서|한|해줘|해)?|\b(?:fix|resolve|repair)\s+(?:the\s+|all\s+)?(?:build|compil(?:e|er|ation)|link(?:er)?)\s+(?:errors?|failures?)\b/giu;
  const buildVerb = /(?:다시\s*)?(?:빌드|컴파일)(?:까지|하고|만|도|를|는)?(?:\s*(?:진행|실행))?(?:해(?:주세요|줘|봐|라)?|하(?:세요|고|기|라))?|\b(?:rebuild|build|compile)\b/giu;
  const connective = /(?:그리고|그다음|프로젝트|다시|먼저|까지|하고|해서|줘|후)|\b(?:please|the|this|project|target|configuration|for|on|in|then|again|and|it)\b/giu;
  const residual = remaining.replace(compileRepair, " ").replace(buildVerb, " ").replace(connective, "").replace(/[^\p{L}\p{N}]/gu, "");
  return { buildAloneSufficient: isBuild && residual.length === 0,
    ...(projectSelector || target || configuration || platform ? { requestedBuild: { project: projectSelector, target, configuration, platform } } : {}) };
}

export function classifyObjective(text: string): TaskObjective {
  const request = String(text || "").replace(NO_CHANGE, " ");
  // A plan-only request runs read-only, as a review does: nothing it asks for needs a write.
  if (planOnlyRequest(text)) return { kind: "review", requiredEffects: new Set<"read" | "mutation" | "long_running">(["read"]), mutationState: "none" };
  const kind = CHANGE_REQUEST.test(request)
    ? /빌드|컴파일|실행|돌려|\b(?:build|compile|run|execute)\b/iu.test(request) ? "build" : "mutate"
    : REVIEW_REQUEST.test(request) ? "review" : "explain";
  const requiredEffects = new Set<"read" | "mutation" | "long_running">(["read"]);
  if (kind === "mutate" || kind === "build") requiredEffects.add("mutation");
  if (kind === "build") requiredEffects.add("long_running");
  return { kind, requiredEffects, mutationState: "none", ...(kind === "build" ? buildAcceptance(request) : {}) };
}

// What the assistant offered to do in its last answer. A change offer and a
// further-review offer in one message leave the approval ambiguous.
const PROPOSES_CHANGE = /(?:수정|패치|적용|변경|구현|반영|교체|추가|제거|이동|옮기)[^.?!\n]{0,16}?(?:하겠|할게|할까|해\s*드릴|진행하겠|진행할까|하면\s*될)|고치(?:겠|면)|\b(?:I(?:'ll| will| can) (?:fix|patch|apply|implement|change|update)|shall I (?:fix|patch|apply|implement|change))\b/iu;
const PROPOSES_REVIEW = /(?:더|추가로|이어서|계속|다음\s*파일도|나머지도)\s*(?:검토|조사|확인|분석|읽)|(?:검토|조사|분석)(?:를|을)?\s*(?:하겠|할게|할까|계속)|\b(?:(?:I(?:'ll| will| can)|shall I) (?:review|investigate|look into|check))\b/iu;
const PROPOSES_BUILD = /빌드(?:하겠|할게|할까|까지|도)|\bbuild\b/iu;
const DECLINES = /하지\s*마|말아|말고|그만|멈춰|중지|보류|아직|\b(?:don'?t|do not|not yet|stop|hold)\b/iu;
const ALREADY_IN_PLACE = /이미\s*(?:적용|반영|수정|되어|돼|구현)|already\s+(?:applied|fixed|in place|done|implemented)|변경(?:이|할\s*것이)?\s*(?:필요\s*)?없/iu;

/** A short approval of the change the last answer proposed (conversation
 * 1794469194476: "진행해" after "1~3번을 패치하겠습니다" ran as a review and
 * read 12 files without editing). The approval comes from the user; the
 * proposal only names what was approved. An earlier "do not change" stands. */
function approvedProposal(messages: readonly ChatMessage[], latest: string): TaskObjective | null {
  if (latest.length > 60 || !APPROVES_PLAN.test(latest) || DECLINES.test(latest)) return null;
  const users = messages.map((m, i) => m.isUserMessage() ? i : -1).filter(i => i >= 0);
  const previousUser = messages[users.at(-2) ?? -1]?.getText() || "";
  if (new RegExp(NO_CHANGE.source, "iu").test(previousUser)) return null;
  const answer = messages.slice((users.at(-2) ?? -1) + 1, users.at(-1)).filter(m => m.isAssistantMessage()).at(-1);
  const { visibleAssistantText } = require("./continuity-text.js") as { visibleAssistantText(text: string): string };
  const proposal = visibleAssistantText(answer?.getText() || "");
  if (!PROPOSES_CHANGE.test(proposal) || PROPOSES_REVIEW.test(proposal)) return null;
  const build = PROPOSES_BUILD.test(proposal);
  const items = [...proposal.matchAll(/^\s*(?:\d+[.)]|[-*])\s+(.+)$/gmu)].map(m => m[1].trim().slice(0, 120)).slice(0, 12);
  return { kind: build ? "build" : "mutate", mutationState: "none", approvedItems: items,
    ...(build ? buildAcceptance(previousUser + "\n" + proposal) : {}),
    requiredEffects: new Set<"read" | "mutation" | "long_running">(build ? ["read", "mutation", "long_running"] : ["read", "mutation"]) };
}

/** An answer that reports the approved change as already in place. */
export function reportsNoChangeNeeded(answer: string): boolean { return ALREADY_IN_PLACE.test(answer); }

/** Continue the existing user objective at the same boundary guidance uses.
 * Explicit new work keeps its own effects. Host approval preferences never
 * turn an acknowledgement into permission to widen a prior review. */
export function executionObjective(messages: readonly ChatMessage[]): TaskObjective {
  const latest = messages.filter(m => m.isUserMessage()).at(-1)?.getText() || "";
  const direct = classifyObjective(latest);
  if (direct.kind !== "explain") return direct;
  const approved = approvedProposal(messages, latest);
  if (approved) return approved;
  const normalized = messages.map((m, index) => ({ role: m.getRole(), text: m.getText(), index,
    toolRequests: m.getToolCallRequests() }));
  const { buildObjectiveContinuity } = require("./continuity-objectives.js");
  const previous = require("./continuity-memory.js").extractPriorContinuityState(normalized);
  const active = buildObjectiveContinuity(normalized, previous).activeObjective;
  if (!active?.continuedBy) return direct;
  // "진행해" after a plan-only request carries that plan out; "계속" continues writing it.
  if (planOnlyRequest(active.text) && APPROVES_PLAN.test(latest))
    return { kind: "mutate", requiredEffects: new Set<"read" | "mutation" | "long_running">(["read", "mutation"]), mutationState: "none",
      approvedItems: [] };
  return classifyObjective(active.text);
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
  /** Review checks may run, but file/project mutations are withheld for every
   * provider. Mixed tools publish only the remaining operations and the guard
   * independently enforces that profile, including confirmed overrides. */
  reviewProfile() {
    return this.tools.flatMap(tool => {
      const operations = operationCapabilities(tool);
      if (!operations.some(op => op.effect === "mutation")) return [tool];
      const actions = operations.filter(op => op.effect !== "mutation" && op.action).map(op => op.action);
      if (!actions.length) return [];
      const schema = tool.parametersJsonSchema as { properties?: Record<string, unknown>; required?: string[] };
      const narrowed = Object.create(tool) as RemoteToolLike;
      Object.defineProperty(narrowed, "parametersJsonSchema", { enumerable: true, value: { ...schema,
        properties: { ...schema?.properties, action: { type: "string", enum: actions } },
        required: [...new Set([...(schema?.required || []), "action"])] } });
      restrictedOperationProfiles.set(narrowed, actions);
      return [narrowed];
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
