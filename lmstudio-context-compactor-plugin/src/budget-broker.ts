import {
  MIN_WORKING_SPACE_TOKENS, PINNED_WORKING_SET_MIN_CHARS, PINNED_WORKING_SET_SHARE, POST_TOOL_REQUEST_TOKENS,
  SCALED_TARGET_RATIO, SCALED_TRIGGER_RATIO, UTF8_BYTES_PER_TOKEN_ESTIMATE, ENVELOPE_TOKENS, MIN_FINAL_OUTPUT_TOKENS,
  UNKNOWN_SIZE_MARGIN_TOKENS, type ContextBudgetMode, type ContextMeasurement, type DirectConfig
} from "./execution-contracts";
import { PERCENT_FIELDS } from "./config-defaults";
import type { RemoteToolLike } from "./tool-capability-registry";
import { RUNTIME_DATA_SHARE } from "./execution-contracts";
import { localObservationTools, capabilityFor, needsReadReservation } from "./tool-capability-registry";
import { operationCapabilities } from "./tool-capability-core.js";
import { READ_RESULT_CONTRACTS } from "./generated-tool-contract";
import { telemetryFingerprint } from "./evidence-telemetry";
import { decodeEnvelope } from "./tool-result-semantics.js";
import type { ReadBudgetDenialCode } from "./read-budget-denial";
const { ARCHIVE_READ_CONTRACT } = require("./evidence-read-contract.js");
export type RetryMinimum = { field: string; value: number };
function retryScope(tool: RemoteToolLike, args: Record<string, unknown>) {
  const { maxChars, byteBudget, maxBytes, repeatReceipt, ...scope } = args;
  return telemetryFingerprint({ provider: tool.pluginIdentifier, tool: tool.name, scope });
}
// A provider's source readers share one metadata shape; paths only lengthen it.
const metadataScope = (tool: RemoteToolLike) => String(tool.pluginIdentifier || "");
/** The reserve a direct provider keeps beyond rendered metadata before a body fits. */
const PROVIDER_RESPONSE_RESERVE_BYTES = 256;

export type Watermarks = {
  contextLength: number; configuredTargetTokens: number; configuredTriggerTokens: number;
  requiredGenerationReserve: number; safetyMarginTokens: number; mandatoryFloorTokens: number;
  configuredLowWaterTokens: number; effectiveLowWaterTokens: number; effectiveHighWaterTokens: number;
  hardInputCeiling: number; reservedToolResultTokens: number; requiredPostToolOverhead: number;
  projectedNextInput: number; preDispatchCompaction: boolean; targetUnreachable: boolean;
  blockingComponent: string | null;
  nextActionFit: boolean; minimumReadResultTokens: number;
  workingInputScaling: "auto" | "fixed";
  /** The mode that produced these numbers, the target and trigger it asked
   * for, and why the applied ones differ (percentage mode). */
  budgetMode: ContextBudgetMode; requestedTargetTokens: number; requestedTriggerTokens: number;
  budgetAdjustments: string[];
};
const tokens = (n: number) => Math.max(0, Math.trunc(Number.isFinite(n) ? n : 0));
const share = (contextLength: number, percent: number) => Math.floor(tokens(contextLength) * percent / 100);

type BudgetConfig = Pick<DirectConfig, "workingInputTargetTokens" | "workingInputTriggerTokens" | "safetyMarginTokens">
  & Partial<Pick<DirectConfig, "workingInputScaling" | "contextBudgetMode" | "contextTargetPercent" | "contextTriggerPercent">>;

/** The target and trigger shares. A target at or above the trigger cannot work, so both fall back to the defaults. */
export function budgetShares(config: Partial<Pick<DirectConfig, "contextTargetPercent" | "contextTriggerPercent">>) {
  const target = config.contextTargetPercent ?? PERCENT_FIELDS.contextTargetPercent.default;
  const trigger = config.contextTriggerPercent ?? PERCENT_FIELDS.contextTriggerPercent.default;
  return target < trigger ? { target, trigger, invalid: false }
    : { target: PERCENT_FIELDS.contextTargetPercent.default, trigger: PERCENT_FIELDS.contextTriggerPercent.default, invalid: true };
}

/** The output floor of percentage mode: a reasoning model needs room to think
 * before it writes (a recorded final report spent 7,586 tokens on reasoning),
 * but never more than a quarter of a small context. */
const OUTPUT_FLOOR_TOKENS = 4096, OUTPUT_FLOOR_SHARE = 25, SAFETY_FLOOR_TOKENS = 512;

export type SessionBudget = {
  mode: ContextBudgetMode; contextLength: number; contextLengthSource: "model" | "configured_fallback";
  outputReserveTokens: number; safetyMarginTokens: number;
  /** Percentage mode only: what was asked for, in percent and in tokens of this context. */
  requested: { targetPercent: number; triggerPercent: number; outputPercent: number; safetyPercent: number;
    targetTokens: number; triggerTokens: number; outputTokens: number; safetyTokens: number; outputMaxTokens: number } | null;
  adjustments: string[];
};

