import type { ContextMeasurement, DirectConfig } from "./execution-contracts";
import type { RemoteToolLike } from "./tool-capability-registry";

export type Watermarks = {
  contextLength: number; configuredTargetTokens: number; configuredTriggerTokens: number;
  requiredGenerationReserve: number; safetyMarginTokens: number; mandatoryFloorTokens: number;
  configuredLowWaterTokens: number; effectiveLowWaterTokens: number; effectiveHighWaterTokens: number;
  hardInputCeiling: number; reservedToolResultTokens: number; requiredPostToolOverhead: number;
  projectedNextInput: number; preDispatchCompaction: boolean; targetUnreachable: boolean;
  blockingComponent: string | null;
};
const tokens = (n: number) => Math.max(0, Math.trunc(Number.isFinite(n) ? n : 0));

/** All quantities here are model-input token budgets, never backend slot/cache counters. */
export class BudgetBroker {
  constructor(readonly config: Pick<DirectConfig, "workingInputTargetTokens" | "workingInputTriggerTokens" | "safetyMarginTokens">) { }
  watermarks(measured: ContextMeasurement, mandatoryFloorTokens = 0, hasReadTools = true): Watermarks {
    const contextLength = tokens(measured.contextLength);
    const requiredGenerationReserve = tokens(measured.outputReserve);
    const safetyMarginTokens = tokens(this.config.safetyMarginTokens);
    const hardInputCeiling = Math.max(0, contextLength - safetyMarginTokens - requiredGenerationReserve);
    // One shared expected envelope budget, and room for this round's generated
    // request/prose. Post-tool overhead appears in projection exactly once.
    const reservedToolResultTokens = hasReadTools ? Math.min(4096, Math.floor(hardInputCeiling / 8)) : 0;
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
  private entries = new Map<string, { tokens: number; state: "held" | "consumed" | "released" }>();
  private closed = false;
  constructor(readonly capacity: number) { }
  get used() { return [...this.entries.values()].reduce((n, e) => n + (e.state === 'released' ? 0 : e.tokens), 0); }
  get available() { return Math.max(0, this.capacity - this.used); }
  reserve(id: string, requested: number, minimum = requested): number {
    if (this.closed || this.entries.has(id)) return 0;
    const amount = Math.min(tokens(requested), this.available);
    if (amount < tokens(minimum) || amount === 0) return 0;
    this.entries.set(id, { tokens: amount, state: 'held' }); return amount;
  }
  settle(id: string, outcome: "returned" | "denied" | "failed" | "canceled"): boolean {
    const entry = this.entries.get(id);
    if (!entry || entry.state !== "held") return false;
    entry.state = outcome === 'returned' ? 'consumed' : 'released'; return true;
  }
  receive(id: string, actualEnvelopeBytes: number): boolean {
    const entry = this.entries.get(id);
    if (!entry || entry.state !== "held") return false;
    // Returned UTF-8 bytes conservatively reserve the model tokens still to be
    // consumed; only unused capacity is reclaimed, never the result itself.
    entry.tokens = tokens(actualEnvelopeBytes) + 128;
    entry.state = "consumed";
    return true;
  }
  close() { if (this.closed) return; for (const id of this.entries.keys()) this.settle(id, 'canceled'); this.closed = true; }
  snapshot() {
    return {
capacity: this.capacity, used: this.used, available: this.available, closed: this.closed,
      entries: [...this.entries].map(([id, e]) => ({ id, ...e }))
};
  }
}

export function reserveReadResult(batch: BatchReservation, id: string, tool: RemoteToolLike,
  args: Record<string, unknown>) {
  const properties = (tool.parametersJsonSchema as { properties?: Record<string, { minimum?: number; maximum?: number }> })?.properties || {};
  const field = properties.byteBudget ? "byteBudget" : properties.maxChars ? "maxChars" : null;
  // Byte count is a conservative token allowance. Character contracts reserve
  // UTF-8/JSON expansion as well; envelope overhead is a separate allocation.
  const multiplier = field === "maxChars" ? 6 : 1;
  const envelopeTokens = 1024;
  const requested = field ? tokens(Number(args[field]) || (field === "byteBudget" ? 24576 : 4096)) : 3072;
  const minimum = field ? tokens(properties[field].minimum ?? (field === "byteBudget" ? 1024 : 128)) : requested;
  const reserved = batch.reserve(id, requested * multiplier + envelopeTokens, minimum * multiplier + envelopeTokens);
  if (!reserved) return { allowed: false, arguments: args, reservedTokens: 0, bounded: field !== null };
  const boundedValue = Math.min(requested, Math.floor((reserved - envelopeTokens) / multiplier),
    field ? properties[field].maximum ?? Infinity : Infinity);
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

