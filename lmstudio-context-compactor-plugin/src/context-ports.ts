import {
  Chat,
  ChatMessage,
  type Tool,
  type ToolCallRequest
} from "@lmstudio/sdk";
import { type CheckpointResult, type ContextMeasurement, type ContinuityNote, type InputAvailabilityProjection, type NormalizedMessage } from "./execution-contracts";

// The deterministic core is CommonJS so it can also be exercised directly by
// Node's test runner without booting an LM Studio plugin host.
export const core = require("./direct-compaction-core.js") as {
  buildCheckpoint(messages: Array<NormalizedMessage>, options?: Record<string, unknown>): CheckpointResult;
  invariantSystemText(message: NormalizedMessage): string;
  shouldCompact(measurement: ContextMeasurement, options?: Record<string, unknown>): boolean;
};

export const objectiveContinuity = require("./continuity-objectives.js") as {
  guidanceObjective(messages: Array<NormalizedMessage & { index: number }>, previousState: unknown): { text?: string; reason: string };
};
export const continuityState = require("./continuity-memory.js") as {
  extractPriorContinuityState(messages: Array<NormalizedMessage & { index: number }>): unknown;
};

export const modelNotes = require("./continuity-model-notes.js") as {
  NOTE_INSTRUCTION: string;
  ContinuityNoteStore: new () => {
    read(key: string): ContinuityNote | null;
    write(key: string, note: ContinuityNote): boolean;
  };
  attachScope(draft: unknown, fingerprint: string, messages: Array<ChatMessage>,
    additionalVerifiedRefs?: Set<string>): ContinuityNote | null;
  resolveNoteUpdate(draft: unknown, fingerprint: string, messages: Array<ChatMessage>,
    additionalVerifiedRefs?: Set<string>, options?: { allowClear?: boolean }):
    { kind: "accepted_update"; note: ContinuityNote } | { kind: "explicit_clear"; note: null }
    | { kind: "rejected"; reason: string };
  historyKey(messages: Array<ChatMessage>, workingDirectory: string): string;
  renderAvailableRefs(messages: Array<ChatMessage>, limit?: number, maxChars?: number): string;
  objectiveFingerprint(messages: Array<ChatMessage>): string;
  reconcileStoredNote(note: ContinuityNote, messages: Array<ChatMessage>,
    additionalVerifiedRefs?: Set<string>, options?: { objectiveFingerprint?: string; projectDescriptor?: string;
      discardReviewClaims?: boolean }): ContinuityNote | null;
  renderAssistantNote(note: ContinuityNote, messages?: ChatMessage[]): string;
  splitVisibleAnswer(text: string): { visibleText: string; hasFooter: boolean; note: unknown };
};

export const workingContextModule = require("./working-context.js") as {
  exchangeIndex(history: Chat): { matches: Map<string, ToolCallRequest>; ambiguous: boolean };
  WorkingContext: new (scope: Record<string, string>, options?: Record<string, unknown>) => {
    archive: { stats: Record<string, number> };
    readonly hasArchivedEvidence: boolean;
    cost: Record<string, number>;
    exposure: Map<string, unknown>;
    note: unknown;
    referenceObservations: any[];
    lastSummaryInput: string;
    summaryAttempts?: string[];
    lastCommitReason: string | null;
    persistable(history: Chat): Chat;
    project(history: Chat, observation: (request: ToolCallRequest) => boolean,
      metadata?: Record<string, unknown>, maxChars?: number): {
        history: Chat; changed: boolean; archiveFailed?: boolean; reason: string;
      };
    tool(): Tool;
    restore(history: Chat): { history: Chat; reason: string; detail?: Record<string, unknown> };
    commit(source: Chat, candidate: Chat, measurement: ContextMeasurement,
      expectedPrefix: string, modelFingerprint: string): boolean;
    seal(source: Chat, candidate: Chat): boolean;
    close(): boolean;
    captureExposure(history: Chat, modelInputId: string, completed?: boolean): void;
    pendingResultIndexes(history: Chat): number[];
    pinnedResultIndexes(history: Chat, maxChars: number): number[];
    archiveConversationProse(history: Chat, retainedIndexes?: number[]): number;
    releaseOmittedPending(history: Chat): string[];
    captureReturned(history: Chat, observation: (request: ToolCallRequest) => boolean, metadata?: Record<string, unknown>): number;
    summaryRefs(): Set<string>;
    verifiedNoteRefs(note: unknown): Set<string>;
    summaryEvidence(maxItems?: number, maxChars?: number): Array<Record<string, unknown>>;
    readLedger: ReadLedgerLike;
    archiveRefFor(request: ToolCallRequest, result: unknown): ArchiveLocator | null;
    archiveLocator(ref: { evidenceId: string; version: string } | undefined, withOutline?: boolean, expected?: ReadFacts | null): ArchiveLocator | null;
  };
  serialize(history: Chat): Array<unknown>;
  validateSemanticNote(text: string, refs: Set<string>, generation: number,
    parentWindow: string | null): Record<string, unknown> | null;
  hash(value: unknown): string;
};

