import { ENGINE_PROFILES, ENGINE_PROVIDERS } from "./engine-profiles.js";
import {
  Chat,
  ChatMessage,
  type ToolCallRequest
} from "@lmstudio/sdk";
import { inputAvailability, readLedgerModule, toolMemory, workingContextModule, type ArchiveLocator, type LedgerEntry,
  type ReadFacts, type ReadLedgerLike } from "./context-ports";
import { repeatRequestKey, telemetryFingerprint } from "./evidence-telemetry";
export { repeatRequestKey };
import { capabilityFor, isObservationOnlyToolCall, localObservationTools, ToolCapabilityRegistry, type RemoteToolLike } from "./tool-capability-registry";
import type { RoundVerdict } from "./execution-contracts";
import { auditChecksFromPacket, emptyReferenceState, ingestReferenceObservations, referenceSnapshot, seedPriorEngineIdentity } from "./reference-context";
import type { ToolScope } from "./tool-scope";
import { resultSemantics, decodeEnvelope } from "./tool-result-semantics.js";
import { retainInterruptedCalls, renderInterruptedCalls, resolveInterruptedCalls } from "./interrupted-calls.js";
import { extractPriorContinuityState } from "./continuity-memory.js";
const identity = require("./evidence-identity.js") as {
  sourceIdentity(value: Record<string, unknown>): string;
  sourceVersion(value: Record<string, unknown>): string;
  queryKey(value: Record<string, unknown>): string;
  normalizeObservation(value: Record<string, unknown>, request: ToolCallRequest, provider: string): Record<string, unknown>;
};
const { changePairKey } = require("./change-evidence-memory.js") as {
  changePairKey(request: ToolCallRequest, content: unknown): string;
};

export type EvidenceIdentity = { provider: string; sourceKind: string; sourceIdentity: string; query: string };
export type EvidenceVersion = { value: string; verified: boolean };
export type EvidenceRepresentation = "source" | "sanitized_body" | "sanitized_archived_tool_envelope";
export type CoverageRange = {
start: number; end: number; boundary: "half_open" | "inclusive";
  unit: "line" | "page" | "utf8_byte" | "utf16_code_units" | "observation"; representation: EvidenceRepresentation
};
export type ArchiveRef = { evidenceId: string; version: string };
export type SourceContinuation = { hasMore: boolean | "unknown"; cursor?: string; durable: false };
export type ContentView = {
identity: EvidenceIdentity; version: EvidenceVersion; representation: EvidenceRepresentation;
  body: string; archive: ArchiveRef | null; continuation: SourceContinuation
};
export type CurrentAvailability = {
modelInputId: string; included: boolean; predictionCompleted: boolean;
  hostVerified: false; ranges: CoverageRange[]
};

/** Owns observation lifecycle and progress; storage remains in WorkingContext.
 * No source adapter can promote a projected/archive view to a fresh read. */
