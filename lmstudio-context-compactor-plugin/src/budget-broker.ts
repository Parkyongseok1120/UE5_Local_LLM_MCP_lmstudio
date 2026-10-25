import {
  MIN_WORKING_SPACE_TOKENS, PINNED_WORKING_SET_MIN_CHARS, PINNED_WORKING_SET_SHARE, POST_TOOL_REQUEST_TOKENS,
  SCALED_TARGET_RATIO, SCALED_TRIGGER_RATIO, UTF8_BYTES_PER_TOKEN_ESTIMATE, type ContextMeasurement, type DirectConfig
} from "./execution-contracts";
import type { RemoteToolLike } from "./tool-capability-registry";
import { localObservationTools } from "./tool-capability-registry";
import { READ_RESULT_CONTRACTS } from "./generated-tool-contract";
const { ARCHIVE_READ_CONTRACT } = require("./evidence-read-contract.js");

export type Watermarks = {
  contextLength: number; configuredTargetTokens: number; configuredTriggerTokens: number;
  requiredGenerationReserve: number; safetyMarginTokens: number; mandatoryFloorTokens: number;
  configuredLowWaterTokens: number; effectiveLowWaterTokens: number; effectiveHighWaterTokens: number;
  hardInputCeiling: number; reservedToolResultTokens: number; requiredPostToolOverhead: number;
  projectedNextInput: number; preDispatchCompaction: boolean; targetUnreachable: boolean;
  blockingComponent: string | null;
  nextActionFit: boolean; minimumReadResultTokens: number;
  workingInputScaling: "auto" | "fixed";
};
const tokens = (n: number) => Math.max(0, Math.trunc(Number.isFinite(n) ? n : 0));

/** Budget allowances for model input, not backend slot/cache counters.
 * Byte-derived result reservations are estimates; only the next fully templated
 * exact measurement authorizes generation. */
