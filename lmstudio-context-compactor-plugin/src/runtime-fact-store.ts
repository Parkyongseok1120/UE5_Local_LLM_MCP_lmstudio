import { RUNTIME_FACTS, type RuntimeFactEntry, type RuntimeFactEvent, type RuntimeFactKey } from "./execution-contracts";

const MAX_RETIRED_FACTS = 32;

/** The execution's runtime facts and the lifetime rules of RUNTIME_FACTS.
 * Removing a fact from this store is not enough: an earlier data block of the
 * model input may still carry its text. A replaced or cleared text is kept as
 * retired until an input that rewrote those blocks is committed, so the input
 * composer can drop it (revised plan 2026-11-07 §H1). */
export class RuntimeFactStore {
  private readonly facts = new Map<RuntimeFactKey, string>();
  private readonly retired: string[] = [];

  /** Setting the same text again is a no-op; a different text retires the old one. */
  set(key: RuntimeFactKey, text: string) {
    const prior = this.facts.get(key);
    if (prior === text) return;
    if (prior !== undefined) this.retire(prior);
    this.facts.set(key, text);
  }
  delete(key: RuntimeFactKey) {
    const prior = this.facts.get(key);
    if (prior === undefined) return;
    this.facts.delete(key);
    this.retire(prior);
  }
  /** Bounded: a text retired before it was ever shown never matches a block. */
  private retire(text: string) {
    if (!this.retired.includes(text)) this.retired.push(text);
    if (this.retired.length > MAX_RETIRED_FACTS) this.retired.shift();
  }
  has(key: RuntimeFactKey) { return this.facts.has(key); }
  get(key: RuntimeFactKey) { return this.facts.get(key); }
  /** Clears every fact whose rule names this event. */
  onEvent(event: RuntimeFactEvent) {
    for (const key of [...this.facts.keys()]) if (RUNTIME_FACTS[key].clearOn?.includes(event)) this.delete(key);
  }
  entries(): RuntimeFactEntry[] { return [...this.facts].map(([key, text]) => ({ key, text })); }
  /** Texts no longer current that an earlier, unrewritten data block may still hold. */
  get retiredTexts(): readonly string[] { return this.retired; }
  /** An input that replaced every shown data block was committed. */
  acknowledgeRewrite() { this.retired.length = 0; }
}

/** Whether a shown data part is a retired fact. Admission may cut a fact's
 * tail, so the shown part is compared by the retired text's head. */
export function isRetiredFact(part: string, retired: readonly string[]) {
  return retired.some(text => {
    const head = text.slice(0, 160);
    return part === text || head.length >= 40 && part.startsWith(head);
  });
}