export class EvidenceManager {
  fingerprints = new Set<string>();
  coverage: RecoveryCoverageLedger = new Map();
  private currentCoverage: RecoveryCoverageLedger = new Map();
  private readonly referenceState = emptyReferenceState();
  // Bounded execution-local provenance at this existing observation owner.
  // Historical raw results never gain a provider from today's tool registry.
  private readonly changeOrigins = new Map<string, Record<string, string> | null>();
  private changesDisabled = false;
  initializeChangeOrigins(history: Chat) {
    for (const message of history.getMessagesArray()) {
      for (const result of message.getToolCallResults()) {
        if (this.changeOrigins.size >= 2048) { this.changeOrigins.clear(); this.changesDisabled = true; return; }
        this.changeOrigins.set(`historical:${telemetryFingerprint(result.content)}`, null);
      }
    }
  }
  changeOrigin = (request: ToolCallRequest, content: unknown) => this.changesDisabled ? undefined
    : this.changeOrigins.get(changePairKey(request, content)) || undefined;
  constructor(readonly store: InstanceType<typeof workingContextModule.WorkingContext> | null,
    readonly tools: Array<RemoteToolLike>) { }
  private sourceExecutionId = "";
  recordReturnedSources(messages: ChatMessage[], executionId: string) {
    this.sourceExecutionId = executionId;
    const counts = new Map<string,number>();
    for(const message of messages) for(const request of message.getToolCallRequests())
      counts.set(String(request.id),1+(counts.get(String(request.id))||0));
    for(const record of observationRecords(messages,this.tools,{confirmedPairsOnly:true})) {
      if(!record.trustedRead || !record.tool || !record.request || !record.value || counts.get(String(record.request.id))!==1)continue;
      const capability=capabilityFor(record.tool,record.request),kind=capability?.resultKind;
      if(kind) this.ledger.recordReturnedSource({...record.value,
        sourceMode:record.value.sourceMode || record.request.arguments?.sourceMode,
        canonicalProject:record.value.canonicalProject || record.request.arguments?.project},kind,executionId,{contractLookup:capability.roles?.includes("contract_lookup")});
    }
  }
  returnedSource(sourceKey:string,span:[number,number]) { return this.ledger.returnedSource(sourceKey,span,this.sourceExecutionId); }
  returnedSources() { return this.ledger.returnedSources(this.sourceExecutionId); }
  rebuildReturnedSources(history: Chat) {
    if(this.ledger.returnedSources().files.length || this.ledger.returnedSources().contractIds.size)return;
    for(const record of observationRecords(history.getMessagesArray(),this.tools,{confirmedPairsOnly:true})) {
      if(!record.trustedRead || !record.tool || !record.request || !record.value)continue;
      const capability=capabilityFor(record.tool,record.request),kind=capability?.resultKind;
      // Old host transcripts prove delivery only. They cannot inherit today's
      // provider identity, archive access, receipts or version coverage.
      if(kind && kind!=="archive_page")this.ledger.recordReturnedSource({...record.value,provider:"historical_unverified",
        sourceMode:record.value.sourceMode || record.request.arguments?.sourceMode,
        canonicalProject:record.value.canonicalProject || record.request.arguments?.project},kind,"",{contractLookup:capability.roles?.includes("contract_lookup")});
    }
  }
  ingestReferences(messages: readonly ChatMessage[], scope: ToolScope, executionId: string) {
    ingestReferenceObservations(this.referenceState, messages, this.tools, scope, executionId);
    if (this.store) this.store.referenceObservations = JSON.parse(JSON.stringify(this.referenceState.observations));
  }
  restoreReferences() {
    const saved = this.store?.referenceObservations;
    if (!Array.isArray(saved) || saved.length > 16) return;
    const observations = saved.filter(o => o && typeof o.id === "string" && typeof o.fingerprint === "string"
      && Number.isSafeInteger(o.batch) && Array.isArray(o.items) && o.items.length <= 16
      && o.items.every((i: any) => i && typeof i.project === "string" && i.origin?.executionId && i.origin?.toolCallId
        && ENGINE_PROVIDERS.includes(i.origin?.provider)));
    this.referenceState.observations = observations.map(o => ({ ...o, historical: true }));
    this.referenceState.batch = Math.max(0, ...observations.map(o => o.batch));
    this.referenceState.seen = new Set(observations.map(o => o.id));
  }
  /** A rejected window does not erase source observations present in the host
   * transcript. Rebuild historical hints only: no inherited refs, receipts,
   * current provider authority, visibility or mutation permission. */
  rebuildHistoricalReads(history: Chat): number {
    let restored = 0;
    const pairs = workingContextModule.exchangeIndex(history);
    if (pairs.ambiguous) return 0;
    for (const [mi, message] of history.getMessagesArray().entries()) {
      for (const [ri, result] of message.getToolCallResults().entries()) {
        const request = pairs.matches.get(`${mi}:${ri}`);
        const value = toolMemory.decodeToolResultRecord(result.content).value;
        if (!request || !readLedgerModule.SOURCE_READ_TOOLS.has(request.name) || !value) continue;
        const facts = readLedgerModule.verifiedReadFacts(identity.normalizeObservation(value, request, "historical_unverified"));
        if (!facts) continue;
        facts.sourceKey = `historical:${facts.sourceKey}`;
        this.ledger.record(facts, { turn: "earlier", toolName: request.name });
        restored++;
      }
    }
    return restored;
  }
  /** Only when no persisted window restored reference observations. */
  seedPriorEngineIdentity(history: Chat, scope: ToolScope, executionId: string) {
    seedPriorEngineIdentity(this.referenceState, history.getMessagesArray(), this.tools, scope, executionId);
  }
  referenceSnapshot(history: Chat) { return referenceSnapshot(this.referenceState, history); }
  observeRoundReferences(history: Chat, returned: readonly ChatMessage[], scope: ToolScope, executionId: string,
    collectReferences = false) {
    const messages = history.getMessagesArray();
    const prior = extractPriorContinuityState(messages.map(m => ({ role: m.getRole(), text: m.getText() })));
    const current = retainInterruptedCalls(messages, prior?.interruptedCalls || []);
    if (!collectReferences && !current.some((item: any) => !item.resolution)) return;
    // One ingestion per round. Settlement also works with optional guidance
    // disabled, using this owner's existing provider/pairing validation.
    this.ingestReferences(returned, scope, executionId);
    if (!current.length) return;
    const resolved = resolveInterruptedCalls(current, this.referenceSnapshot(history).items);
    if (JSON.stringify(current) !== JSON.stringify(resolved)) history.append("assistant", renderInterruptedCalls(resolved, prior?.interruptedCallRetention));
  }
  private readonly observedRounds = new Map<string, ReadDelta>();
  seed(history: Chat) {
    const state = seedRecoveryProgress(history, this.tools, this.resolveProjection);
    for (const fp of state.fingerprints) this.fingerprints.add(fp);
    for (const [key, ranges] of state.coverage)
      this.coverage.set(key, normalizedCoverageRanges([...(this.coverage.get(key) || []), ...ranges]));
    this.boundProgress();
  }
  private boundProgress() {
    for (const collection of [this.fingerprints, this.coverage, this.currentCoverage])
      while (collection.size > 2048) collection.delete(collection.keys().next().value!);
  }
  observe(messages: Array<ChatMessage>, roundId?: string, screening?: ReadScreening, budgetDenied: readonly string[] = []): ReadDelta {
    if (roundId && this.observedRounds.has(roundId)) return this.observedRounds.get(roundId)!;
    const observed = observeRecoveryProgress(messages, this.fingerprints, this.coverage, this.currentCoverage, this.tools,
      this.isArchiveRepage);
    const repeatRestoreOnly = Boolean(screening && screening.otherResults === 0 && screening.repeatRestores > 0
      && screening.repeatRestores + screening.redundant >= screening.readResults - screening.failedReads);
    const noNewEvidence = Boolean(screening && screening.readResults > 0 && screening.failedReads === 0
      && screening.newDeliveries === 0 && screening.focusedReads === 0 && screening.otherResults === 0
      && (observed.restoredWorkingEvidenceUnits === 0 || repeatRestoreOnly));
    const envelopes = observationRecords(messages, this.tools, { confirmedPairsOnly: true })
      .filter(record => record.trustedRead && record.request && record.tool)
      .map(record => ({ tool: record.tool!, request: record.request!, envelope: decodeEnvelope(record.content) }));
    const verdict: RoundVerdict = Object.freeze({ progressed: observed.usefulProgress && !repeatRestoreOnly,
      noNewEvidence, failedReads: screening?.failedReads ?? envelopes.filter(r => r.envelope.errorCode).length, budgetDenied: Object.freeze([...budgetDenied]),
      mutationOutcomes: new ToolCapabilityRegistry(this.tools).mutationOutcomes(messages) });
    const progress: ReadDelta = { ...observed, repeatRestoreOnly, noNewEvidence, verdict, envelopes,
      usableSourceEvidence: Boolean(screening && screening.newDeliveries + screening.focusedReads > 0),
      progressed: observed.progressed && !repeatRestoreOnly, usefulProgress: observed.usefulProgress && !repeatRestoreOnly };
    Object.freeze(progress);
    this.boundProgress();
    if (roundId) {
      this.observedRounds.set(roundId, progress);
      while (this.observedRounds.size > 8) this.observedRounds.delete(this.observedRounds.keys().next().value!);
    }
    return progress;
  }
  project(history: Chat, metadata: Record<string, unknown>, maxChars: number) {
    return this.store?.project(history, request => {
      const matches = this.tools.filter(tool => tool.name === request.name);
      return matches.length === 1 && isObservationOnlyToolCall(matches[0], request);
    }, { ...metadata, toolProviders: Object.fromEntries(this.tools.map(t => [t.name, t.pluginIdentifier || "local"])) }, maxChars)
      || { history, changed: false, reason: "disabled" };
  }
  captureExposure(history: Chat, modelInputId: string, completed = false) {
    this.currentCoverage = seedRecoveryProgress(history, this.tools, this.resolveProjection).coverage;
    this.updateReadVisibility(history);
    this.store?.captureExposure(history, modelInputId, completed);
  }
  captureReturned(messages: Array<ChatMessage>, executionId: string, batchId = "") {
    const history = Chat.empty(); for (const message of messages) history.append(message);
    const pairs = workingContextModule.exchangeIndex(history);
    if (!pairs.ambiguous && batchId && !this.changesDisabled) for (const [mi, message] of messages.entries()) {
      for (const [ri, result] of message.getToolCallResults().entries()) {
        const request = pairs.matches.get(`${mi}:${ri}`);
        const tools = this.tools.filter(t => t.name === request?.name);
        if (!request?.id || tools.length !== 1 || !(tools[0].pluginIdentifier === ENGINE_PROFILES.unreal.fileProvider
          && ENGINE_PROFILES.unreal.changeEvidenceTools.includes(request.name)
          || tools[0].pluginIdentifier === ENGINE_PROFILES.unity.fileProvider
          && ENGINE_PROFILES.unity.changeEvidenceTools.includes(request.name))) continue;
        if (this.changeOrigins.size >= 2048) { this.changeOrigins.clear(); this.changesDisabled = true; break; }
        const key = changePairKey(request, result.content);
        this.changeOrigins.set(key, this.changeOrigins.has(key)
          || this.changeOrigins.has(`historical:${telemetryFingerprint(result.content)}`) ? null : { provider: tools[0].pluginIdentifier,
          tool: request.name, executionId, batchId, toolCallId: request.id, observedAt: new Date().toISOString() });
      }
    }
    return this.store?.captureReturned(history, request => {
      const matches = this.tools.filter(tool => tool.name === request.name);
      return matches.length === 1 && isObservationOnlyToolCall(matches[0], request);
    }, { executionId, proofLevel: "returned_tool_result",
      toolProviders: Object.fromEntries(this.tools.map(t => [t.name, t.pluginIdentifier || "local"])) }) || 0;
  }
  private readonly localLedger = new readLedgerModule.ReadLedger();
  /** The per-file read ledger: the working window's (persisted with it), or
   * this execution's own when no working context exists. */
  get ledger(): ReadLedgerLike { return this.store?.readLedger || this.localLedger; }
  // Execution-local: archive pages this execution saw re-deliver a version
  // the ledger already held, and hidden read ranges it already restored.
  private readonly repagedPages = new Set<string>();
  // Restored spans per file version and unit: a restore inside them is a
  // repeat, whichever offsets the model chose (2026-10-25 run: 52 restore
  // rounds, each a new byte or line window, never counted as re-reading).
  private readonly restoredSpans = new Map<string, Array<[number, number]>>();
  private readonly wholeRestores = new Set<string>();
  // The delivered path each receipt this execution may send back stands for.
  private readonly receiptPaths = new Map<string, ReadFacts>();
  private readonly handleCache = new Map<string, string | null>();
  private visibleReads = new Map<string, { line: Array<[number, number]>; utf8_byte: Array<[number, number]> }>();

