import {
  ChatMessage
} from "@lmstudio/sdk";
import crypto from "node:crypto";
import { toolMemory } from "./context-ports";
import { type ContinuityNote } from "./execution-contracts";

export const VOLATILE_OBSERVATION_KEYS = new Set([
  "observedAt", "lastObservedAt", "snapshotCapturedAt", "snapshotId", "nextCursor", "compactionGeneration",
  "gitTiming",
]);

export function canonicalTelemetryValue(value: unknown, semantic = false): unknown {
  if (Array.isArray(value)) return value.map(item => canonicalTelemetryValue(item, semantic));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !(semantic && VOLATILE_OBSERVATION_KEYS.has(key)))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => [key, canonicalTelemetryValue(item, semantic)]));
}

export function telemetryFingerprint(value: unknown, semantic = false): string {
  let parsed = value;
  if (typeof value === "string") {
    try { parsed = JSON.parse(value); } catch { parsed = value; }
  }
  if (semantic) {
    const decoded = toolMemory.decodeToolResultRecord(parsed);
    if (decoded.value) parsed = decoded.value;
  }
  const serialized = JSON.stringify(canonicalTelemetryValue(parsed, semantic));
  return crypto.createHash("sha256")
    .update(serialized === undefined ? "undefined" : serialized)
    .digest("hex");
}

export function resultContent(result: unknown): unknown {
  if (!result || typeof result !== "object") return result;
  return (result as { content?: unknown }).content;
}

export const PUBLIC_PAGED_GIT_READ_TOOLS = new Set([
  "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file",
]);

export function readResultReservation(name: string, argumentsValue: unknown): Record<string, unknown> | null {
  const args = argumentsValue && typeof argumentsValue === "object"
    ? argumentsValue as Record<string, unknown> : {};
  const limit = Number.isSafeInteger(Number(args.limit)) && Number(args.limit) > 0
    ? Number(args.limit) : undefined;
  const cursorPresent = typeof args.cursor === "string" && args.cursor.length > 0;
  if (PUBLIC_PAGED_GIT_READ_TOOLS.has(name)) {
    const defaultByteBudget = name === "git_changed_files" ? 4096 : 32768;
    const requestedByteBudget = Number.isSafeInteger(Number(args.byteBudget))
      && Number(args.byteBudget) >= 1024 ? Number(args.byteBudget) : defaultByteBudget;
    return {
      kind: "git_public_read",
      requestedLimit: limit,
      effectiveByteBudget: requestedByteBudget,
      byteBudgetSource: args.byteBudget === undefined ? "tool_default" : "request",
      cursorPresent,
      ...(cursorPresent ? { cursorFingerprint: telemetryFingerprint(args.cursor) } : {}),
    };
  }
  if (name === "evidence_first_read_context") {
    const requestedMaxChars = Number.isSafeInteger(Number(args.maxChars))
      && Number(args.maxChars) > 0 ? Number(args.maxChars) : 4096;
    return {
      kind: "archive_read",
      effectiveMaxChars: requestedMaxChars,
      maxCharsSource: args.maxChars === undefined ? "tool_default" : "request",
      cursorPresent: false,
    };
  }
  return null;
}