/** The one place the session output reserve and safety margin are derived
 * (revised plan 2026-11-10, D2/D3). Legacy modes return the configured
 * tokens unchanged. The saved percentages are never rewritten by a clamp. */
export function resolveSessionBudget(config: Pick<DirectConfig, "contextBudgetMode" | "contextBudgetRequested" | "maxOutputReserve"
  | "safetyMarginTokens" | "contextTargetPercent" | "contextTriggerPercent" | "contextOutputPercent" | "contextSafetyPercent"
  | "contextOutputMaxTokens">, contextLength: number, contextLengthSource: SessionBudget["contextLengthSource"]): SessionBudget {
  const C = tokens(contextLength);
  if (config.contextBudgetMode !== "percentage") return { mode: config.contextBudgetMode, contextLength: C, contextLengthSource,
    outputReserveTokens: config.maxOutputReserve, safetyMarginTokens: config.safetyMarginTokens, requested: null,
    adjustments: config.contextBudgetRequested === "percentage" ? ["percentage_unsupported_with_legacy_working_context"] : [] };
  const shares = budgetShares(config), adjustments: string[] = shares.invalid ? ["invalid_target_trigger_defaults"] : [];
  const outputTokens = share(C, config.contextOutputPercent), safetyTokens = share(C, config.contextSafetyPercent);
  let output = outputTokens;
  const floor = Math.min(OUTPUT_FLOOR_TOKENS, share(C, OUTPUT_FLOOR_SHARE));
  if (output < floor) { output = floor; adjustments.push("output_floor"); }
  if (output > config.contextOutputMaxTokens) { output = config.contextOutputMaxTokens; adjustments.push("output_cap"); }
  output = Math.max(MIN_FINAL_OUTPUT_TOKENS, output);
  const safety = Math.max(SAFETY_FLOOR_TOKENS, safetyTokens);
  if (safety > safetyTokens) adjustments.push("safety_floor");
  return { mode: "percentage", contextLength: C, contextLengthSource, outputReserveTokens: output, safetyMarginTokens: safety,
    requested: { targetPercent: shares.target, triggerPercent: shares.trigger, outputPercent: config.contextOutputPercent,
      safetyPercent: config.contextSafetyPercent, targetTokens: share(C, shares.target), triggerTokens: share(C, shares.trigger),
      outputTokens, safetyTokens, outputMaxTokens: config.contextOutputMaxTokens }, adjustments };
}

/** The configuration the execution runs with: in percentage mode the resolved
 * output reserve and safety margin replace the configured tokens, so every
 * existing reader of those two values follows the session budget. */
export function applySessionBudget<T extends Pick<DirectConfig, "maxOutputReserve" | "safetyMarginTokens" | "outputRecoveryMaxTokens" | "outputRecoveryFollowsReserve">>(
  config: T, budget: SessionBudget): T & { sessionContextLength: number } {
  if (budget.mode !== "percentage") return { ...config, sessionContextLength: budget.contextLength };
  return { ...config, sessionContextLength: budget.contextLength, maxOutputReserve: budget.outputReserveTokens,
    safetyMarginTokens: budget.safetyMarginTokens,
    // Numeric equality does not imply inheritance: the user may have set that cap explicitly.
    outputRecoveryMaxTokens: config.outputRecoveryFollowsReserve
      ? budget.outputReserveTokens : config.outputRecoveryMaxTokens };
}

/** Budget allowances for model input, not backend slot/cache counters.
 * Byte-derived result reservations are estimates; only the next fully templated
 * exact measurement authorizes generation. */
