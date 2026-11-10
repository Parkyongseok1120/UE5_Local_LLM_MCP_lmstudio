/** The read-result reservation denials: one table for the reason codes, what
 * the model is told, and how the denied call is presented (revised plan
 * 2026-11-07 §A). Every code is a host-side outcome: recovery never counts a
 * budget denial as model stagnation, whatever its disposition. */
export type ReadBudgetDenialCode = "held_reservations_insufficient" | "remaining_capacity_insufficient"
  | "returned_result_overrun" | "batch_closed" | "duplicate_reservation" | "invalid_reservation"
  | "minimum_exceeds_published_limit";

export type ReadBudgetAllowance = { availableTokens: number; minimumTokens: number; capacityTokens: number };

/** retry_next_round: this batch's room ran out; the same read can come back.
 * retry_after_use: the batch needs remeasuring or the host's own accounting
 * failed; use what returned, then ask again only if still needed.
 * narrow_request: the request cannot fit the tool's published limit at all. */
type Disposition = "retry_next_round" | "retry_after_use" | "narrow_request";

const KEPT = "Results already returned in this round are kept.";
const NOT_EXHAUSTED = "This is this batch's reservation room, not the task's read budget.";
const room = (allowance?: ReadBudgetAllowance) => allowance
  ? ` About ${allowance.availableTokens} of ${allowance.capacityTokens} estimated tokens remain; this read needs at least ${allowance.minimumTokens}.` : "";
const HOST_STATE = (what: string) => () =>
  `${what} This is host batch state, not a problem with the read's size. ${KEPT} Request the read again in a later round if it is still needed.`;

export const READ_BUDGET_DENIALS: Readonly<Record<ReadBudgetDenialCode, {
  disposition: Disposition; advice: (allowance?: ReadBudgetAllowance) => string;
}>> = {
  held_reservations_insufficient: { disposition: "retry_next_round", advice: allowance =>
    `Parallel reads in this batch hold the allowance.${room(allowance)} ${KEPT} ${NOT_EXHAUSTED} Request this read again by itself in the next round.` },
  remaining_capacity_insufficient: { disposition: "retry_next_round", advice: allowance =>
    `The model input has no room for another result in this batch.${room(allowance)} ${KEPT} ${NOT_EXHAUSTED} `
    + "The next round's input is measured again, and compacted when it needs to be; use the returned results, then request this read alone in a later round." },
  returned_result_overrun: { disposition: "retry_after_use", advice: () =>
    `A returned result exceeded its reservation, so the input is remeasured before more reads. ${KEPT} Use them first.` },
  batch_closed: { disposition: "retry_after_use", advice: HOST_STATE("This round's read batch was already closed.") },
  duplicate_reservation: { disposition: "retry_after_use", advice: HOST_STATE("This call was already reserved in this batch.") },
  invalid_reservation: { disposition: "retry_after_use", advice: HOST_STATE("The host could not account a reservation for this call.") },
  minimum_exceeds_published_limit: { disposition: "narrow_request", advice: () =>
    "The smallest response this read needs is larger than the tool's published limit. If the source body is what is large, "
    + "request a narrower line range; if the response metadata alone exceeds the limit, a narrower range of the same tool will not help, "
    + `so report the item as unread. ${KEPT}` },
};

/** What a denied read tells the model. The numbers are byte-derived
 * allowances, not exact model tokens. */
export function readBudgetDenialMessage(code: ReadBudgetDenialCode, allowance?: ReadBudgetAllowance) {
  const entry = READ_BUDGET_DENIALS[code] ?? READ_BUDGET_DENIALS.invalid_reservation;
  return `Shared read-result budget unavailable (${code}). ${entry.advice(allowance)}`;
}

/** The call is deferred to a later round rather than refused. */
export const deferredReadDenial = (code: ReadBudgetDenialCode | undefined) =>
  code !== undefined && READ_BUDGET_DENIALS[code]?.disposition === "retry_next_round";