  private updateReadVisibility(history: Chat) {
    const observations = this.rawObservations(history);
    this.visibleReads = readLedgerModule.visibleRanges(observations);
    for (const result of observationRecords(history.getMessagesArray(), this.tools, { confirmedPairsOnly: true })) {
      const page = result.trustedRead ? readLedgerModule.archivePageFacts(result.value) : null;
      if (page?.bodyVerified) this.rememberVisibleRead(page);
      const source = result.trustedRead ? readLedgerModule.verifiedReadFacts(result.value) : null;
      if (source) this.rememberVisibleRead(source);
    }
  }

  private rawObservations(history: Chat) {
    const records = observationRecords(history.getMessagesArray(), this.tools, { confirmedPairsOnly: true });
    return inputAvailability.currentRawObservations([{ toolResults: records.filter(r => r.trustedRead && r.value)
      .map(r => ({ content: JSON.stringify(r.value) })) }] as unknown as Parameters<typeof inputAvailability.currentRawObservations>[0]);
  }

  private resolveProjection = (value: Record<string, unknown>, request: ToolCallRequest) =>
    this.store?.resolveProjection?.(value, request) || null;

  private visibleRead(facts: ReadFacts) {
    const shown = this.visibleReads.get(`${facts.sourceKey || facts.key}\u0000${facts.sourceVersion}`);
    const covered = (ranges: Array<[number, number]> | undefined, target: [number, number] | null) =>
      Boolean(target && ranges?.some(([s, e]) => s <= target[0] && e >= target[1]));
    return covered(shown?.line, facts.lines) || covered(shown?.utf8_byte, facts.bytes);
  }

  private rememberVisibleRead(facts: ReadFacts) {
    const id = `${facts.sourceKey || facts.key}\u0000${facts.sourceVersion}`;
    const shown = this.visibleReads.get(id) || { line: [], utf8_byte: [] };
    if (facts.lines) shown.line = inputAvailability.normalizeRanges([...shown.line, facts.lines]) as Array<[number, number]>;
    if (facts.bytes) shown.utf8_byte = inputAvailability.normalizeRanges([...shown.utf8_byte, facts.bytes]) as Array<[number, number]>;
    this.visibleReads.set(id, shown);
    while (this.visibleReads.size > 64) this.visibleReads.delete(this.visibleReads.keys().next().value!);
  }

  /** Before returned results enter the working history: a same-hash read of
   * an already delivered range becomes an UNCHANGED fact with the archive
   * locator of that version while that range is still visible in the input,
   * unless it is a narrower read or no readable record of the version exists.
   * A hidden range or a different hash always keeps its body. */
  screenReturnedReads(messages: Array<ChatMessage>, roundIndex: number): ReadScreening {
    const screening: ReadScreening = { messages: [...messages], replaced: 0, redundant: 0, duplicatedChars: 0, repages: 0, focusedReads: 0,
      repeatRestores: 0, newDeliveries: 0, readResults: 0, failedReads: 0, otherResults: 0, pending: [], suppressedCallIds: [] };
    const history = Chat.empty(); for (const message of messages) history.append(message);
    const pairs = workingContextModule.exchangeIndex(history);
    if (pairs.ambiguous) return screening;
    const round = roundIndex + 1;
    messages.forEach((message, mi) => {
      const results = message.getToolCallResults();
      if (!results.length) return;
      let changed = false;
      const copies = results.map((result, ri) => {
        const request = pairs.matches.get(`${mi}:${ri}`);
        const tools = this.tools.filter(tool => tool.name === request?.name);
        if (!request || tools.length !== 1 || !isObservationOnlyToolCall(tools[0], request)) { screening.otherResults++; return result; }
        const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
        if (request.name === "evidence_first_read_context") {
          const page = readLedgerModule.archivePageFacts(value);
          if (value?.ok === false || value?.errorCode) { screening.readResults++; screening.failedReads++; }
          else if (!page) screening.otherResults++;
          else {
            screening.readResults++;
            const repage = this.ledger.notePage(page, round);
            if (repage) { screening.repages++; this.repagedPages.add(archivePageKey(value!)); }
            if (page.bodyVerified && this.visibleRead(page)) {
              const entry = this.ledger.get(page.path, page.sourceVersion, page.sourceKey);
              if (entry) {
                changed = true; screening.replaced++; screening.redundant++;
                screening.suppressedCallIds.push(String(result.toolCallId));
                return { ...result, content: JSON.stringify(this.unchangedFact(entry, page, value!, null, true)) };
              }
            }
            const returned = value?.returnedRange;
            const span = Array.isArray(returned) && Number.isSafeInteger(returned[0]) && returned[1] > returned[0]
              ? [returned[0], returned[1] - 1] as [number, number] : null;
            const totalChars = Number(value?.totalChars);
            if (this.noteRestore(`${page.sourceKey || page.key}\u0000${page.sourceVersion}`, `archive:${String(value?.evidenceId || "")}`, span,
              Number.isSafeInteger(totalChars) && totalChars > 0 ? [0, totalChars - 1] : null))
              screening.repeatRestores++;
            else if (!repage) screening.newDeliveries++;
            if (page.bodyVerified) this.rememberVisibleRead(page);
          }
          return result;
        }
        if (!readLedgerModule.SOURCE_READ_TOOLS.has(request.name)) { screening.otherResults++; return result; }
        screening.readResults++;
        if (!value || sourceObservationFailed(value)) { screening.failedReads++; return result; }
        const content = this.screenSourceRead(request, String(result.toolCallId),
          identity.normalizeObservation(value, request, tools[0].pluginIdentifier || "local"), round, screening);
        if (content === null) return result;
        changed = true;
        if (screening.suppressedCallIds.includes(String(result.toolCallId))) screening.replaced++;
        return { ...result, content };
      });
      if (changed) screening.messages[mi] = ChatMessage.from({ role: "tool",
        content: copies.map(result => ({ type: "toolCallResult", ...result })) } as Parameters<typeof ChatMessage.from>[0]);
    });
    return screening;
  }