export class BudgetBroker {
  // Execution-local, request-scope-specific retry floors. Never carried to the
  // next user turn or borrowed across tools, projects, queries or operations.
  private readonly retryFloors = new Map<string, RetryMinimum>();
  private readonly retryAliases = new Map<string, Set<string>>();
  // Execution-local size floor, not request authority: the largest response
  // metadata (result without its body, plus the provider's 256-byte reserve)
  // a provider's byteBudget reader returned. A window clamped below it returns
  // no body, so the read is denied before dispatch (2026-11-01 run, R6).
  private readonly metadataFloors = new Map<string, number>();
  private observeMetadataFloor(tool: RemoteToolLike, args: Record<string, unknown>, envelope: ReturnType<typeof decodeEnvelope>) {
    if (readResultContract(tool, args).field !== "byteBudget") return;
    const value = envelope.value as Record<string, unknown> | undefined;
    // Only a successful response separates metadata from body. A failure's
    // retry minimum can include a long first line of that one file; it stays
    // a request-scoped retry floor (observeResult) and never becomes the
    // provider-wide metadata cost (revised plan §E: one 10,414 B line made
    // every small file of the provider unreadable).
    const bytes = envelope.ok && typeof value?.content === "string"
      ? Buffer.byteLength(JSON.stringify({ ...value, content: "" }), "utf8") + PROVIDER_RESPONSE_RESERVE_BYTES : 0;
    const key = metadataScope(tool);
    if (bytes > (this.metadataFloors.get(key) || 0)) this.metadataFloors.set(key, bytes);
  }
  observeResult(tool: RemoteToolLike, args: Record<string, unknown>, envelope: ReturnType<typeof decodeEnvelope>, tools: readonly RemoteToolLike[] = []) {
    this.observeMetadataFloor(tool, args, envelope);
    const key = retryScope(tool, args);
    const clear = (scope: string) => {
      for (const alias of this.retryAliases.get(scope) || [scope]) { this.retryFloors.delete(alias); this.retryAliases.delete(alias); }
    };
    if (envelope.ok) clear(key);
    else if ("minimum" in envelope.retry && envelope.retry.minimum) {
      const minimum = envelope.retry.minimum;
      clear(key); this.retryFloors.set(key, { ...minimum });
      // Only the provider's registered source-read continuation may inherit
      // this floor. A suggestion cannot lend authority to another provider,
      // project, path, query or operation. It is still subject to the guard.
      const advice = envelope.value?.suggestion;
      const target = tools.filter(t => t.name === advice?.tool && t.pluginIdentifier === tool.pluginIdentifier);
      const next = advice?.args;
      if (["read_file", "read_file_range"].includes(tool.name) && target.length === 1
        && ["read_file", "read_file_range"].includes(target[0].name)
        && next && typeof next === "object" && !Array.isArray(next) && typeof args.path === "string"
        && next.path === args.path && (next.project ?? args.project) === args.project
        && Object.keys(args).every(k => ["byteBudget", "repeatReceipt", "lineNumbers"].includes(k)
          || next[k] === args[k])
        && Object.keys(next).every(k => ["path", "project", "byteBudget", "startLine", "endLine", "lineNumbers"].includes(k))
        && capabilityFor(target[0], { name: target[0].name, arguments: next })?.effect === "read") {
        const contract = readResultContract(target[0], next);
        if (contract.field === minimum.field && minimum.value >= contract.minimum && minimum.value <= contract.maximum) {
          const alias = retryScope(target[0], next), group = new Set([key, alias]);
          this.retryFloors.set(alias, { ...minimum });
          for (const member of group) this.retryAliases.set(member, group);
        }
      }
    }
    while (this.retryFloors.size > 256) clear(this.retryFloors.keys().next().value!);
  }
  retryMinimum = (tool: RemoteToolLike, args: Record<string, unknown>): RetryMinimum | undefined => {
    const scoped = this.retryFloors.get(retryScope(tool, args));
    const observed = this.metadataFloors.get(metadataScope(tool));
    if (!observed || readResultContract(tool, args).field !== "byteBudget" || (scoped && scoped.value >= observed)) return scoped;
    return { field: "byteBudget", value: observed };
  };
  constructor(readonly config: BudgetConfig) { }
  watermarks(measured: ContextMeasurement, mandatoryFloorTokens = 0, hasReadTools = true, minimumReadTokens = 2048): Watermarks {
    const contextLength = tokens(measured.contextLength);
    const requiredGenerationReserve = tokens(measured.outputReserve);
    const safetyMarginTokens = tokens(this.config.safetyMarginTokens);
    const hardInputCeiling = Math.max(0, contextLength - safetyMarginTokens - requiredGenerationReserve);
    // One shared expected envelope budget, and room for this round's generated
    // tool request. The next round's own output reserve is already excluded by
    // that round's hard ceiling, so it is not subtracted here a second time.
    const minimumReadResultTokens = hasReadTools ? tokens(minimumReadTokens) : 0;
    const reservedToolResultTokens = hasReadTools
      ? Math.max(minimumReadResultTokens, Math.min(4096, Math.floor(hardInputCeiling / 8))) : 0;
    const requiredPostToolOverhead = hasReadTools ? Math.min(requiredGenerationReserve, POST_TOOL_REQUEST_TOKENS) : 0;
    const safeTrigger = Math.max(0, hardInputCeiling - reservedToolResultTokens - requiredPostToolOverhead);
    const percentage = this.config.contextBudgetMode === "percentage";
    // Configured trigger/target are floors in "auto" mode, so a larger loaded
    // context keeps proportionally more conversation before compaction.
    const scaled = this.config.workingInputScaling !== "fixed";
    const shares = budgetShares(this.config);
    // Percentage mode asks for shares of the whole context length. Auto and
    // fixed keep their token settings (scaled against the usable input in auto).
    const trigger = percentage ? share(contextLength, shares.trigger)
      : scaled ? Math.max(tokens(this.config.workingInputTriggerTokens), Math.floor(hardInputCeiling * SCALED_TRIGGER_RATIO))
        : tokens(this.config.workingInputTriggerTokens);
    const target = percentage ? share(contextLength, shares.target)
      : scaled ? Math.max(tokens(this.config.workingInputTargetTokens), Math.floor(hardInputCeiling * SCALED_TARGET_RATIO))
        : tokens(this.config.workingInputTargetTokens);
    const effectiveHighWaterTokens = Math.min(trigger, safeTrigger);
    // Percentage mode keeps the requested target unless it would leave less
    // than one tool result between target and trigger: an explicit
    // re-compaction gap in place of the fixed 0.75 of high water.
    const configuredLowWaterTokens = percentage ? Math.min(target, Math.max(0, effectiveHighWaterTokens - reservedToolResultTokens))
      : Math.min(target, Math.floor(effectiveHighWaterTokens * 0.75));
    // Compacting to the bare mandatory floor leaves no room for the next result
    // and causes immediate re-compaction. Keep working space when it fits.
    const workingSpaceLowWater = (scaled || percentage) && mandatoryFloorTokens > 0
      ? Math.min(effectiveHighWaterTokens, tokens(mandatoryFloorTokens) + MIN_WORKING_SPACE_TOKENS) : 0;
    const effectiveLowWaterTokens = Math.max(configuredLowWaterTokens, tokens(mandatoryFloorTokens), workingSpaceLowWater);
    const budgetAdjustments = !percentage ? [] : [
      ...(effectiveHighWaterTokens < trigger ? ["tool_result_reserve"] : []),
      ...(configuredLowWaterTokens < target ? ["hysteresis"] : []),
      ...(tokens(mandatoryFloorTokens) > configuredLowWaterTokens ? ["mandatory_floor"]
        : effectiveLowWaterTokens > configuredLowWaterTokens ? ["working_space"] : [])];
    const projectedNextInput = measured.inputTokens + reservedToolResultTokens + requiredPostToolOverhead;
    return {
      contextLength, configuredTargetTokens: this.config.workingInputTargetTokens,
      configuredTriggerTokens: this.config.workingInputTriggerTokens, requiredGenerationReserve, safetyMarginTokens,
      mandatoryFloorTokens: tokens(mandatoryFloorTokens), configuredLowWaterTokens, effectiveLowWaterTokens,
      effectiveHighWaterTokens, hardInputCeiling, reservedToolResultTokens, requiredPostToolOverhead,
      projectedNextInput, preDispatchCompaction: hasReadTools && projectedNextInput > hardInputCeiling,
      minimumReadResultTokens, workingInputScaling: scaled ? "auto" : "fixed",
      budgetMode: percentage ? "percentage" : scaled ? "legacy_auto" : "absolute",
      requestedTargetTokens: target, requestedTriggerTokens: trigger, budgetAdjustments,
      nextActionFit: measured.inputTokens + requiredPostToolOverhead + minimumReadResultTokens <= hardInputCeiling,
      targetUnreachable: mandatoryFloorTokens > configuredLowWaterTokens,
      blockingComponent: mandatoryFloorTokens > configuredLowWaterTokens ? "mandatory_objective_evidence_and_template" : null
    };
  }
  /** Envelope characters the current turn's working set may keep verbatim
   * through projection and compaction: a share of low water, so the pinned
   * reads plus the mandatory floor still leave room under high water. */
  pinnedWorkingSetChars(water: Pick<Watermarks, "effectiveLowWaterTokens">): number {
    return Math.max(PINNED_WORKING_SET_MIN_CHARS,
      Math.floor(tokens(water.effectiveLowWaterTokens) * PINNED_WORKING_SET_SHARE * UTF8_BYTES_PER_TOKEN_ESTIMATE));
  }
  beginBatch(measured: ContextMeasurement, hasReadTools = true, reserveFloor = 0) {
    const budget = this.watermarks(measured, 0, hasReadTools);
    return new BatchReservation(Math.max(0, budget.hardInputCeiling - measured.inputTokens - budget.requiredPostToolOverhead), reserveFloor);
  }
  runtimeDataAllowanceChars(water: Pick<Watermarks, "effectiveLowWaterTokens">): number {
    return Math.floor(tokens(water.effectiveLowWaterTokens) * RUNTIME_DATA_SHARE * UTF8_BYTES_PER_TOKEN_ESTIMATE);
  }
}

