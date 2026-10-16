import type { Chat } from "@lmstudio/sdk";
import type { workingContextModule } from "./context-ports";

type WorkingContextStore = InstanceType<typeof workingContextModule.WorkingContext>;

/** Terminal hand-off of the working window: seal the returned evidence and
 * approved planning for the next execution, then release the archive lease
 * exactly once. A seal failure wins over a close failure; a close failure
 * after a successful seal is reported to the caller. No model work happens. */
export function sealAndRelease(workingContext: WorkingContextStore, visible: Chat, workingHistory: Chat,
  cleanupFailures: string[]) {
  let sealed = false;
  let released = false;
  let sealError: unknown;
  try { sealed = workingContext.seal(visible, workingHistory); }
  catch (error) { sealError = error; }
  try { released = workingContext.close(); }
  catch (error) {
    if (sealError === undefined) throw error;
    cleanupFailures.push(String(error));
  }
  if (sealError !== undefined) throw sealError;
  return { sealed, released, reason: sealed ? "terminal_remeasure_required" : workingContext.lastCommitReason };
}
