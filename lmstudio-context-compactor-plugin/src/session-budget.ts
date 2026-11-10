import { applySessionBudget, resolveSessionBudget, type SessionBudget } from "./budget-broker";
import type { DirectConfig } from "./execution-contracts";

/** The loaded context length as the measurements read it, or the configured fallback. */
async function sessionContextLength(config: Pick<DirectConfig, "assumedContextLength">, tokenSource: unknown)
  : Promise<{ contextLength: number; source: SessionBudget["contextLengthSource"] }> {
  const source = tokenSource as { getContextLength?: () => Promise<number> };
  try {
    if (typeof source.getContextLength === "function") {
      const loaded = await source.getContextLength();
      if (Number.isSafeInteger(loaded) && loaded >= 2048 && loaded <= 4_000_000) return { contextLength: loaded, source: "model" };
    }
  } catch { /* The fallback below is reported as such. */ }
  return { contextLength: config.assumedContextLength, source: "configured_fallback" };
}

/** Resolve this execution's budget once, right after the model is known, and
 * return the configuration the execution runs with (revised plan 2026-11-10,
 * D2/D6). The debug event carries what was requested, what is applied and
 * why they differ; in a legacy mode it also previews percentage mode, so the
 * switch can be judged before it is made. */
export async function sessionBudgetConfig(config: DirectConfig, tokenSource: unknown,
  debug: (value: Record<string, unknown>) => void): Promise<DirectConfig> {
  const { contextLength, source } = await sessionContextLength(config, tokenSource);
  const budget = resolveSessionBudget(config, contextLength, source);
  if (config.showDebugInfo) {
    const preview = budget.mode === "percentage" || config.contextManagementMode === "legacy" ? null
      : resolveSessionBudget({ ...config, contextBudgetMode: "percentage" }, contextLength, source);
    debug({ event: "context_budget", ...budget,
      ...(preview ? { percentagePreview: { outputReserveTokens: preview.outputReserveTokens, safetyMarginTokens: preview.safetyMarginTokens,
        requested: preview.requested, adjustments: preview.adjustments } } : {}) });
  }
  return applySessionBudget(config, budget);
}