/** Held and consumed allocations both count until the batch ends. Settling a
 * result must not let a later parallel call spend the same capacity again. */
export class BatchReservation {
  private entries = new Map<string, { tokens: number; reservedTokens: number; actualEnvelopeBytes: number | null;
    overrun: boolean; state: "held" | "consumed" | "released" }>();
  private closed = false;
  constructor(readonly capacity: number, readonly reserveFloor = 0) { }
  get used() { return [...this.entries.values()].reduce((n, e) => n + (e.state === 'released' ? 0 : e.tokens), 0); }
  get available() { return Math.max(0, this.capacity - this.used); }
  get exceeded() { return this.used > this.capacity || [...this.entries.values()].some(e => e.overrun); }
  denialReason(id: string, minimum: number): ReadBudgetDenialCode | null {
    if (this.closed) return "batch_closed";
    if (this.entries.has(id)) return "duplicate_reservation";
    if (this.exceeded) return "returned_result_overrun";
    if (this.available < tokens(minimum) || this.available === 0)
      return [...this.entries.values()].some(e => e.state === "held")
        ? "held_reservations_insufficient" : "remaining_capacity_insufficient";
    return null;
  }
  reserve(id: string, requested: number, minimum = requested): number {
    if (this.closed || this.exceeded || this.entries.has(id)) return 0;
    let amount = Math.min(tokens(requested), this.available);
    if (this.available - amount < this.reserveFloor && this.available - this.reserveFloor >= tokens(minimum))
      amount = this.available - this.reserveFloor;
    if (amount < tokens(minimum) || amount === 0) return 0;
    this.entries.set(id, { tokens: amount, reservedTokens: amount, actualEnvelopeBytes: null,
      overrun: false, state: 'held' }); return amount;
  }
  accountFixed(id: string) {
    if (!this.closed && !this.entries.has(id)) this.entries.set(id, {tokens:ENVELOPE_TOKENS,
      reservedTokens:ENVELOPE_TOKENS,actualEnvelopeBytes:null,overrun:false,state:"consumed"});
  }
  settle(id: string, outcome: "returned" | "denied" | "failed" | "canceled"): boolean {
    const entry = this.entries.get(id);
    if (!entry || entry.state !== "held") return false;
    entry.state = outcome === 'returned' ? 'consumed' : 'released'; return true;
  }
  receive(id: string, actualEnvelopeBytes: number): boolean {
    const entry = this.entries.get(id);
    if (!entry || entry.state !== "held") return false;
    // Account received bytes using the reservation allowance, without claiming
    // they are exact model tokens. The next model input is measured separately.
    entry.tokens = Math.ceil(tokens(actualEnvelopeBytes) / UTF8_BYTES_PER_TOKEN_ESTIMATE) + ENVELOPE_TOKENS;
    entry.actualEnvelopeBytes = tokens(actualEnvelopeBytes);
    entry.overrun = entry.tokens > entry.reservedTokens;
    entry.state = "consumed";
    return true;
  }
  close() { if (this.closed) return; for (const id of this.entries.keys()) this.settle(id, 'canceled'); this.closed = true; }
  snapshot() {
    return {
      capacity: this.capacity, reserveFloor: this.reserveFloor, used: this.used, available: this.available, closed: this.closed,
      accountingUnit: "conservative_byte_derived_allowance_not_exact_model_tokens",
      held: [...this.entries.values()].filter(e => e.state === "held").reduce((n, e) => n + e.tokens, 0),
      consumed: [...this.entries.values()].filter(e => e.state === "consumed").reduce((n, e) => n + e.tokens, 0),
      exceeded: this.exceeded, nextDispatchAllowed: !this.closed && !this.exceeded,
      disposition: this.exceeded ? "retain_result_remeasure_before_next_model_call" : "within_reservation",
      entries: [...this.entries].map(([id, e]) => ({ id, ...e }))
};
  }
}