  private screenSourceRead(request: ToolCallRequest, toolCallId: string, value: Record<string, any>, round: number,
    screening: ReadScreening): string | null {
    const ledger = this.ledger;
    // The server's own repeat cache answered: the identical read against unchanged state.
    if (value.duplicate === true && value.status === "no_new_information") {
      const receipt = request.arguments?.repeatReceipt;
      const facts = readLedgerModule.readFacts(value) || (typeof receipt === "string" ? this.receiptPaths.get(receipt) : undefined);
      const entry = facts ? ledger.get(facts.path, facts.sourceVersion, facts.sourceKey) : null;
      if (!entry) return null;
      const bodyVisible = this.visibleRead(facts!);
      // The receipt stands for a body this input no longer shows: answer the
      // read with the archived body of that version, so restoring it does not
      // take a second round through the archive (2026-10-25 run, r32 and r34).
      const archived = bodyVisible ? null : this.archivedReadFor(entry, request.arguments || {});
      if (archived) return this.screenSourceRead(request, toolCallId, archived, round, screening) ?? JSON.stringify(archived);
      ledger.noteRedundant(entry, round, 0, { source: "server_repeat_receipt" });
      screening.redundant++; screening.suppressedCallIds.push(toolCallId);
      return JSON.stringify(this.unchangedFact(entry, facts!, value, this.store?.archiveLocator(entry.archiveRef, true) || null, bodyVisible));
    }
    const facts = readLedgerModule.verifiedReadFacts(value);
    if (!facts) { screening.otherResults++; screening.readResults--; return null; }
    const verdict = ledger.classify(facts);
    const pending = (content: string | null) => {
      screening.pending.push({ request, toolCallId, content, facts, repeatReceipt: typeof value.repeatReceipt === "string" ? value.repeatReceipt : "" });
      return content;
    };
    // A repeat is suppressed only while its body is still visible in this
    // input; once projection or compaction removed it, the read is how the
    // model restores it, whole file or focused range alike.
    const restoringHidden = verdict.status === "redundant" && !this.visibleRead(facts);
    if (verdict.status === "redundant" && verdict.entry && !restoringHidden) {
      const locator = this.store?.archiveLocator(verdict.entry.archiveRef, true, facts) || null;
      ledger.noteRedundant(verdict.entry, round, facts.chars, locator ? {} : { bodyKept: "no_archive_locator" });
      screening.redundant++; screening.duplicatedChars += facts.chars;
      if (locator) {
        screening.suppressedCallIds.push(toolCallId);
        return JSON.stringify(this.unchangedFact(verdict.entry, facts, value, locator));
      }
      // No readable record of this version is left: this body becomes its locator.
      ledger.record(facts, { round, toolName: request.name });
      this.rememberVisibleRead(facts);
      return pending(null);
    }
    const prior = verdict.entry;
    ledger.record(facts, { round, toolName: request.name });
    this.rememberVisibleRead(facts);
    // Restoring a hidden body is progress once per version and span: restoring
    // what was already restored means it left the input again, a loop the
    // stagnation guard must still see.
    const whole: [number, number] | null = facts.lines ? (facts.totalLines ? [1, facts.totalLines] : null)
      : facts.size ? [0, facts.size - 1] : null;
    const repeat = restoringHidden && this.noteRestore(`${facts.sourceKey || facts.key}\u0000${facts.sourceVersion}`,
      facts.lines ? "line" : "byte", facts.lines || facts.bytes, whole);
    if (repeat) screening.repeatRestores++;
    else if (verdict.status === "narrower" || restoringHidden) screening.focusedReads++;
    else screening.newDeliveries++;
    if (!prior || verdict.status === "new_version") return pending(null);
    // Same hash as an earlier delivery: say so, so "modified" can only come from a hash change.
    return pending(JSON.stringify({ ...value, unchangedSince: unchangedSince(prior),
      unchangedNotice: `Same sha ${prior.sourceVersion.slice(0, 8)} as the version delivered in ${unchangedSince(prior)}: the file is unchanged.` }));
  }

  /** The archived read of entry's version that answers these arguments: the
   * whole file, or the same starting window. Another window is not it. */
  private archivedReadFor(entry: LedgerEntry, args: Record<string, unknown>): Record<string, any> | null {
    const value = this.store?.archivedValue?.(entry.archiveRef) as Record<string, any> | null | undefined;
    const facts = value ? readLedgerModule.readFacts({ ...value, sourceKey: entry.sourceKey }) : null;
    if (!value || !facts || facts.key !== entry.key || facts.sourceVersion !== entry.sourceVersion) return null;
    if (facts.complete) return { ...value, sourceKey: entry.sourceKey };
    const sameStart = args.startLine !== undefined ? facts.lines?.[0] === Number(args.startLine)
      && (args.endLine === undefined || facts.lines[1] >= Number(args.endLine))
      : facts.bytes?.[0] === Number(args.offsetBytes ?? 0);
    return sameStart ? { ...value, sourceKey: entry.sourceKey } : null;
  }

  /** Records a restored span of one file version in one unit; true when
   * earlier restores already covered it, or already restored the whole
   * version in any unit (archive pages and source re-reads alternate). */
  private noteRestore(version: string, unit: string, span: [number, number] | null, whole: [number, number] | null): boolean {
    if (!span) return false;
    const key = `${version}\u0000${unit}`, spans = this.restoredSpans.get(key) || [];
    const repeat = this.wholeRestores.has(version) || spans.some(([start, end]) => start <= span[0] && end >= span[1]);
    const merged = inputAvailability.normalizeRanges([...spans, span]) as Array<[number, number]>;
    this.restoredSpans.set(key, merged);
    if (whole && merged.some(([start, end]) => start <= whole[0] && end >= whole[1])) this.wholeRestores.add(version);
    while (this.restoredSpans.size > 128) this.restoredSpans.delete(this.restoredSpans.keys().next().value!);
    return repeat;
  }

  private rememberReceipt(receipt: string, facts: ReadFacts) {
    this.receiptPaths.delete(receipt); this.receiptPaths.set(receipt, facts);
    while (this.receiptPaths.size > 256) this.receiptPaths.delete(this.receiptPaths.keys().next().value!);
  }

