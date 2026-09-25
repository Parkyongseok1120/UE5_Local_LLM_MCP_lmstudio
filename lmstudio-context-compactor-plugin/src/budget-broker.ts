import type { ContextMeasurement, DirectConfig } from "./execution-contracts";
import type { RemoteToolLike } from "./tool-capability-registry";
import { localObservationTools } from "./tool-capability-registry";

export type Watermarks = {
  contextLength: number; configuredTargetTokens: number; configuredTriggerTokens: number;
  requiredGenerationReserve: number; safetyMarginTokens: number; mandatoryFloorTokens: number;
  configuredLowWaterTokens: number; effectiveLowWaterTokens: number; effectiveHighWaterTokens: number;
  hardInputCeiling: number; reservedToolResultTokens: number; requiredPostToolOverhead: number;
  projectedNextInput: number; preDispatchCompaction: boolean; targetUnreachable: boolean;
  blockingComponent: string | null;
  nextActionFit: boolean; minimumReadResultTokens: number;
};
const tokens = (n: number) => Math.max(0, Math.trunc(Number.isFinite(n) ? n : 0));

/** Budget allowances for model input, not backend slot/cache counters.
 * Byte-derived result reservations are estimates; only the next fully templated
 * exact measurement authorizes generation. */
export class BudgetBroker {
  constructor(readonly config: Pick<DirectConfig, "workingInputTargetTokens" | "workingInputTriggerTokens" | "safetyMarginTokens">) { }
  watermarks(measured: ContextMeasurement, mandatoryFloorTokens = 0, hasReadTools = true, minimumReadTokens = 2048): Watermarks {
    const contextLength = tokens(measured.contextLength);
    const requiredGenerationReserve = tokens(measured.outputReserve);
    const safetyMarginTokens = tokens(this.config.safetyMarginTokens);
    const hardInputCeiling = Math.max(0, contextLength - safetyMarginTokens - requiredGenerationReserve);
    // One shared expected envelope budget, and room for this round's generated
    // request/prose. Post-tool overhead appears in projection exactly once.
    const minimumReadResultTokens = hasReadTools ? tokens(minimumReadTokens) : 0;
    const reservedToolResultTokens = hasReadTools
      ? Math.max(minimumReadResultTokens, Math.min(4096, Math.floor(hardInputCeiling / 8))) : 0;
    const requiredPostToolOverhead = hasReadTools ? requiredGenerationReserve : 0;
    const safeTrigger = Math.max(0, hardInputCeiling - reservedToolResultTokens - requiredPostToolOverhead);
    const effectiveHighWaterTokens = Math.min(tokens(this.config.workingInputTriggerTokens), safeTrigger);
    const configuredLowWaterTokens = Math.min(tokens(this.config.workingInputTargetTokens), Math.floor(effectiveHighWaterTokens * 0.75));
    const effectiveLowWaterTokens = Math.max(configuredLowWaterTokens, tokens(mandatoryFloorTokens));
    const projectedNextInput = measured.inputTokens + reservedToolResultTokens + requiredPostToolOverhead;
    return {
      contextLength, configuredTargetTokens: this.config.workingInputTargetTokens,
      configuredTriggerTokens: this.config.workingInputTriggerTokens, requiredGenerationReserve, safetyMarginTokens,
      mandatoryFloorTokens: tokens(mandatoryFloorTokens), configuredLowWaterTokens, effectiveLowWaterTokens,
      effectiveHighWaterTokens, hardInputCeiling, reservedToolResultTokens, requiredPostToolOverhead,
      projectedNextInput, preDispatchCompaction: hasReadTools && projectedNextInput > hardInputCeiling,
      minimumReadResultTokens,
      nextActionFit: measured.inputTokens + requiredPostToolOverhead + minimumReadResultTokens <= hardInputCeiling,
      targetUnreachable: mandatoryFloorTokens > configuredLowWaterTokens,
      blockingComponent: mandatoryFloorTokens > configuredLowWaterTokens ? "mandatory_objective_evidence_and_template" : null
    };
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
    entry.tokens = tokens(actualEnvelopeBytes) + 128;
    entry.actualEnvelopeBytes = tokens(actualEnvelopeBytes);
    entry.overrun = entry.tokens > entry.reservedTokens;
    entry.state = "consumed";
    return true;
  }
  close() { if (this.closed) return; for (const id of this.entries.keys()) this.settle(id, 'canceled'); this.closed = true; }
  snapshot() {
    return {
capacity: this.capacity, used: this.used, available: this.available, closed: this.closed,
      exceeded: this.exceeded, nextDispatchAllowed: !this.closed && !this.exceeded,
      disposition: this.exceeded ? "retain_result_remeasure_before_next_model_call" : "within_reservation",
      entries: [...this.entries].map(([id, e]) => ({ id, ...e }))
};
  }
}

export function readResultContract(tool: RemoteToolLike) {
  const properties = (tool.parametersJsonSchema as { properties?: Record<string, { minimum?: number; maximum?: number; default?: number }> })?.properties || {};
  const nativeBytes = ["mcp/unreal-agent", "mcp/unreal-rag"].includes(tool.pluginIdentifier || "")
    && ["read_file", "read_file_range"].includes(tool.name);
  const field = properties.byteBudget ? "byteBudget" : properties.maxChars ? "maxChars"
    : nativeBytes && properties.maxBytes ? "maxBytes" : null;
  const contract = field ? properties[field] : {};
  const archiveEnvelope = tool.name === "evidence_first_read_context" && localObservationTools.has(tool);
  const defaultValue = contract.default ?? (field === "maxBytes" ? 65536 : field === "maxChars" ? 4096
    : tool.name === "git_changed_files" ? 4096 : 32768);
  return { field, defaultValue, multiplier: field === "byteBudget" ? 1 : archiveEnvelope ? 3 : 6,
    // The archive's schema accepts maxChars=1 for range validation, but its
    // identity envelope needs usable headroom before any body can be returned.
    minimum: archiveEnvelope ? Math.max(2048, tokens(contract.minimum ?? 1))
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
    return c.field ? c.minimum * c.multiplier + 1024 : 4096;
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

export function reserveReadResult(batch: BatchReservation, id: string, tool: RemoteToolLike,
  args: Record<string, unknown>) {
  const contract = readResultContract(tool);
  const { field } = contract;
  // Character contracts allow for UTF-8/JSON expansion and envelope overhead.
  // This allocation does not replace the final exact tokenizer gate.
  const multiplier = field ? contract.multiplier : 1;
  const envelopeTokens = 1024;
  const requested = field ? Math.max(contract.minimum, Math.min(contract.maximum,
    tokens(args[field] === undefined ? contract.defaultValue : Number(args[field])))) : 3072;
  const minimum = field ? contract.minimum : requested;
  const reserved = batch.reserve(id, requested * multiplier + envelopeTokens, minimum * multiplier + envelopeTokens);
  if (!reserved) return { allowed: false, arguments: args, reservedTokens: 0, bounded: field !== null };
  const boundedValue = Math.min(requested, Math.floor((reserved - envelopeTokens) / multiplier));
  return {
allowed: true, arguments: field ? { ...args, [field]: boundedValue } : args,
    reservedTokens: reserved, bounded: field !== null
};
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

