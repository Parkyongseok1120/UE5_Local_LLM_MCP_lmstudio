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
import { isObservationOnlyToolCall, type RemoteToolLike } from "./tool-capability-registry";
import { auditChecksFromPacket, emptyReferenceState, ingestReferenceObservations, referenceSnapshot, seedPriorEngineIdentity } from "./reference-context";
import type { ToolScope } from "./tool-scope";
import { resultSemantics } from "./tool-result-semantics.js";
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
  private readonly observedRounds = new Map<string, RecoveryProgress>();
  seed(history: Chat) {
    const state = seedRecoveryProgress(history, this.tools);
    for (const fp of state.fingerprints) this.fingerprints.add(fp);
    for (const [key, ranges] of state.coverage)
      this.coverage.set(key, normalizedCoverageRanges([...(this.coverage.get(key) || []), ...ranges]));
    this.boundProgress();
  }
  private boundProgress() {
    for (const collection of [this.fingerprints, this.coverage, this.currentCoverage])
      while (collection.size > 2048) collection.delete(collection.keys().next().value!);
  }
  observe(messages: Array<ChatMessage>, roundId?: string) {
    if (roundId && this.observedRounds.has(roundId)) return this.observedRounds.get(roundId)!;
    const progress = observeRecoveryProgress(messages, this.fingerprints, this.coverage, this.currentCoverage, this.tools,
      this.isArchiveRepage);
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
    this.currentCoverage = seedRecoveryProgress(history, this.tools).coverage;
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
  // Execution-local: the receipt of a read whose body later left the input,
  // by the archive record it was captured as, and archive pages this
  // execution saw re-deliver a version the ledger already held.
  private readonly archivedReceipts = new Map<string, { key: string; receipt: string; arguments: Record<string, unknown> }>();
  private readonly repagedPages = new Set<string>();
  // The delivered path each receipt this execution may send back stands for.
  private readonly receiptPaths = new Map<string, string>();
  private readonly handleCache = new Map<string, string | null>();

  /** Before returned results enter the working history: a same-hash read of
   * an already delivered range becomes an UNCHANGED fact with the archive
   * locator of that version, unless it is a narrower read or no readable
   * record of the version exists. A different hash always keeps its body. */
  screenReturnedReads(messages: Array<ChatMessage>, roundIndex: number): ReadScreening {
    const screening: ReadScreening = { messages: [...messages], replaced: 0, redundant: 0, duplicatedChars: 0, repages: 0,
      newDeliveries: 0, readResults: 0, failedReads: 0, otherResults: 0, pending: [], suppressedCallIds: [] };
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
            if (this.ledger.notePage(page, round)) { screening.repages++; this.repagedPages.add(archivePageKey(value!)); }
            else screening.newDeliveries++;
          }
          return result;
        }
        if (!readLedgerModule.SOURCE_READ_TOOLS.has(request.name)) { screening.otherResults++; return result; }
        screening.readResults++;
        if (!value || sourceObservationFailed(value)) { screening.failedReads++; return result; }
        const content = this.screenSourceRead(request, String(result.toolCallId), value, round, screening);
        if (content === null) return result;
        changed = true; screening.replaced++;
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
      const path = request.arguments?.path, receipt = request.arguments?.repeatReceipt;
      const receiptPath = typeof receipt === "string" ? this.receiptPaths.get(receipt) : undefined;
      const entry = (receiptPath && ledger.latestFor(receiptPath)) || (typeof path === "string" ? ledger.latestFor(path) : null);
      if (!entry) return null;
      ledger.noteRedundant(entry, round, 0, { source: "server_repeat_receipt" });
      screening.redundant++; screening.suppressedCallIds.push(toolCallId);
      return JSON.stringify(this.unchangedFact(entry, null, value, this.store?.archiveLocator(entry.archiveRef, true) || null));
    }
    const facts = readLedgerModule.readFacts(value);
    if (!facts) { screening.otherResults++; screening.readResults--; return null; }
    const verdict = ledger.classify(facts);
    const pending = (content: string | null) => {
      screening.pending.push({ request, toolCallId, content, facts, repeatReceipt: typeof value.repeatReceipt === "string" ? value.repeatReceipt : "" });
      return content;
    };
    if (verdict.status === "redundant" && verdict.entry) {
      const locator = this.store?.archiveLocator(verdict.entry.archiveRef, true) || null;
      ledger.noteRedundant(verdict.entry, round, facts.chars, locator ? {} : { bodyKept: "no_archive_locator" });
      screening.redundant++; screening.duplicatedChars += facts.chars;
      if (locator) {
        screening.suppressedCallIds.push(toolCallId);
        return JSON.stringify(this.unchangedFact(verdict.entry, facts, value, locator));
      }
      // No readable record of this version is left: this body becomes its locator.
      ledger.record(facts, { round, toolName: request.name });
      return pending(null);
    }
    const prior = verdict.entry;
    ledger.record(facts, { round, toolName: request.name });
    if (verdict.status !== "narrower") screening.newDeliveries++;
    if (!prior) return pending(null);
    // Same hash as an earlier delivery: say so, so "modified" can only come from a hash change.
    return pending(JSON.stringify({ ...value, unchangedSince: unchangedSince(prior),
      unchangedNotice: `Same sha ${prior.sourceVersion.slice(0, 8)} as the version delivered in ${unchangedSince(prior)}: the file is unchanged.` }));
  }

  private rememberReceipt(receipt: string, path: string) {
    this.receiptPaths.delete(receipt); this.receiptPaths.set(receipt, path);
    while (this.receiptPaths.size > 256) this.receiptPaths.delete(this.receiptPaths.keys().next().value!);
  }

  private unchangedFact(entry: LedgerEntry, facts: ReadFacts | null, value: Record<string, any>, locator: ArchiveLocator | null) {
    if (typeof value.repeatReceipt === "string") this.rememberReceipt(value.repeatReceipt, entry.path);
    const identityFields = ["canonicalProject", "canonicalProjectRoot", "projectIdentity", "canonicalPath", "activeProject",
      "resolvedRootType", "projectRelativePath"];
    return { kind: "redundant_source_read", ok: true, status: "unchanged_already_delivered",
      path: entry.path, sha256: entry.sourceVersion,
      ...Object.fromEntries(identityFields.filter(key => value[key] !== undefined).map(key => [key, value[key]])),
      ...(facts?.lines ? { startLine: facts.lines[0], endLine: facts.lines[1] } : {}),
      ...(entry.totalLines ? { totalLines: entry.totalLines } : {}),
      ...(facts?.bytes ? { offsetBytes: facts.bytes[0], nextOffsetBytes: facts.bytes[1] + 1 } : {}),
      ...(entry.size !== null ? { size: entry.size } : {}),
      unchangedSince: unchangedSince(entry), deliveredRanges: readLedgerModule.deliveredText(entry),
      readCoverageState: entry.complete ? "complete" : "partial", bodyOmitted: true, omittedBodyChars: facts?.chars ?? 0,
      ...(locator ? { archiveRef: { evidenceId: locator.evidenceId, version: locator.version, readHandle: locator.readHandle, view: "body" } } : {}),
      ...(locator?.outline ? { sourceOutline: { entries: locator.outline, approximate: true } } : {}),
      ...(typeof value.repeatReceipt === "string" ? { repeatReceipt: value.repeatReceipt } : {}),
      notice: readLedgerModule.unchangedReadNotice(entry, locator) };
  }

  /** After the returned results were archived: bind each delivered version to
   * its archive record, remember receipts and result sizes. */
  recordReturnedReads(screening: ReadScreening) {
    for (const item of screening.pending) {
      const result = { toolCallId: item.toolCallId, content: item.content ?? undefined };
      const original = screening.messages.flatMap(message => message.getToolCallResults())
        .find(candidate => String(candidate.toolCallId) === item.toolCallId);
      const locator = this.store?.archiveRefFor(item.request, original || result) || null;
      if (item.repeatReceipt) this.rememberReceipt(item.repeatReceipt, item.facts.path);
      if (locator) {
        this.ledger.attachArchiveRef(item.facts, locator);
        this.handleCache.set(locator.evidenceId, locator.readHandle);
        if (item.repeatReceipt) this.archivedReceipts.set(locator.evidenceId, { key: repeatRequestKey(item.request.name,
          item.request.arguments || {}), receipt: item.repeatReceipt, arguments: item.request.arguments || {} });
      }
      const content = original?.content;
      const bytes = Buffer.byteLength(typeof content === "string" ? content : JSON.stringify(content ?? ""));
      if (item.facts.complete || item.request.name !== "read_file") this.ledger.rememberResultSize(
        repeatRequestKey(item.request.name, item.request.arguments || {}), bytes);
    }
  }

  /** Bytes the same request returned before, while its version is unchanged. */
  knownResultBytes = (name: string, args: Record<string, unknown>): number | undefined =>
    this.ledger.knownResultBytes(repeatRequestKey(name, args));

  /** Per-file checks from a submitted evidence packet's verified claims, tied
   * to the version the ledger holds for each cited file. */
  recordChecks(packet: unknown, roundIndex: number): number {
    let recorded = 0;
    for (const item of auditChecksFromPacket(packet))
      if (this.ledger.recordCheck(item.location, item.check, roundIndex + 1, item.claim)) recorded++;
    return recorded;
  }

  /** The ledger as a runtime fact for the input being assembled. */
  readLedgerFact(history: Chat, reportNow = false): string | null {
    const ledger = this.ledger;
    if (!ledger.size) return null;
    const messages = history.getMessagesArray();
    const observations = inputAvailability.currentRawObservations(messages.map(message =>
      ({ toolResults: message.getToolCallResults() })) as unknown as Parameters<typeof inputAvailability.currentRawObservations>[0]);
    const excerpts = new Set<string>(), handles = new Map<string, string>();
    const id = (path: unknown, version: unknown) => `${readLedgerModule.pathKey(path)}\u0000${String(version || "").toLowerCase()}`;
    for (const message of messages) for (const result of message.getToolCallResults()) {
      const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
      const projected = readLedgerModule.projectionFacts(value);
      if (projected) {
        excerpts.add(id(projected.path, projected.sourceVersion));
        if (typeof value?.archiveRef?.readHandle === "string") handles.set(id(projected.path, projected.sourceVersion), value.archiveRef.readHandle);
      } else if (value?.kind === "redundant_source_read" && typeof value.archiveRef?.readHandle === "string")
        handles.set(id(value.path, value.sha256), value.archiveRef.readHandle);
    }
    for (const entry of ledger.entries()) {
      const key = id(entry.path, entry.sourceVersion);
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

  /** Receipts of identical reads this input carries, including reads whose
   * bodies were since projected or archived. */
  repeatReceipts(history: Chat) {
    return repeatReceiptCandidates(history, this.archivedReceipts);
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
  newDeliveries: number; readResults: number; failedReads: number; otherResults: number;
  pending: Array<{ request: ToolCallRequest; toolCallId: string; content: string | null; facts: ReadFacts; repeatReceipt: string }>;
  /** Results whose body entered the working history as an UNCHANGED fact. */
  suppressedCallIds: string[];
};

function unchangedSince(entry: LedgerEntry) {
  return entry.firstTurn === "earlier" ? "an earlier turn" : entry.firstRound === null ? "an earlier round" : `round ${entry.firstRound}`;
}

function archivePageKey(value: Record<string, unknown>) {
  return JSON.stringify([value.evidenceId, value.version, value.returnedRange]);
}

export type RepeatReceipt = { receipt: string; arguments: Record<string, unknown> };

/** Receipts of successful identical reads in this input. A read whose body
 * was projected or archived still names it: the server answers "no new
 * information" only while the source is unchanged, and that answer becomes
 * the UNCHANGED fact with the archive locator. */
export function repeatReceiptCandidates(history: Chat,
  archived: Map<string, { key: string; receipt: string; arguments: Record<string, unknown> }> = new Map()): Map<string, RepeatReceipt> {
  const present = new Map<string, RepeatReceipt>();
  const index = workingContextModule.exchangeIndex(history);
  history.getMessagesArray().forEach((message, mi) => {
    message.getToolCallResults().forEach((result, ri) => {
      const request = index.matches.get(`${mi}:${ri}`);
      const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
      if (!request || !value || value.ok === false) return;
      const kind = String(value.kind || "");
      if (["archived_tool_result_projection", "historical_evidence_range"].includes(kind)) {
        const stored = archived.get(String(value.archiveRef?.evidenceId || value.evidenceId || ""));
        if (stored) present.set(stored.key, { receipt: stored.receipt, arguments: stored.arguments });
        return;
      }
      if (kind === "historical_evidence_index" || typeof value.repeatReceipt !== "string" || !value.repeatReceipt) return;
      present.set(repeatRequestKey(request.name, request.arguments || {}), { receipt: value.repeatReceipt, arguments: request.arguments || {} });
    });
  });
  return present;
}

export function presentRepeatReceipts(history: Chat,
  archived?: Map<string, { key: string; receipt: string; arguments: Record<string, unknown> }>): Map<string, string> {
  return new Map([...repeatReceiptCandidates(history, archived)].map(([key, value]) => [key, value.receipt]));
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
  if (kind === "archived_tool_result_projection") return [];
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
  options: { confirmedPairsOnly?: boolean } = {}) {
  const records: Array<{ content: unknown; value: Record<string, unknown> | undefined; trustedRead: boolean }> = [];
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
      records.push({ content: result.content, trustedRead: Boolean(trusted), value: decoded && trusted && !archived ? {
        ...identity.normalizeObservation(decoded, request, matches[0].pluginIdentifier || "local"), canonicalReadObservation: true,
      } : decoded });
    }
  }
  return records;
}

export function seedRecoveryProgress(history: Chat, tools: Array<RemoteToolLike> = []): {
  fingerprints: Set<string>; coverage: RecoveryCoverageLedger;
} {
  const fingerprints = new Set<string>();
  const coverage: RecoveryCoverageLedger = new Map();
  for (const result of observationRecords(history.getMessagesArray(), tools)) {
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
