import type { ReviewRegister, ReviewRow } from "./review-register";
import { acceptReviewSupplement, type ReviewAcceptance } from "./review-acceptance";
import type { ReviewGate } from "./prediction-stream";

export const REPORT_REVIEW_STATUS = "검토 보완 작성 중 · 앞선 보고서와 겹치는지 확인한 뒤 표시합니다";

/** One execution's report review: whether it ran or was blocked, what it was
 * asked to address, what its held output was allowed to publish, and what it
 * left open. The prediction loop decides when; this owns what was decided. */
export class ReportReviewState {
  attempted = false;
  blocked = false;
  /** The review round ran and published no supplement. */
  empty = false;
  duplicateOnly = false;
  missing: ReviewRow[] = [];
  subjects: string[] = [];
  acceptance?: ReviewAcceptance;

  /** A review is held until it ends and published only as accepted: a
   * rewrite of the delivered report is not shown a second time. */
  gate(report: string): ReviewGate {
    const subjects = this.subjects;
    return (candidate, complete) => acceptReviewSupplement({ candidate, report, subjects, complete });
  }

  /** Only a review that ended on its own is judged; a cut or stopped one publishes nothing. */
  static ended(captured: { failure?: unknown; finishReason?: string }, aborted: boolean): boolean {
    return captured.failure === undefined && !aborted
      && (captured.finishReason === "eosFound" || captured.finishReason === "stopStringFound");
  }

  /** Records the decision once both delivery paths have made it; the debug payload, if any. */
  decided(acceptance: ReviewAcceptance | undefined, finishReason?: string): Record<string, unknown> | null {
    this.acceptance = acceptance;
    return acceptance ? { decision: acceptance.decision, reason: acceptance.reason, generatedChars: acceptance.generatedChars,
      deliveredChars: acceptance.text.length, salvagedBlocks: acceptance.salvagedBlocks, signals: acceptance.signals,
      finishReason: finishReason || "unknown" } : null;
  }

  /** The review round's published result. A stopped round is never duplicate-only. */
  observeFinal(rawIntentFinal: boolean, visibleText: string,
    shown: { generatedVisibleChars: number; duplicateSuppressedChars: number }, stopped: boolean) {
    if (rawIntentFinal || !visibleText.trim()) this.empty = true;
    if (this.empty && !rawIntentFinal && !stopped)
      this.duplicateOnly = shown.generatedVisibleChars > 0 && shown.duplicateSuppressedChars >= shown.generatedVisibleChars;
  }

  /** Why nothing was published although the review generated text. */
  get withheld(): "restatement" | "incomplete" | undefined {
    const decision = this.acceptance?.decision;
    return decision === "rejected" ? "restatement" : decision === "withheld" ? "incomplete" : undefined;
  }

  /** Terminal notice options. What the review left open is judged on the
   * report plus what was published after it; a held-back rewrite resolves
   * nothing. Obligations are recorded apart from delivery. */
  terminal(register: ReviewRegister, answer: string, record: (requested: string[], unresolved: string[]) => void) {
    let cause: "restatement" | "incomplete" | "unresolved" | undefined;
    if (this.attempted && !this.blocked) {
      cause = this.withheld;
      if (!cause && !this.empty) { this.missing = register.missing(answer); cause = "unresolved"; }
      record(this.subjects, this.missing.map(row => row.identifier));
    }
    return { reviewedReport: this.attempted && !this.blocked && !this.empty, reportReviewBlocked: this.blocked,
      reportReviewEmpty: this.empty, reportReviewDuplicateOnly: this.duplicateOnly, reviewMissing: this.missing,
      reportReviewCause: cause };
  }
}