  private unchangedFact(entry: LedgerEntry, facts: ReadFacts | null, value: Record<string, any>, locator: ArchiveLocator | null,
    bodyVisible = true) {
    if (facts && typeof value.repeatReceipt === "string") this.rememberReceipt(value.repeatReceipt, facts);
    const identityFields = ["canonicalProject", "canonicalProjectRoot", "projectIdentity", "canonicalPath", "activeProject",
      "resolvedRootType", "projectRelativePath"];
    return { kind: "redundant_source_read", ok: true, status: "unchanged_already_delivered",
      path: entry.path, sha256: entry.sourceVersion, sourceKey: entry.sourceKey,
      ...Object.fromEntries(identityFields.filter(key => value[key] !== undefined).map(key => [key, value[key]])),
      ...(facts?.lines ? { startLine: facts.lines[0], endLine: facts.lines[1] } : {}),
      ...(entry.totalLines ? { totalLines: entry.totalLines } : {}),
      ...(facts?.bytes ? { offsetBytes: facts.bytes[0], nextOffsetBytes: facts.bytes[1] + 1 } : {}),
      ...(entry.size !== null ? { size: entry.size } : {}),
      unchangedSince: unchangedSince(entry), deliveredRanges: readLedgerModule.deliveredText(entry),
      readCoverageState: entry.complete ? "complete" : "partial", bodyOmitted: true, omittedBodyChars: facts?.chars ?? 0,
      bodyInCurrentInput: bodyVisible,
      ...(locator ? { archiveRef: { evidenceId: locator.evidenceId, version: locator.version, readHandle: locator.readHandle, view: "body" } } : {}),
      ...(locator?.outline ? { sourceOutline: { entries: locator.outline, approximate: true } } : {}),
      ...(typeof value.repeatReceipt === "string" ? { repeatReceipt: value.repeatReceipt } : {}),
      notice: readLedgerModule.unchangedReadNotice(entry, locator, bodyVisible) };
  }

  /** After the returned results were archived: bind each delivered version to
   * its archive record, remember receipts and result sizes. */
  recordReturnedReads(screening: ReadScreening) {
    for (const item of screening.pending) {
      const result = { toolCallId: item.toolCallId, content: item.content ?? undefined };
      const original = screening.messages.flatMap(message => message.getToolCallResults())
        .find(candidate => String(candidate.toolCallId) === item.toolCallId);
      const locator = this.store?.archiveRefFor(item.request, original || result) || null;
      if (item.repeatReceipt) this.rememberReceipt(item.repeatReceipt, item.facts);
      if (locator) {
        this.ledger.attachArchiveRef(item.facts, locator);
        this.handleCache.set(locator.evidenceId, locator.readHandle);
      }
      const content = original?.content;
      const bytes = Buffer.byteLength(typeof content === "string" ? content : JSON.stringify(content ?? ""));
      if (item.facts.complete || item.request.name !== "read_file") this.ledger.rememberResultSize(
        repeatRequestKey(item.request.name, item.request.arguments || {}), bytes);
    }
  }

  /** Bytes the same request returned before, while its version is unchanged. */
  knownResultBytes = (name: string, args: Record<string, unknown>): number | undefined =>
    name === "evidence_first_read_context" ? this.store?.archiveResponseBytes?.(args)
      : this.ledger.knownResultBytes(repeatRequestKey(name, args));

  /** Accepted packet claims tied to observed source ranges. Acceptance and
   * source coverage do not independently verify the assistant's statement. */
  recordChecks(packet: unknown, roundIndex: number, accepted = false): number {
    if (!accepted) return 0;
    let recorded = 0;
    for (const item of auditChecksFromPacket(packet))
      if (this.ledger.recordCheck(item.location, item.check, roundIndex + 1, item.claim, item.sourceVersion)) recorded++;
    return recorded;
  }

  /** The ledger as a runtime fact for the input being assembled. */
  readLedgerFact(history: Chat, reportNow = false): string | null {
    const ledger = this.ledger;
    if (!ledger.size) return null;
    const messages = history.getMessagesArray();
    const observations = this.rawObservations(history);
    const excerpts = new Set<string>(), handles = new Map<string, string>();
    const id = (key: unknown, version: unknown) => `${key}\u0000${String(version || "").toLowerCase()}`;
    for (const message of messages) for (const result of message.getToolCallResults()) {
      const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
      const projected = readLedgerModule.projectionFacts(value);
      if (projected) {
        excerpts.add(id(projected.sourceKey || projected.key, projected.sourceVersion));
        if (typeof value?.archiveRef?.readHandle === "string") handles.set(id(projected.sourceKey || projected.key, projected.sourceVersion), value.archiveRef.readHandle);
      } else if (value?.kind === "redundant_source_read" && typeof value.archiveRef?.readHandle === "string")
        handles.set(id(value.sourceKey || readLedgerModule.pathKey(value.path), value.sha256), value.archiveRef.readHandle);
    }
    for (const entry of ledger.entries()) {
      const key = `${entry.sourceKey || entry.key}\u0000${entry.sourceVersion}`;
      if (handles.has(key) || !entry.archiveRef) continue;
      let handle = this.handleCache.get(entry.archiveRef.evidenceId);
      if (handle === undefined) {
        handle = this.store?.archiveLocator(entry.archiveRef)?.readHandle || null;
        this.handleCache.set(entry.archiveRef.evidenceId, handle);
      }
      if (handle) handles.set(key, handle);
    }
    return readLedgerModule.ledgerFact(ledger, { observations, excerpts, handles, reportNow });
  }

  /** Receipts of identical reads whose bodies this input still carries. */
  repeatReceipts(history: Chat) {
    return repeatReceiptCandidates(history);
  }

  isArchiveRepage = (value: Record<string, unknown>) => this.repagedPages.has(archivePageKey(value));

  view(value: Record<string, unknown>, provider: string): ContentView {
    const kind = String(value.kind || "observation");
    const projected = kind === "archived_tool_result_projection", archived = ["historical_evidence_range", "historical_evidence_index"].includes(kind);
    const body = [value.content, value.body, value.text, value.excerpt].find(x => typeof x === "string");
    const version = coverageSourceVersion(value);
    return {
identity: { provider, sourceKind: kind, sourceIdentity: coverageSourceIdentity(value), query: coverageQueryKey(value) },
      version: { value: version, verified: version !== "unknown-version" && !version.startsWith("observed:") },
      representation: archived ? "sanitized_archived_tool_envelope" : projected ? "sanitized_body" : "source",
      body: typeof body === "string" ? body : "", archive: (value.archiveRef as ArchiveRef) || null,
      continuation: {
hasMore: typeof value.pageHasMore === "boolean" ? value.pageHasMore : "unknown",
        ...(typeof value.nextCursor === "string" ? { cursor: value.nextCursor } : {}), durable: false
}
};
  }
}

export type ReadScreening = {
  messages: Array<ChatMessage>; replaced: number; redundant: number; duplicatedChars: number; repages: number;
  newDeliveries: number; readResults: number; failedReads: number; otherResults: number; focusedReads: number;
  /** Hidden bodies this execution had already restored once, restored again. */
  repeatRestores: number;
  pending: Array<{ request: ToolCallRequest; toolCallId: string; content: string | null; facts: ReadFacts; repeatReceipt: string }>;
  /** Results whose body entered the working history as an UNCHANGED fact. */
  suppressedCallIds: string[];
};

/** One immutable round decision for recovery, stagnation and reporting. Actual
 * regained availability stays measured even when it repeats an earlier restore. */
export type ReadDelta = RecoveryProgress & {
  readonly verdict: RoundVerdict;
  readonly envelopes: Array<{ tool: RemoteToolLike; request: ToolCallRequest; envelope: ReturnType<typeof decodeEnvelope> }>;
  repeatRestoreOnly: boolean; noNewEvidence: boolean; usableSourceEvidence: boolean;
};