export function readResultContract(tool: RemoteToolLike, args: Record<string, unknown> = {}) {
  const properties = (tool.parametersJsonSchema as { properties?: Record<string, { minimum?: number; maximum?: number; default?: number; $comment?: string }> })?.properties || {};
  const nativeBytes = (READ_RESULT_CONTRACTS.nativeByteTools[tool.pluginIdentifier || ""] || []).includes(tool.name);
  const field = properties.byteBudget ? "byteBudget" : properties.maxChars ? "maxChars"
    : nativeBytes && properties.maxBytes ? "maxBytes" : null;
  const contract = field ? properties[field] : {};
  const capability = capabilityFor(tool, { name: tool.name, arguments: args });
  const archiveEnvelope = capability?.resultKind === "archive_page";
  // The manifest floor is in UTF-16 characters. Byte contracts need a safe
  // UTF-8 allowance; do not treat a character count as a byte count.
  const manifestMinimum = field ? (capability?.minimumEnvelopeChars || 0) * (field === "maxChars" ? 1 : 3) : 0;
  // A provider's source-file ceiling is not a useful default batch allocation.
  // Bound an omitted native window to 4 KiB; explicit requests can still ask
  // for more. Bytes already account for UTF-8, unlike character limits.
  const byteBudgetDefault = READ_RESULT_CONTRACTS.byteBudgetDefaults[tool.pluginIdentifier || ""];
  const defaultValue = field === "byteBudget" && byteBudgetDefault !== undefined
    && contract.$comment === "direct-json-text-utf8" ? contract.default ?? byteBudgetDefault
    : field === "maxBytes" ? Math.min(contract.default ?? READ_RESULT_CONTRACTS.maxBytesCeiling, READ_RESULT_CONTRACTS.maxBytesCeiling)
    : contract.default ?? (field === "maxChars" ? 4096
    : READ_RESULT_CONTRACTS.toolDefaults[tool.name] ?? READ_RESULT_CONTRACTS.fieldDefault);
  // Token allowance per requested unit. UTF-8 bytes use the shared estimate;
  // raw file bytes may expand under JSON escaping; UTF-16 characters are
  // bounded for CJK text (about one token per character) plus escaping.
  // Only the provider default (not a tool's own published default) adapts.
  const adaptiveDefault = field === "byteBudget" && byteBudgetDefault !== undefined
    && contract.$comment === "direct-json-text-utf8" && contract.default === undefined;
  return { field, defaultValue, adaptiveDefault, archiveEnvelope, multiplier: field === "byteBudget" ? 1 / UTF8_BYTES_PER_TOKEN_ESTIMATE
    : field === "maxBytes" ? 1.25 / UTF8_BYTES_PER_TOKEN_ESTIMATE : 1.25,
    // The archive's schema accepts maxChars=1 for range validation, but its
    // identity envelope needs usable headroom before any body can be returned.
    minimum: Math.max(manifestMinimum, archiveEnvelope ? Math.max(ARCHIVE_READ_CONTRACT.minimumEnvelopeChars, tokens(contract.minimum ?? 1))
      : tokens(contract.minimum ?? (field === "maxChars" ? 128 : 1024))),
    maximum: tokens(contract.maximum ?? (field === "maxBytes" ? 2 * 1024 * 1024 : field === "byteBudget" ? 65536 : 4096)) };
}

