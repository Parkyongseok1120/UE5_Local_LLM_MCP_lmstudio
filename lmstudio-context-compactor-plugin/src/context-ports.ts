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

export const modelNotes = require("./continuity-model-notes.js") as {
  NOTE_INSTRUCTION: string;
  ContinuityNoteStore: new () => {
    read(key: string): ContinuityNote | null;
    write(key: string, note: ContinuityNote): boolean;
  };
  attachScope(draft: unknown, fingerprint: string, messages: Array<ChatMessage>,
    additionalVerifiedRefs?: Set<string>): ContinuityNote | null;
  historyKey(messages: Array<ChatMessage>, workingDirectory: string): string;
  objectiveFingerprint(messages: Array<ChatMessage>): string;
  reconcileStoredNote(note: ContinuityNote, messages: Array<ChatMessage>,
    additionalVerifiedRefs?: Set<string>): ContinuityNote | null;
  renderAssistantNote(note: ContinuityNote): string;
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
    lastSummaryInput: string;
    lastCommitReason: string | null;
    persistable(history: Chat): Chat;
    project(history: Chat, observation: (request: ToolCallRequest) => boolean,
      metadata?: Record<string, unknown>, maxChars?: number): {
        history: Chat; changed: boolean; archiveFailed?: boolean; reason: string;
      };
    tool(): Tool;
    restore(history: Chat): { history: Chat; reason: string };
    commit(source: Chat, candidate: Chat, measurement: ContextMeasurement,
      expectedPrefix: string, modelFingerprint: string): boolean;
    captureExposure(history: Chat, modelInputId: string, completed?: boolean): void;
    captureReturned(history: Chat, observation: (request: ToolCallRequest) => boolean, metadata?: Record<string, unknown>): number;
    summaryRefs(): Set<string>;
    summaryEvidence(maxItems?: number, maxChars?: number): Array<Record<string, unknown>>;
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