export function messageEvidenceTelemetry(messages: Array<ChatMessage>) {
  const calls = messages.flatMap(message => message.getToolCallRequests()).map(request => ({
    toolCallId: request.id,
    toolName: request.name,
    argumentsFingerprint: telemetryFingerprint(request.arguments || {}),
    resultReservation: readResultReservation(request.name, request.arguments || {}),
  }));
  const results = messages.flatMap(message => message.getToolCallResults()).map(result => {
    const content = resultContent(result);
    const decoded = toolMemory.decodeToolResultRecord(content);
    const value = decoded.value;
    return {
      toolCallId: result.toolCallId,
      resultFingerprint: telemetryFingerprint(content),
      semanticResultFingerprint: telemetryFingerprint(content, true),
      resultChars: typeof content === "string" ? content.length : JSON.stringify(content || null).length,
      resultBytes: Buffer.byteLength(typeof content === "string" ? content : JSON.stringify(content || null)),
      resultStatus: value?.status,
      resultOk: value?.ok,
      errorCode: value?.errorCode,
      kind: value?.kind,
      pageStart: value?.pageStart ?? value?.sourcePageStart,
      pageEnd: value?.pageEnd ?? value?.sourcePageEnd,
      pageHasMore: value?.pageHasMore ?? value?.sourcePageHasMore,
      returnedRange: value?.returnedRange ?? value?.archiveReturnedRange,
      archiveHasMore: value?.archiveHasMore,
      archiveReachedEnd: value?.archiveReachedEnd,
      nextCursorPresent: typeof value?.nextCursor === "string" && value.nextCursor.length > 0,
      ...(value?.gitTiming ? { gitTiming: value.gitTiming } : {}),
      ...(value?.kind === "archived_tool_result_projection" ? {
        viewMode: value.viewMode,
        bodyField: value.bodyField,
        bodyTotalChars: value.bodyTotalChars,
        bodyOmittedChars: value.bodyOmittedChars,
        projectedBodyRanges: value.projectedBodyRanges,
        omittedBodyRanges: value.omittedBodyRanges,
        archiveRef: value.archiveRef,
      } : {}),
    };
  });
  const requestedByteBudget = calls.reduce((total, call) => (
    total + Number((call.resultReservation as Record<string, unknown> | null)?.effectiveByteBudget || 0)
  ), 0);
  const requestedMaxChars = calls.reduce((total, call) => (
    total + Number((call.resultReservation as Record<string, unknown> | null)?.effectiveMaxChars || 0)
  ), 0);
  return {
    calls,
    results,
    batchReservation: {
      callCount: calls.length,
      publicReadCallCount: calls.filter(call => call.resultReservation !== null).length,
      requestedByteBudget,
      requestedMaxChars,
      unknownReturnLimitCallCount: calls.filter(call => call.resultReservation === null).length,
      actualResultChars: results.reduce((total, result) => total + result.resultChars, 0),
      actualResultBytes: results.reduce((total, result) => total + result.resultBytes, 0),
      actualResultCount: results.length,
    },
  };
}

export function serializedCheckpointCounts(checkpoint: string) {
  try {
    const marker = checkpoint.indexOf("[Direct continuity state v2]");
    const start = checkpoint.indexOf("{", marker);
    const parsed = JSON.parse(checkpoint.slice(start));
    return {
      serializedSchemaVersion: parsed?.schemaVersion ?? null,
      serializedCompactionGeneration: parsed?.compactionGeneration ?? null,
      serializedToolOutcomeCount: parsed?.currentWorkStatus?.recentToolOutcomes?.length || 0,
      serializedGitObservationCount: parsed?.currentWorkStatus?.gitObservations?.length || 0,
      serializedFileObservationCount: parsed?.currentWorkStatus?.modifiedOrObservedFiles?.length || 0,
      serializedObservedRangeCount: (parsed?.currentWorkStatus?.modifiedOrObservedFiles || [])
        .reduce((count: number, file: { observedLineRanges?: Array<unknown> }) => (
          count + (Array.isArray(file?.observedLineRanges) ? file.observedLineRanges.length : 0)
        ), 0),
    };
  } catch {
    return {
      serializedSchemaVersion: null, serializedCompactionGeneration: null,
      serializedToolOutcomeCount: 0, serializedGitObservationCount: 0,
      serializedFileObservationCount: 0, serializedObservedRangeCount: 0
    };
  }
}

export function assistantNoteTelemetry(note: ContinuityNote | null, enabled: boolean) {
  const items = note ? [note.decisions, note.rejectedHypotheses, note.openQuestions].flat() as Array<{
    status?: unknown;
  }> : [];
  const statusCounts = { open: 0, resolved: 0, superseded: 0 };
  for (const item of items) {
    const status = String(item?.status || "open") as keyof typeof statusCounts;
    if (Object.hasOwn(statusCounts, status)) statusCounts[status] += 1;
  }
  return {
    protocol: enabled ? "enabled" : "disabled",
    state: note ? "injected" : "absent",
    itemCount: items.length,
    reviewClaimCount: note?.reviewClaims?.length || 0,
    statusCounts,
  };
}

export class ToolRoundStagnationDetector {
  private previous = "";
  private count = 0;

  observe(messages: Array<ChatMessage>) {
    const evidence = messageEvidenceTelemetry(messages);
    if (!evidence.calls.length || !evidence.results.length) {
      this.previous = "";
      this.count = 0;
      return { repeated: false, count: 0, fingerprint: "" };
    }
    const fingerprint = telemetryFingerprint({
      calls: evidence.calls.map(call => ({ toolName: call.toolName, argumentsFingerprint: call.argumentsFingerprint })),
      results: evidence.results.map(result => result.semanticResultFingerprint),
    });
    this.count = fingerprint === this.previous ? this.count + 1 : 1;
    this.previous = fingerprint;
    return { repeated: this.count > 1, count: this.count, fingerprint };
  }
}