export function minimumReadResultTokens(tools: RemoteToolLike[], archiveRequired = false): number {
  // Require an actionable source read when source readers are exposed. The
  // auxiliary archive reader may have no refs yet, so it cannot certify that
  // source acquisition fits. The guard reserves the actual selected request.
  const readers = tools.filter(tool => operationCapabilities(tool).some(op => ["source_body", "search_hits", "archive_page"].includes(op.resultKind)));
  const sources = readers.filter(tool => operationCapabilities(tool).some(op => ["source_body", "search_hits"].includes(op.resultKind)));
  const actionable = sources.length ? sources : readers;
  const minimumFor = (tool: RemoteToolLike) => {
    const c = readResultContract(tool);
    return c.field ? Math.ceil(c.minimum * c.multiplier) + UNKNOWN_SIZE_MARGIN_TOKENS : 3072 + UNKNOWN_SIZE_MARGIN_TOKENS;
  };
  const floorTools = tools.filter(tool => operationCapabilities(tool).some(op => op.roles?.includes("budget_floor")));
  const sourceMinimum = floorTools.length ? Math.max(...floorTools.map(minimumFor))
    : actionable.length ? Math.min(...actionable.map(minimumFor)) : 0;
  // Once returned evidence is archived, acquisition and rehydration are both
  // required lifecycle operations. A cheap source read cannot certify that
  // the archive is usable. This reserves either operation, not their sum:
  // parallel requests still compete in the existing shared batch broker.
  const archive = archiveRequired && readers.find(tool => capabilityFor(tool)?.resultKind === "archive_page"
    && localObservationTools.has(tool));
  return Math.max(sourceMinimum, archive ? minimumFor(archive) : 0);
}

/** The smallest default byteBudget an adaptive default shrinks to. */
const DEFAULT_READ_FLOOR_BYTES = 8192;
/** A read with any of these selects part of a file, so it is not a whole-file read. */
const WHOLE_FILE_SELECTORS = ["startLine", "endLine", "cursor", "offset", "offsetBytes", "startOffset", "nextOffset", "maxBytes"];

/** Conservative response estimate, never source availability or authority. */
export function estimateReadResponseBytes(hint: { sizeBytes: number | null; totalLines: number | null } | null,
  selector: Record<string, unknown> = {}): number | undefined {
  if (!hint || !Number.isSafeInteger(hint.sizeBytes) || hint.sizeBytes! < 0
    || ["offsetBytes", "nextOffsetBytes", "offset", "startOffset", "maxBytes"].some(key => selector[key] !== undefined)) return undefined;
  let bodyBytes = hint.sizeBytes!, lines = hint.totalLines ?? Math.ceil(bodyBytes / 32);
  if (selector.startLine !== undefined || selector.endLine !== undefined) {
    const start = Number(selector.startLine), end = Number(selector.endLine);
    if (!Number.isSafeInteger(hint.totalLines) || hint.totalLines! <= 0
      || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) return undefined;
    lines = end - start + 1; bodyBytes = Math.ceil(bodyBytes * lines / hint.totalLines!);
  }
  const bytes = Math.ceil(bodyBytes * 1.15) + lines * 8 + 2560;
  return Number.isSafeInteger(bytes) && bytes > 0 ? bytes : undefined;
}