function unchangedSince(entry: LedgerEntry) {
  return entry.firstTurn === "earlier" ? "an earlier turn" : entry.firstRound === null ? "an earlier round" : `round ${entry.firstRound}`;
}

function archivePageKey(value: Record<string, unknown>) {
  return JSON.stringify([value.evidenceId, value.version, value.returnedRange]);
}

export type RepeatReceipt = { receipt: string; arguments: Record<string, unknown> };

/** Receipts of successful identical reads whose body is still verbatim in this
 * input: the server answers "no new information" only while the source is
 * unchanged, and that answer becomes the UNCHANGED fact. A projected or
 * archived read carries no receipt, so repeating it returns the body the
 * model asked for instead of a locator it must page in further rounds
 * (2026-10-25 run: two or three extra rounds per archived file). */
export function repeatReceiptCandidates(history: Chat): Map<string, RepeatReceipt> {
  const present = new Map<string, RepeatReceipt>();
  const index = workingContextModule.exchangeIndex(history);
  history.getMessagesArray().forEach((message, mi) => {
    message.getToolCallResults().forEach((result, ri) => {
      const request = index.matches.get(`${mi}:${ri}`);
      const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
      if (!request || !value || value.ok === false) return;
      const kind = String(value.kind || "");
      if (value.duplicate === true || value.bodyOmitted === true
        || ["archived_tool_result_projection", "historical_evidence_range", "historical_evidence_index"].includes(kind)
        || typeof value.repeatReceipt !== "string" || !value.repeatReceipt) return;
      present.set(repeatRequestKey(request.name, request.arguments || {}), { receipt: value.repeatReceipt, arguments: request.arguments || {} });
    });
  });
  return present;
}

export function presentRepeatReceipts(history: Chat): Map<string, string> {
  return new Map([...repeatReceiptCandidates(history)].map(([key, value]) => [key, value.receipt]));
}


export function completedRequestFingerprints(history: Chat): Set<string> {
  const completed = new Set<string>();
  const index = workingContextModule.exchangeIndex(history);
  history.getMessagesArray().forEach((message, mi) => {
    message.getToolCallResults().forEach((result, ri) => {
      const request = index.matches.get(`${mi}:${ri}`);
      if (!request) return;
      const decoded = toolMemory.decodeToolResultRecord(result.content);
      const value = decoded.value;
      const status = String(value?.status || "").toLowerCase();
      const currentRaw = value && !["archived_tool_result_projection", "historical_evidence_index"].includes(
        String(value.kind || ""),
      );
      const succeeded = currentRaw && resultSemantics(value).operationStatus === "completed" && !sourceObservationFailed(value)
        && !["error", "failed", "timeout", "timed_out", "canceled", "cancelled"].includes(status);
      if (succeeded) completed.add(telemetryFingerprint({
        name: request.name,
        arguments: request.arguments || {},
      }));
    });
  });
  return completed;
}

export type RecoveryCoverageRange = [number, number];

export type RecoveryCoverageLedger = Map<string, Array<RecoveryCoverageRange>>;

export type RecoveryCoverageDescriptor = {
  category: "source" | "archive";
  key: string;
  range: RecoveryCoverageRange;
  rangeUnit: string;
  sourceVersion?: string;
  sourceIdentity?: string;
};

export function finiteCoverageInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function normalizedCoverageRanges(ranges: Array<RecoveryCoverageRange>): Array<RecoveryCoverageRange> {
  return inputAvailability.normalizeRanges(ranges as Array<unknown>);
}

export const subtractCoverageRanges = require("./evidence-ranges.js").subtractRanges as (
  candidate: RecoveryCoverageRange, prior: Array<RecoveryCoverageRange>
) => Array<RecoveryCoverageRange>;

export const coverageSourceIdentity = identity.sourceIdentity;
export const coverageSourceVersion = identity.sourceVersion;
export const coverageQueryKey = identity.queryKey;
export function coverageRange(value: Record<string, unknown>, category: "source" | "archive"): { range: RecoveryCoverageRange; rangeUnit: string } | null {
  const native = value.range as { start?: number; end?: number; endExclusive?: number; unit?: string } | undefined;
  if (native && native.start !== undefined) {
    const start = finiteCoverageInteger(native.start), end = finiteCoverageInteger(native.endExclusive ?? native.end);
    if (start === null || end === null || end < start || (native.endExclusive !== undefined && end === start)) return null;
    return { range: [start, native.endExclusive !== undefined ? end - 1 : end],
      rangeUnit: native.unit === "byte" ? "utf8_byte" : String(native.unit || "unknown") };
  }
  if (value.returnedLineCount === 0 || value.returnedCount === 0) return null;
  const returned = value.returnedRange ?? value.archiveReturnedRange;
  if (Array.isArray(returned) && returned.length >= 2) {
    const start = finiteCoverageInteger(returned[0]);
    const end = finiteCoverageInteger(returned[1]);
    if (start !== null && end !== null && end >= start) {
      const exclusive = category === "archive" && (value.nextOffset !== undefined
        || value.archiveHasMore !== undefined || value.archiveReachedEnd !== undefined);
      if (exclusive && end === start) return null;
      return {
        range: [start, exclusive ? end - 1 : end],
        rangeUnit: String(value.rangeUnit || (exclusive ? "utf16_code_units" : "unknown"))
      };
    }
    return null;
  }
  const offsetStart = finiteCoverageInteger(value.startOffset ?? value.offsetBytes);
  const offsetEnd = finiteCoverageInteger(value.nextOffset ?? value.nextOffsetBytes);
  if (offsetStart !== null && offsetEnd !== null && offsetEnd >= offsetStart) {
    if (offsetEnd === offsetStart) return null;
    return {
      range: [offsetStart, Math.max(offsetStart, offsetEnd - 1)],
      rangeUnit: value.offsetBytes !== undefined ? "utf8_byte" : "utf16_code_units"
    };
  }
  const pageStart = finiteCoverageInteger(value.pageStart ?? value.sourcePageStart);
  const pageEnd = finiteCoverageInteger(value.pageEnd ?? value.sourcePageEnd);
  if (pageStart !== null && pageEnd !== null && pageEnd >= pageStart) {
    return { range: [pageStart, pageEnd], rangeUnit: "page" };
  }
  const lineStart = finiteCoverageInteger(value.startLine);
  const lineEnd = finiteCoverageInteger(value.endLine);
  if (lineStart !== null && lineEnd !== null && lineEnd >= lineStart) {
    return { range: [lineStart, lineEnd], rangeUnit: "line" };
  }
  return { range: [0, 0], rangeUnit: "observation" };
}

