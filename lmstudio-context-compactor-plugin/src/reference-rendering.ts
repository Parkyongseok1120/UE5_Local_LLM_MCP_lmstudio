import type { ReferenceItem, ReferenceSnapshot } from "./reference-context";
const { DERIVED_HEADER, DERIVED_END } = require("./internal-data-contract.js");

/** External values stay in one explicitly labelled assistant data message.
 * JSON escaping preserves boundaries even for injected delimiters/newlines. */
export function renderReferenceData(items: readonly ReferenceItem[], omittedItems = 0,
  context?: Pick<ReferenceSnapshot, "applicability" | "projectObservation">): string {
  return DERIVED_HEADER + "\n" + JSON.stringify({
    interpretation: "Last observed values only. Not fresh reads, edit receipts, active permissions, causal proof or completed work. Source availability is limited to this SDK input; host verification is unknown.",
    coverage: { omittedItems, completeProjectDescription: false, scope: "bounded_current_execution" },
    ...(context ? { referenceContext: context } : {}),
    items,
  }) + "\n" + DERIVED_END;
}