export class BudgetBroker {
  constructor(readonly config: Pick<DirectConfig, "workingInputTargetTokens" | "workingInputTriggerTokens" | "safetyMarginTokens">
    & Partial<Pick<DirectConfig, "workingInputScaling">>) { }
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
    // Configured trigger/target are floors in "auto" mode, so a larger loaded
    // context keeps proportionally more conversation before compaction.
    const scaled = this.config.workingInputScaling !== "fixed";
    const trigger = scaled ? Math.max(tokens(this.config.workingInputTriggerTokens), Math.floor(hardInputCeiling * SCALED_TRIGGER_RATIO))
      : tokens(this.config.workingInputTriggerTokens);
    const target = scaled ? Math.max(tokens(this.config.workingInputTargetTokens), Math.floor(hardInputCeiling * SCALED_TARGET_RATIO))
      : tokens(this.config.workingInputTargetTokens);
    const effectiveHighWaterTokens = Math.min(trigger, safeTrigger);
    const configuredLowWaterTokens = Math.min(target, Math.floor(effectiveHighWaterTokens * 0.75));
    // Compacting to the bare mandatory floor leaves no room for the next result
    // and causes immediate re-compaction. Keep working space when it fits.
    const workingSpaceLowWater = scaled && mandatoryFloorTokens > 0
      ? Math.min(effectiveHighWaterTokens, tokens(mandatoryFloorTokens) + MIN_WORKING_SPACE_TOKENS) : 0;
    const effectiveLowWaterTokens = Math.max(configuredLowWaterTokens, tokens(mandatoryFloorTokens), workingSpaceLowWater);
    const projectedNextInput = measured.inputTokens + reservedToolResultTokens + requiredPostToolOverhead;
    return {
      contextLength, configuredTargetTokens: this.config.workingInputTargetTokens,
      configuredTriggerTokens: this.config.workingInputTriggerTokens, requiredGenerationReserve, safetyMarginTokens,
      mandatoryFloorTokens: tokens(mandatoryFloorTokens), configuredLowWaterTokens, effectiveLowWaterTokens,
      effectiveHighWaterTokens, hardInputCeiling, reservedToolResultTokens, requiredPostToolOverhead,
      projectedNextInput, preDispatchCompaction: hasReadTools && projectedNextInput > hardInputCeiling,
      minimumReadResultTokens, workingInputScaling: scaled ? "auto" : "fixed",
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
  beginBatch(measured: ContextMeasurement, hasReadTools = true) {
    const budget = this.watermarks(measured, 0, hasReadTools);
    return new BatchReservation(Math.max(0, budget.hardInputCeiling - measured.inputTokens - budget.requiredPostToolOverhead));
  }
}

/** Held and consumed allocations both count until the batch ends. Settling a
 * result must not let a later parallel call spend the same capacity again. */
export class BatchReservation {
  private entries = new Map<string, { tokens: number; reservedTokens: number; actualEnvelopeBytes: number | null;
    overrun: boolean; state: "held" | "consumed" | "released" }>();
  private closed = false;
  constructor(readonly capacity: number) { }
  get used() { return [...this.entries.values()].reduce((n, e) => n + (e.state === 'released' ? 0 : e.tokens), 0); }
  get available() { return Math.max(0, this.capacity - this.used); }
  get exceeded() { return this.used > this.capacity || [...this.entries.values()].some(e => e.overrun); }
  denialReason(id: string, minimum: number): string | null {
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
    const amount = Math.min(tokens(requested), this.available);
    if (amount < tokens(minimum) || amount === 0) return 0;
    this.entries.set(id, { tokens: amount, reservedTokens: amount, actualEnvelopeBytes: null,
      overrun: false, state: 'held' }); return amount;
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
    entry.tokens = Math.ceil(tokens(actualEnvelopeBytes) / UTF8_BYTES_PER_TOKEN_ESTIMATE) + 128;
    entry.actualEnvelopeBytes = tokens(actualEnvelopeBytes);
    entry.overrun = entry.tokens > entry.reservedTokens;
    entry.state = "consumed";
    return true;
  }
  close() { if (this.closed) return; for (const id of this.entries.keys()) this.settle(id, 'canceled'); this.closed = true; }
  snapshot() {
    return {
      capacity: this.capacity, used: this.used, available: this.available, closed: this.closed,
      accountingUnit: "conservative_byte_derived_allowance_not_exact_model_tokens",
      held: [...this.entries.values()].filter(e => e.state === "held").reduce((n, e) => n + e.tokens, 0),
      consumed: [...this.entries.values()].filter(e => e.state === "consumed").reduce((n, e) => n + e.tokens, 0),
      exceeded: this.exceeded, nextDispatchAllowed: !this.closed && !this.exceeded,
      disposition: this.exceeded ? "retain_result_remeasure_before_next_model_call" : "within_reservation",
      entries: [...this.entries].map(([id, e]) => ({ id, ...e }))
};
  }
}

export function readResultContract(tool: RemoteToolLike) {
  const properties = (tool.parametersJsonSchema as { properties?: Record<string, { minimum?: number; maximum?: number; default?: number; $comment?: string }> })?.properties || {};
  const nativeBytes = (READ_RESULT_CONTRACTS.nativeByteTools[tool.pluginIdentifier || ""] || []).includes(tool.name);
  const field = properties.byteBudget ? "byteBudget" : properties.maxChars ? "maxChars"
    : nativeBytes && properties.maxBytes ? "maxBytes" : null;
  const contract = field ? properties[field] : {};
  const archiveEnvelope = tool.name === "evidence_first_read_context" && localObservationTools.has(tool);
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
  return { field, defaultValue, adaptiveDefault, multiplier: field === "byteBudget" ? 1 / UTF8_BYTES_PER_TOKEN_ESTIMATE
    : field === "maxBytes" ? 1.25 / UTF8_BYTES_PER_TOKEN_ESTIMATE : 1.25,
    // The archive's schema accepts maxChars=1 for range validation, but its
    // identity envelope needs usable headroom before any body can be returned.
    minimum: archiveEnvelope ? Math.max(ARCHIVE_READ_CONTRACT.minimumEnvelopeChars, tokens(contract.minimum ?? 1))
      : tokens(contract.minimum ?? (field === "maxChars" ? 128 : 1024)),
    maximum: tokens(contract.maximum ?? (field === "maxBytes" ? 2 * 1024 * 1024 : field === "byteBudget" ? 65536 : 4096)) };
}

export function minimumReadResultTokens(tools: RemoteToolLike[], archiveRequired = false): number {
  // Require an actionable source read when source readers are exposed. The
  // auxiliary archive reader may have no refs yet, so it cannot certify that
  // source acquisition fits. The guard reserves the actual selected request.
  const sources = tools.filter(tool => tool.name !== "evidence_first_read_context");
  const actionable = sources.length ? sources : tools;
  const minimumFor = (tool: RemoteToolLike) => {
    const c = readResultContract(tool);
    return c.field ? Math.ceil(c.minimum * c.multiplier) + 1024 : 4096;
  };
  const sourceMinimum = actionable.length ? Math.min(...actionable.map(minimumFor)) : 0;
  // Once returned evidence is archived, acquisition and rehydration are both
  // required lifecycle operations. A cheap source read cannot certify that
  // the archive is usable. This reserves either operation, not their sum:
  // parallel requests still compete in the existing shared batch broker.
  const archive = archiveRequired && tools.find(tool => tool.name === "evidence_first_read_context"
    && localObservationTools.has(tool));
  return Math.max(sourceMinimum, archive ? minimumFor(archive) : 0);
}

/** The smallest default byteBudget an adaptive default shrinks to. */
const DEFAULT_READ_FLOOR_BYTES = 8192;
/** Headroom over a known earlier result size: metadata and a grown file. */
const KNOWN_SIZE_SLACK = 1.25;
const KNOWN_SIZE_MARGIN_BYTES = 512;

export function reserveReadResult(batch: BatchReservation, id: string, tool: RemoteToolLike,
  args: Record<string, unknown>, knownResultBytes?: number) {
  const contract = readResultContract(tool);
  const { field } = contract;
  // Character contracts allow for UTF-8/JSON expansion and envelope overhead.
  // This allocation does not replace the final exact tokenizer gate.
  const multiplier = field ? contract.multiplier : 1;
  const envelopeTokens = 1024;
  // An omitted byteBudget takes at most half of what the batch still has, so a
  // large default reads small files whole in a wide context and parallel reads
  // still fit a narrow one; never below the former fixed 8 KiB default.
  const adaptive = field !== null && contract.adaptiveDefault && args[field] === undefined
    && contract.defaultValue > DEFAULT_READ_FLOOR_BYTES;
  const share = Math.floor((batch.available / 2 - envelopeTokens) / multiplier);
  // The same unchanged request returned this many bytes before: reserve about
  // that, not a half-batch share, so parallel peers are not starved.
  const known = field === "byteBudget" && Number.isSafeInteger(knownResultBytes) && knownResultBytes! > 0
    ? Math.ceil(knownResultBytes! * KNOWN_SIZE_SLACK) + KNOWN_SIZE_MARGIN_BYTES : null;
  const requested = field ? tokens(args[field] === undefined
    ? known !== null ? Math.min(contract.defaultValue, known)
      : adaptive ? Math.max(DEFAULT_READ_FLOOR_BYTES, Math.min(contract.defaultValue, share)) : contract.defaultValue
    : known !== null ? Math.min(Number(args[field]), known) : Number(args[field])) : 3072;
  const allocation = field ? Math.max(contract.minimum, Math.min(contract.maximum, requested)) : requested;
  const minimum = field ? contract.minimum : requested;
  const minimumTokens = Math.ceil(minimum * multiplier) + envelopeTokens;
  const reserved = batch.reserve(id, Math.ceil(allocation * multiplier) + envelopeTokens, minimumTokens);
  if (!reserved) return { allowed: false, arguments: args, reservedTokens: 0, bounded: field !== null,
    limit: { field, requested, effective: 0, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" },
    denialReason: batch.denialReason(id, minimumTokens) || "invalid_reservation",
    allowance: { availableTokens: batch.available, minimumTokens, capacityTokens: batch.capacity } };
  const boundedValue = Math.min(allocation, Math.floor((reserved - envelopeTokens) / multiplier));
  return {
allowed: true, arguments: field ? { ...args, [field]: boundedValue } : args,
    reservedTokens: reserved, bounded: field !== null,
    limit: { field, requested, effective: boundedValue, unit: field === "maxChars" ? "utf16_code_units" : "utf8_bytes" }
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
    + ". Limits bound the full response, not source body length. Keep the requested page budget stable while advancing the returned cursor; do not subtract previously read content. Consume delivered peers before retrying a denied read in a later round." : "";
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

/** What a denied read tells the model: why, that delivered results are kept,
 * and the one next step. A denial means even the contract minimum does not
 * fit this batch, so no smaller limit is offered for the same batch. The
 * numbers are byte-derived allowances, not exact model tokens. */
export function readBudgetDenialMessage(reason: string,
  allowance?: { availableTokens: number; minimumTokens: number; capacityTokens: number }) {
  const head = `Shared read-result budget unavailable (${reason}).`;
  const kept = "Results already returned in this round are kept.";
  const room = allowance ? ` About ${allowance.availableTokens} of ${allowance.capacityTokens} estimated tokens remain; this read needs at least ${allowance.minimumTokens}.` : "";
  if (reason === "held_reservations_insufficient") {
    return `${head} Parallel reads in this batch hold the allowance.${room} ${kept} Request this read again by itself in the next round.`;
  }
  if (reason === "remaining_capacity_insufficient") {
    return `${head} The model input has no room for another result.${room} ${kept} Use them first; request this read alone in a later round.`;
  }
  if (reason === "returned_result_overrun") {
    return `${head} A returned result exceeded its reservation, so the input is remeasured before more reads. ${kept} Use them first.`;
  }
  return `${head} ${kept} Split the read batch; use delivered results before requesting more.`;
}
