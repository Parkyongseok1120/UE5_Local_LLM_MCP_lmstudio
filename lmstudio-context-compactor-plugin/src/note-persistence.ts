import type { ChatMessage } from "@lmstudio/sdk";
import { modelNotes, workingContextModule } from "./context-ports";
import type { ContinuityNote } from "./execution-contracts";

type NoteStore = InstanceType<typeof modelNotes.ContinuityNoteStore>;
type WorkingContextStore = InstanceType<typeof workingContextModule.WorkingContext> | null;
type NoteScope = { objectiveFingerprint: string; projectDescriptor: string };

export type NotePersistenceResult = {
  status: "stored" | "not_stored" | "skipped";
  reason?: "unstable_history_key" | "scope_reconciliation_failed" | "atomic_write_failed";
  itemCount: number;
  reviewClaimCount: number;
};

/** Sole owner of where a continuity note is read from and written to. The
 * per-history note store and the durable working window both carry a note;
 * this class decides precedence on restore and keeps them consistent on
 * every change and at termination (including canceled executions). */
export class NotePersistence {
  constructor(
    private readonly store: NoteStore,
    private readonly workingContext: WorkingContextStore,
    private readonly workingDirectory: string,
    private readonly scope: NoteScope,
    private readonly observe?: (event: Record<string, unknown>) => void,
  ) { }

  /** The note keyed by the history before the new user message wins; the
   * window's note is the fallback. A newly attached file may change what a
   * review claim means, so review claims never cross that boundary. */
  restore(messages: ChatMessage[], discardReviewClaims: boolean): ContinuityNote | null {
    const scope = { ...this.scope, discardReviewClaims };
    const historyKey = messages.at(-1)?.isUserMessage()
      ? modelNotes.historyKey(messages.slice(0, -1), this.workingDirectory) : "";
    const stored = historyKey ? this.store.read(historyKey) : null;
    let note = stored
      ? modelNotes.reconcileStoredNote(stored, messages, this.workingContext?.verifiedNoteRefs(stored), scope) : null;
    let source = note ? "history" : "none";
    const windowNote = this.workingContext?.note as ContinuityNote | null | undefined;
    if (!note && windowNote) {
      note = modelNotes.reconcileStoredNote(windowNote, messages, this.workingContext!.verifiedNoteRefs(windowNote), scope);
      if(note)source="window";
    }
    if(stored?.schemaVersion===2 || windowNote?.schemaVersion===2)this.observe?.({event:"architecture_restore",source,
      restoredCount:note?.architecture?.length || 0,historicalCount:note?.architecture?.length || 0,
      rejectedCount:note?0:1,rejectionReasons:note?[]:["scope_or_reference_reconciliation"],authority:"historical_on_activation"});
    this.mirror(note);
    return note;
  }

  /** Keep the durable window's copy identical to the active note. */
  mirror(note: ContinuityNote | null) {
    if (this.workingContext) this.workingContext.note = note;
  }

  /** Write the note under the key the next turn will compute from the host's
   * stored transcript. Called once at termination on every exit path. */
  persist(note: ContinuityNote | null, visibleMessages: ChatMessage[]): NotePersistenceResult {
    if (!note || !this.workingDirectory) return { status: "skipped", itemCount: 0, reviewClaimCount: 0 };
    const nextHistoryKey = modelNotes.historyKey(visibleMessages, this.workingDirectory);
    const verified = modelNotes.reconcileStoredNote(note, visibleMessages, this.workingContext?.verifiedNoteRefs(note), this.scope);
    const stored = Boolean(nextHistoryKey && verified && this.store.write(nextHistoryKey, verified));
    if(note.schemaVersion===2)this.observe?.({event:"architecture_persistence",noteVersion:2,stateItemCount:note.architecture?.length || 0,
      status:stored?"stored":"not_stored",reason:stored?"accepted":!nextHistoryKey?"unstable_history_key":!verified?"scope_reconciliation_failed":"atomic_write_failed"});
    return {
      status: stored ? "stored" : "not_stored",
      ...(stored ? {} : { reason: !nextHistoryKey ? "unstable_history_key"
        : !verified ? "scope_reconciliation_failed" : "atomic_write_failed" as const }),
      itemCount: verified ? verified.decisions.length + verified.rejectedHypotheses.length + verified.openQuestions.length : 0,
      reviewClaimCount: verified?.reviewClaims?.length || 0,
    };
  }
}