export function recoveryCoverageDescriptors(value: Record<string, unknown>): Array<RecoveryCoverageDescriptor> {
  const kind = String(value.kind || "");
  if (value.bodyOmitted === true || value.duplicate === true || kind === "redundant_source_read") return [];
  if (kind === "archived_tool_result_projection") return [];
  const file = kind === "historical_evidence_range" ? readLedgerModule.archivePageFacts(value) : readLedgerModule.verifiedReadFacts(value);
  if (kind !== "historical_evidence_range" && readLedgerModule.readFacts(value) && !file) return [];
  if (file && (kind !== "historical_evidence_range" || file.bodyVerified)) {
    const range = file.lines || file.bytes;
    if (range) return [{ category: kind === "historical_evidence_range" ? "archive" : "source",
      key: telemetryFingerprint({ sourceKey: file.sourceKey || file.key, version: file.sourceVersion,
        unit: file.lines ? "line" : "utf8_byte" }, true),
      range, rangeUnit: file.lines ? "line" : "utf8_byte", sourceVersion: file.sourceVersion, sourceIdentity: file.sourceKey || file.key }];
  }
  if (kind === "historical_evidence_index") {
    const entries = Array.isArray(value.entries) ? value.entries as Array<Record<string, unknown>> : [];
    return entries.filter(e => /^ev_[a-f0-9]{64}$/u.test(String(e.evidenceId))
      && /^[a-f0-9]{64}$/u.test(String(e.version))).map(e => ({
      category: "archive", key: telemetryFingerprint({ catalogEntry: e.evidenceId, version: e.version }, true),
      range: [0, 0], rangeUnit: "observation", sourceVersion: String(e.version), sourceIdentity: String(e.evidenceId),
    }));
  }
  if (kind === "historical_evidence_range") {
    const archiveRef = value.archiveRef && typeof value.archiveRef === "object"
      ? value.archiveRef as Record<string, unknown> : {};
    const evidenceId = String(value.evidenceId || archiveRef.evidenceId || "unknown-evidence");
    const version = String(value.version || archiveRef.version || "unknown-version");
    const ranged = coverageRange(value, "archive");
    if (!ranged) return [];
    return [{
      category: "archive", key: telemetryFingerprint({
        evidenceId, version,
        representation: value.representation === "sanitized_body"
          ? `sanitized_body:${String(value.bodyField || "unknown")}` : "sanitized_archived_tool_envelope", rangeUnit: ranged.rangeUnit
      }, true), range: ranged.range, rangeUnit: ranged.rangeUnit,
      sourceVersion: version, sourceIdentity: evidenceId
    }];
  }
  const isSourceObservation = value.canonicalReadObservation === true || kind === "workspace_file_observation"
    || /^(?:git|file|symbol|log|unity|unreal)_observation$/u.test(kind) || Boolean(
    value.action || value.sourceAction || value.pageStart !== undefined || value.sourcePageStart !== undefined,
  );
  if (!isSourceObservation) return [];
  const ranged = coverageRange(value, "source");
  if (!ranged) return [];
  const sourceIdentity = coverageSourceIdentity(value);
  const sourceVersion = coverageSourceVersion(value);
  return [{
    category: "source", key: telemetryFingerprint({
      query: coverageQueryKey(value),
      rangeUnit: ranged.rangeUnit, representation: value.representation || "source"
    }, true), range: ranged.range,
    rangeUnit: ranged.rangeUnit, sourceVersion, sourceIdentity
  }];
}

export function projectionCoverageDescriptors(value: Record<string, unknown>): Array<RecoveryCoverageDescriptor> {
  const descriptors: Array<RecoveryCoverageDescriptor> = [];
  const archiveRef = value.archiveRef && typeof value.archiveRef === "object"
    ? value.archiveRef as Record<string, unknown> : {};
  const evidenceId = String(archiveRef.evidenceId || value.evidenceId || "unknown-evidence");
  const version = String(archiveRef.version || value.version || "unknown-version");
  const bodyRanges = Array.isArray(value.projectedBodyRanges) && value.projectedBodyRanges.length > 0;
  const ranges = bodyRanges ? value.projectedBodyRanges : value.projectedSanitizedRanges;
  const representation = bodyRanges ? `sanitized_body:${String(value.bodyField || "unknown")}`
    : "sanitized_archived_tool_envelope";
  if (Array.isArray(ranges)) for (const candidate of ranges) {
    if (!Array.isArray(candidate) || candidate.length < 2) continue;
    const start = finiteCoverageInteger(candidate[0]);
    const end = finiteCoverageInteger(candidate[1]);
    if (start === null || end === null || end <= start) continue;
    descriptors.push({
      category: "archive", key: telemetryFingerprint({
        evidenceId, version,
        representation, rangeUnit: value.bodyRangeUnit || value.rangeUnit || "utf16_code_units"
      }, true),
      range: [start, end - 1], rangeUnit: String(value.bodyRangeUnit || value.rangeUnit || "utf16_code_units"),
      sourceVersion: version, sourceIdentity: evidenceId
    });
  }
  const sourceStart = finiteCoverageInteger(value.sourcePageStart);
  const sourceEnd = finiteCoverageInteger(value.sourcePageEnd);
  if (sourceStart !== null && sourceEnd !== null && sourceEnd >= sourceStart) {
    const sourceIdentity = coverageSourceIdentity(value);
    const sourceVersion = coverageSourceVersion(value);
    descriptors.push({
      category: "source", key: telemetryFingerprint({
        query: coverageQueryKey(value),
        rangeUnit: "page", representation: "source"
      }, true), range: [sourceStart, sourceEnd],
      rangeUnit: "page", sourceVersion, sourceIdentity
    });
  }
  return descriptors;
}

export function recordRecoveryCoverage(
  ledger: RecoveryCoverageLedger,
  descriptor: RecoveryCoverageDescriptor,
): { uncovered: Array<RecoveryCoverageRange>; overlapped: boolean } {
  const prior = ledger.get(descriptor.key) || [];
  const uncovered = subtractCoverageRanges(descriptor.range, prior);
  const overlapped = prior.some(([start, end]) => start <= descriptor.range[1] && end >= descriptor.range[0]);
  ledger.set(descriptor.key, normalizedCoverageRanges([...prior, descriptor.range]));
  return { uncovered, overlapped };
}

export function sourceObservationFailed(value: Record<string, unknown>): boolean {
  return ["failed", "outcome_unknown", "canceled", "pending", "accepted"].includes(resultSemantics(value).operationStatus)
    || value.ok === false || value.resultStatus === false || Boolean(value.errorCode)
    // LM Studio SDK guard denials and implementation exceptions use {error}.
    || (value.error !== undefined && value.error !== null && value.error !== false)
    || [value.resultStatus, value.status].some(status =>
      ["error", "failed", "timeout", "timed_out", "canceled", "cancelled", "denied"].includes(String(status).toLowerCase()));
}

/** Normalize native producer payloads using the actual paired request and trusted
 * provider. A payload cannot grant itself read authority. Pagination/size fields
 * identify a range within the query, rather than a different semantic query. */