export const { REASONING_SEPARATOR } = require("./continuity-text.js") as { REASONING_SEPARATOR: string };

export const toolMemory = require("./compaction-tool-memory.js") as {
  decodeToolResultRecord(content: unknown): { value?: Record<string, unknown>; error?: string };
};

export type ArchiveLocator = { evidenceId: string; version: string; readHandle: string; outline?: string[] };
/** What one source read, projection or archive page covers of one path+version. */
export type ReadFacts = { path: string; key: string; sourceVersion: string; lines: [number, number] | null;
  bytes: [number, number] | null; complete: boolean; totalLines: number | null; size: number | null; chars: number;
  partialPage?: boolean; bodyVerified?: boolean };
export type LedgerEntry = { path: string; key: string; sourceVersion: string; lineRanges: Array<[number, number]>;
  byteRanges: Array<[number, number]>; complete: boolean; totalLines: number | null; size: number | null; chars: number;
  firstRound: number | null; firstTurn: string; lastRound: number | null; deliveries: number; redundant: number;
  duplicatedChars: number; archivePages: number; checks: Array<{ check: string; round: number | null; claim?: string }>;
  archiveRef?: { evidenceId: string; version: string } };
export type ReadLedgerLike = {
  readonly size: number;
  get(path: string, sourceVersion: string): LedgerEntry | null;
  latestFor(path: string): LedgerEntry | null;
  entries(): LedgerEntry[];
  classify(facts: ReadFacts | null): { status: "unknown" | "new_file" | "new_version" | "new_range" | "redundant" | "narrower";
    entry: LedgerEntry | null; previous?: LedgerEntry | null };
  record(facts: ReadFacts | null, options?: { round?: number | null; turn?: string; toolName?: string;
    archiveRef?: { evidenceId: string; version: string } | null }): LedgerEntry | null;
  attachArchiveRef(facts: ReadFacts | null, archiveRef: { evidenceId: string; version: string } | null): LedgerEntry | null;
  noteRedundant(entry: LedgerEntry | null, round: number | null, duplicatedChars?: number, extra?: Record<string, unknown>): void;
  notePage(facts: ReadFacts | null, round: number | null): boolean;
  recordCheck(location: string, check: string, round?: number | null, claim?: string, sourceVersion?: string): boolean;
  rememberResultSize(requestKey: string, bytes: number): void;
  knownResultBytes(requestKey: string): number | undefined;
  stats(): Record<string, number>;
  events: Array<Record<string, unknown>>;
  toJSON(): Record<string, unknown>;
  load(value: unknown): boolean;
};
export const readLedgerModule = require("./read-ledger.js") as {
  ReadLedger: new () => ReadLedgerLike;
  SOURCE_READ_TOOLS: Set<string>;
  readFacts(value: unknown): ReadFacts | null;
  projectionFacts(value: unknown): ReadFacts | null;
  archivePageFacts(value: unknown): ReadFacts | null;
  pathKey(value: unknown): string;
  unchangedReadNotice(entry: LedgerEntry, locator: ArchiveLocator | null, bodyVisible?: boolean): string;
  deliveredText(entry: LedgerEntry): string;
  visibleRanges(observations: Array<Record<string, unknown>>): Map<string, { line: Array<[number, number]>; utf8_byte: Array<[number, number]> }>;
  ledgerFact(ledger: ReadLedgerLike, options: { observations?: Array<Record<string, unknown>>; excerpts?: Set<string>;
    handles?: Map<string, string>; reportNow?: boolean }): string | null;
};

export const inputAvailability = require("./input-availability.js") as {
  currentRawObservations(messages: Array<NormalizedMessage>): Array<Record<string, unknown>>;
  historicalAvailabilityFromMemory(memory: unknown): Array<Record<string, unknown>>;
  normalizeRanges(ranges: Array<unknown>): Array<[number, number]>;
  projectInputAvailability(messages: Array<NormalizedMessage>, historical: Array<Record<string, unknown>>,
    options: { modelInputId: string; maxEntries?: number }): InputAvailabilityProjection;
  renderInputAvailabilityMetadata(projection: InputAvailabilityProjection): string;
  traceToolRound(messages: Array<NormalizedMessage>, options: {
    executionId: string; modelInputId: string; roundIndex: number;
  }): Record<string, unknown>;
};