export function reserveReadResult(batch: BatchReservation, id: string, tool: RemoteToolLike,
  args: Record<string, unknown>, knownResultBytes?: number, retryMinimum?: RetryMinimum, estimatedBytes?: number) {
  const capability = capabilityFor(tool, { name: tool.name, arguments: args });
  if (capability && !needsReadReservation(tool, { name: tool.name, arguments: args })) {
    batch.accountFixed(id);
    return { allowed: true, arguments: args, reservedTokens: ENVELOPE_TOKENS, bounded: false, limit: undefined };
  }
  const contract = readResultContract(tool, args);
  const { field } = contract;
  if (retryMinimum?.field === field) contract.minimum = Math.max(contract.minimum, retryMinimum.value);
  const requestedWindow = Number(args[field ?? ""] ?? contract.defaultValue);
  const requiredMinimum = field ? { field, value: contract.minimum } : undefined;
  if (contract.minimum > contract.maximum) return { allowed: false, arguments: args, reservedTokens: 0,
    bounded: field !== null, denialReason: "minimum_exceeds_published_limit" as const, requiredMinimum,
    limit: { field, requested: requestedWindow, effective: 0, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" } };
  // Known responses use the same allowance as receive(). Preserve the published
  // request window; the actual returned size, not that ceiling, is reserved.
  if (Number.isSafeInteger(knownResultBytes) && knownResultBytes! > 0 && requestedWindow >= contract.minimum) {
    const exact = Math.ceil(knownResultBytes! / UTF8_BYTES_PER_TOKEN_ESTIMATE) + ENVELOPE_TOKENS;
    const reserved = batch.reserve(id, exact, exact);
    const requested = Number(args[field ?? ""] ?? contract.defaultValue);
    if (reserved) return { allowed: true, arguments: field ? { ...args, [field]: requested } : args, reservedTokens: reserved, bounded: field !== null,
      limit: { field, requested, effective: requested, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" } };
    return { allowed: false, arguments: args, reservedTokens: 0, bounded: field !== null,
      limit: { field, requested, effective: 0, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" },
      denialReason: batch.denialReason(id, exact) || "invalid_reservation", requiredMinimum,
      allowance: { availableTokens: batch.available, minimumTokens: exact, capacityTokens: batch.capacity } };
  }
  // Character contracts allow for UTF-8/JSON expansion and envelope overhead.
  // This allocation does not replace the final exact tokenizer gate.
  const multiplier = field ? contract.multiplier : 1;
  const envelopeTokens = UNKNOWN_SIZE_MARGIN_TOKENS;
  // An omitted byteBudget takes at most half of what the batch still has, so a
  // large default reads small files whole in a wide context and parallel reads
  // still fit a narrow one; never below the former fixed 8 KiB default.
  const adaptive = field !== null && contract.adaptiveDefault && args[field] === undefined
    && contract.defaultValue > DEFAULT_READ_FLOOR_BYTES;
  const share = Math.floor((batch.available / 2 - envelopeTokens) / multiplier);
  const ceilingFromHint = Number.isSafeInteger(estimatedBytes) && estimatedBytes! > 0
    ? Math.max(contract.minimum, Math.ceil(estimatedBytes! * 1.25)) : Infinity;
  // An explicit budget below the estimated response of a whole-file read, or
  // of a line range, is raised to that estimate when it fits this batch's
  // share and the published maximum; otherwise the request stands. A budget is
  // a floor when what was asked for fits (revised plan §E: 18 files were cut
  // and re-read).
  const modelRequested = field !== null && args[field] !== undefined ? tokens(Number(args[field])) : undefined;
  const belowEstimate = field === "byteBudget" && modelRequested !== undefined
    && Number.isSafeInteger(estimatedBytes) && estimatedBytes! > modelRequested
    && estimatedBytes! <= contract.maximum && estimatedBytes! <= share;
  const wholeFile = belowEstimate && tool.name === "read_file"
    && !WHOLE_FILE_SELECTORS.some(key => args[key] !== undefined);
  // The same floor for a line range, whose estimate is sized for those lines
  // (estimateReadResponseBytes). Conversation 1794590083146: a 33-line read
  // with a small explicit budget ran at the 3,276-byte minimum, the response
  // metadata alone took about 2.4 KB of it, and four such reads came back one
  // line short, each costing another read.
  const lineRange = belowEstimate && tool.name === "read_file_range"
    && Number.isSafeInteger(Number(args.startLine)) && Number.isSafeInteger(Number(args.endLine))
    && !WHOLE_FILE_SELECTORS.some(key => key !== "startLine" && key !== "endLine" && args[key] !== undefined);
  const raised = wholeFile || lineRange;
  const requested = field ? Math.min(tokens(args[field] === undefined
    ? adaptive ? Math.max(DEFAULT_READ_FLOOR_BYTES, Math.min(contract.defaultValue, share)) : contract.defaultValue
    : raised ? estimatedBytes! : Number(args[field])), ceilingFromHint) : 3072;
  const allocation = field ? Math.max(contract.minimum, Math.min(contract.maximum, requested)) : requested;
  const minimum = field ? contract.minimum : requested;
  const minimumTokens = Math.ceil(minimum * multiplier) + envelopeTokens;
  const reserved = batch.reserve(id, Math.ceil(allocation * multiplier) + envelopeTokens, minimumTokens);
  if (!reserved) return { allowed: false, arguments: args, reservedTokens: 0, bounded: field !== null,
    limit: { field, requested, effective: 0, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" },
    denialReason: batch.denialReason(id, minimumTokens) || "invalid_reservation", requiredMinimum,
    allowance: { availableTokens: batch.available, minimumTokens, capacityTokens: batch.capacity } };
  const boundedValue = Math.min(allocation, Math.floor((reserved - envelopeTokens) / multiplier));
  return {
allowed: true, arguments: field ? { ...args, [field]: boundedValue } : args,
    reservedTokens: reserved, bounded: field !== null,
    limit: { field, requested, effective: boundedValue, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes",
      ...(raised ? { modelRequested, raisedTo: wholeFile ? "whole_file_estimate" : "line_range_estimate" } : {}) }
};
}

export function readBudgetFeedback(trace: Array<Record<string, unknown>>): string {
  const limits = trace.flatMap(call => {
    const limit = call.readResultLimit as { field?: string; requested?: number; effective?: number; unit?: string } | undefined;
    if (!limit?.field || limit.requested === limit.effective) return [];
    return [{ field: limit.field, requested: limit.requested, effective: limit.effective, unit: limit.unit,
      denial: call.budgetDenialReason || null, delivered: call.readResultSize || null }];
  }).slice(0, 4);
  return limits.length ? "Runtime read-result budget facts: " + JSON.stringify(limits)
    + ". Limits bound the full response, not source body length. For a whole-file read, omit byteBudget: the host sizes it from a listed file size when it has one, otherwise from its adaptive default. Keep a page budget only while paging one file by its returned cursor, and do not subtract previously read content. Consume delivered peers before retrying a denied read in a later round." : "";
}

/** A round shows the read budget no longer constrains reads: a bounded read
 * ran at its requested size and no read in the round was denied or lowered.
 * One success beside a denied peer is not a resolution. */
export function readBudgetResolved(trace: Array<Record<string, unknown>>): boolean {
  const limited = (call: Record<string, unknown>) => {
    const limit = call.readResultLimit as { field?: string; requested?: number; effective?: number } | undefined;
    return Boolean(call.budgetDenialReason) || call.validationState === "denied_batch_budget"
      || Boolean(limit?.field) && limit!.requested !== limit!.effective;
  };
  return !trace.some(limited) && trace.some(call => Boolean((call.readResultLimit as { field?: string } | undefined)?.field));
}

export type GenerationBudget = {
  desiredMaxTokens: number;
  appliedMaxTokens: number;
  headroomTokens: number;
  fit: boolean;
  clampedToHeadroom: boolean;
};

/**
 * Resolve the cap that will be sent to .act() from the same measured input
 * budget used by the compactor. A negative or already-reserved value is not
 * subtracted twice: headroom is context - input - safety only.
 */
export function resolveGenerationBudget(options: {
  desiredMaxTokens: number;
  contextLength: number;
  inputTokens: number;
  safetyMarginTokens: number;
  minimumTokens?: number;
}): GenerationBudget {
  const desiredMaxTokens = Math.max(0, Math.trunc(Number(options.desiredMaxTokens) || 0));
  const headroomTokens = Math.trunc(
    Number(options.contextLength) - Number(options.inputTokens) - Number(options.safetyMarginTokens),
  );
  const appliedMaxTokens = Math.max(0, Math.min(desiredMaxTokens, headroomTokens));
  const minimumTokens = Math.max(1, Math.trunc(Number(options.minimumTokens) || 1));
  return {
    desiredMaxTokens,
    appliedMaxTokens,
    headroomTokens,
    fit: appliedMaxTokens >= minimumTokens,
    clampedToHeadroom: appliedMaxTokens < desiredMaxTokens,
  };
}

export { readBudgetDenialMessage } from "./read-budget-denial";