export function observationRecords(messages: Array<ChatMessage>, tools: Array<RemoteToolLike> = [],
  options: { confirmedPairsOnly?: boolean; resolveProjection?: ProjectionResolver } = {}) {
  const records: Array<{ content: unknown; value: Record<string, unknown> | undefined; trustedRead: boolean;
    request?: ToolCallRequest; tool?: RemoteToolLike }> = [];
  const history = Chat.empty(); for (const message of messages) history.append(message);
  const pairs = workingContextModule.exchangeIndex(history);
  // Do not manufacture observations out of canceled, orphaned or duplicate
  // callback sequences. The same pairing contract protects durable commit.
  // Pure range-algebra callers can supply already decoded result-only samples.
  // Runtime adapters always supply a registry and must prove complete pairing.
  if (!options.confirmedPairsOnly && pairs.ambiguous
    && (tools.length || messages.some(m => m.getToolCallRequests().length))) return records;
  for (const [mi, message] of messages.entries()) {
    for (const [ri, result] of message.getToolCallResults().entries()) {
      const decoded = toolMemory.decodeToolResultRecord(result.content).value;
      const request = pairs.matches.get(`${mi}:${ri}`);
      // Partial delivery may retain individually confirmed pairs beside a
      // canceled sibling. Orphan/duplicate results still have no match.
      if (options.confirmedPairsOnly && !request) continue;
      const matches = tools.filter(t => t.name === request?.name);
      const trusted = request && matches.length === 1 && isObservationOnlyToolCall(matches[0], request);
      const archived = ["archived_tool_result_projection", "historical_evidence_range", "historical_evidence_index"].includes(String(decoded?.kind));
      const localArchive = trusted && request.name === "evidence_first_read_context" && localObservationTools.has(matches[0]);
      const projection = decoded && trusted ? options.resolveProjection?.(decoded, request) : null;
      records.push({ content: result.content, request, tool: matches.length === 1 ? matches[0] : undefined,
        trustedRead: Boolean(trusted), value: projection || (decoded && trusted && !localArchive ? {
        ...identity.normalizeObservation(decoded, request, matches[0].pluginIdentifier || "local"), canonicalReadObservation: true,
        ...(archived ? { kind: "untrusted_archive_representation" } : {}),
      } : decoded) });
    }
  }
  return records;
}

type ProjectionResolver = (value: Record<string, unknown>, request: ToolCallRequest) => Record<string, unknown> | null;
export function seedRecoveryProgress(history: Chat, tools: Array<RemoteToolLike> = [], resolveProjection?: ProjectionResolver): {
  fingerprints: Set<string>; coverage: RecoveryCoverageLedger;
} {
  const fingerprints = new Set<string>();
  const coverage: RecoveryCoverageLedger = new Map();
  for (const result of observationRecords(history.getMessagesArray(), tools, { resolveProjection })) {
      if (tools.length && !result.trustedRead) continue;
      fingerprints.add(telemetryFingerprint(result.content));
      const value = result.value;
      if (!value || typeof value !== "object") continue;
      if (sourceObservationFailed(value)) continue;
      if (value.kind === "archived_tool_result_projection") {
        for (const descriptor of projectionCoverageDescriptors(value)) recordRecoveryCoverage(coverage, descriptor);
        continue;
      }
      const status = String(value.status || "").toLowerCase();
      const failed = value.ok === false || Boolean(value.errorCode)
        || ["error", "failed", "timeout", "timed_out", "canceled", "cancelled", "denied"].includes(status);
      if (failed) continue;
      for (const descriptor of recoveryCoverageDescriptors(value)) recordRecoveryCoverage(coverage, descriptor);
  }
  return { fingerprints, coverage };
}

export type RecoveryProgress = {
  observationOnly?: boolean;
  newResultCount: number;
  newResponseFingerprintCount: number;
  newSuccessfulObservationCount: number;
  newSourceUnits: number;
  newArchiveUnits: number;
  currentInputAvailabilityGain: null;
  restoredWorkingEvidenceUnits: number;
  usefulProgress: boolean;
  progressed: boolean;
  errorCount: number;
  duplicateCount: number;
  partialOverlapCount: number;
  /** UNCHANGED facts returned for same-hash re-reads of delivered ranges. */
  redundantRereadCount: number;
  /** Archive pages that re-delivered lines of a version already delivered. */
  archiveRepageCount: number;
  coverage: Array<Record<string, unknown>>;
};

export function observeRecoveryProgress(
  messages: Array<ChatMessage>,
  seen: Set<string>,
  coverageLedger: RecoveryCoverageLedger = new Map(),
  currentCoverage?: RecoveryCoverageLedger,
  tools: Array<RemoteToolLike> = [],
  isArchiveRepage: (value: Record<string, unknown>) => boolean = () => false,
): RecoveryProgress {
  let newResultCount = 0;
  let newSuccessfulObservationCount = 0;
  let newSourceUnits = 0;
  let newArchiveUnits = 0;
  let restoredWorkingEvidenceUnits = 0;
  // No final-input view is supplied here; historical novelty cannot measure
  // regained current availability (nor can unlike range units be summed).
  const currentInputAvailabilityGain = null;
  let errorCount = 0;
  let duplicateCount = 0;
  let partialOverlapCount = 0;
  let redundantRereadCount = 0;
  let archiveRepageCount = 0;
  const coverage: Array<Record<string, unknown>> = [];
  const records = observationRecords(messages, tools);
  const observationOnly = records.length > 0 && records.every(result => !tools.length || result.trustedRead);
  for (const result of records) {
      if (tools.length && !result.trustedRead) continue;
      const value = result.value;
      const fingerprint = telemetryFingerprint(result.content);
      const isNewResult = !seen.has(fingerprint);
      if (isNewResult) { seen.add(fingerprint); newResultCount += 1; }
      else duplicateCount += 1;
      if (!value || typeof value !== "object") { errorCount += 1; continue; }
      if (sourceObservationFailed(value)) { errorCount += 1; continue; }
      if (value.kind === "redundant_source_read") redundantRereadCount += 1;
      // Paging the archive for lines already delivered restores, it does not add evidence.
      const repage = value.kind === "historical_evidence_range" && isArchiveRepage(value);
      if (repage) archiveRepageCount += 1;
      const descriptors = recoveryCoverageDescriptors(value);
      let resultAddedCoverage = false;
      for (const descriptor of descriptors) {
        const delta = recordRecoveryCoverage(coverageLedger, descriptor);
        if (currentCoverage && recordRecoveryCoverage(currentCoverage, descriptor).uncovered.length) {
          restoredWorkingEvidenceUnits++;
        }
        if (delta.uncovered.length && !repage) {
          resultAddedCoverage = true;
          if (descriptor.category === "source") newSourceUnits += 1;
          else newArchiveUnits += 1;
        } else if (delta.overlapped) partialOverlapCount += 1;
        coverage.push({
          key: descriptor.key,
          category: descriptor.category,
          kind: value.kind,
          sourceIdentity: descriptor.sourceIdentity,
          sourceVersion: descriptor.sourceVersion,
          rangeUnit: descriptor.rangeUnit,
          range: descriptor.range,
          uncoveredRanges: delta.uncovered,
          resultFingerprint: fingerprint,
        });
      }
      if (resultAddedCoverage) newSuccessfulObservationCount += 1;
  }
  const progressed = newSourceUnits > 0 || newArchiveUnits > 0 || restoredWorkingEvidenceUnits > 0;
  return {
    observationOnly,
    newResultCount, newResponseFingerprintCount: newResultCount, newSuccessfulObservationCount,
    newSourceUnits, newArchiveUnits, currentInputAvailabilityGain, restoredWorkingEvidenceUnits,
    usefulProgress: progressed, progressed, errorCount, duplicateCount, partialOverlapCount,
    redundantRereadCount, archiveRepageCount, coverage
  };
}
