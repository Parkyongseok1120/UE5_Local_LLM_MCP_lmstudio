import {
  ChatMessage
} from "@lmstudio/sdk";
import crypto from "node:crypto";
import { toolMemory } from "./context-ports";
import { type ContinuityNote } from "./execution-contracts";
import type { RecoveryProgress } from "./evidence-manager";

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

export function readResultSize(content: unknown) {
  const envelope = typeof content === "string" ? content : JSON.stringify(content) ?? "";
  const decoded = toolMemory.decodeToolResultRecord(content).value;
  const body = decoded && [decoded.content, decoded.body, decoded.text].find(value => typeof value === "string");
  return { envelopeChars: envelope.length, envelopeBytes: Buffer.byteLength(envelope),
    bodyChars: typeof body === "string" ? body.length : null,
    bodyBytes: typeof body === "string" ? Buffer.byteLength(body) : null };
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
        bodyComplete: value.bodyComplete,
        noticePresent: typeof value.notice === "string" && value.notice.length > 0,
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
  private recent: Array<{ fingerprint: string; novel: boolean; restored: boolean }> = [];
  private announced = new Set<string>();
  // Failed requests by tool + argument fingerprint + error code, across rounds.
  // The cycle check below requires error-free rounds, so a model that keeps
  // re-sending the same failing call is otherwise never told it is repeating.
  private failures = new Map<string, number>();
  private failureAnnounced = new Set<string>();
  acknowledge(key: string) { this.announced.add(key); }

  private repeatedFailure(evidence: ReturnType<typeof messageEvidenceTelemetry>) {
    let found: { key: string; toolName: string; errorCode: string; count: number; firstNotice: boolean } | null = null;
    for (const call of evidence.calls) {
      const result = evidence.results.find(item => item.toolCallId === call.toolCallId);
      if (!result || (result.resultOk !== false && !result.errorCode)) continue;
      const errorCode = String(result.errorCode || "TOOL_FAILED");
      const key = `${call.toolName}|${call.argumentsFingerprint}|${errorCode}`;
      const count = (this.failures.get(key) || 0) + 1;
      this.failures.set(key, count);
      if (count >= 2 && !found) {
        found = { key, toolName: call.toolName, errorCode, count, firstNotice: !this.failureAnnounced.has(key) };
        this.failureAnnounced.add(key);
      }
    }
    while (this.failures.size > 64) this.failures.delete(this.failures.keys().next().value!);
    return found;
  }

  observe(messages: Array<ChatMessage>, progress?: RecoveryProgress) {
    const evidence = messageEvidenceTelemetry(messages);
    if (!evidence.calls.length || !evidence.results.length) {
      this.previous = "";
      this.count = 0;
      this.recent = []; this.announced.clear();
      return { repeated: false, count: 0, fingerprint: "", warningKey: "", cycleLength: 0, cycleDetected: false, firstWarning: false,
        reacquisition: false, repeatedFailure: null };
    }
    const repeatedFailure = this.repeatedFailure(evidence);
    const fingerprint = telemetryFingerprint({
      calls: evidence.calls.map(call => ({ toolName: call.toolName, argumentsFingerprint: call.argumentsFingerprint })),
      results: evidence.results.map(result => result.semanticResultFingerprint),
    });
    this.count = fingerprint === this.previous ? this.count + 1 : 1;
    this.previous = fingerprint;
    let cycleLength = 0, cycleKey = "", reacquisition = false;
    if (progress?.coverage.length && !progress.errorCount && progress.observationOnly !== false) {
      const unversioned = progress.coverage.some(c => c.category === "source"
        && (!c.sourceVersion || c.sourceVersion === "unknown-version"));
      const semantic = telemetryFingerprint({ coverage: progress.coverage.map(c => ({ key: c.key,
        category: c.category, version: c.sourceVersion, unit: c.rangeUnit, range: c.range }))
        .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
        // Listings and live state can change without a version or range change.
        ...(unversioned ? { results: evidence.results.map(result => result.semanticResultFingerprint).sort() } : {}) });
      this.recent.push({ fingerprint: semantic, novel: progress.newSourceUnits > 0 || progress.newArchiveUnits > 0,
        restored: progress.restoredWorkingEvidenceUnits > 0 });
      this.recent = this.recent.slice(-8);
      if (this.recent.at(-1)!.novel) this.announced.clear();
      for (let size = 2; size <= 4 && this.recent.length >= size * 2; size++) {
        const last = this.recent.slice(-size), prior = this.recent.slice(-size * 2, -size);
        if (last.every((frame, i) => !frame.novel && frame.fingerprint === prior[i].fingerprint)) {
          cycleLength = size; reacquisition = last.some(frame => frame.restored);
          cycleKey = telemetryFingerprint(last.map(frame => frame.fingerprint).sort()); break;
        }
      }
    } else this.recent = [];
    const key = cycleKey || fingerprint;
    const firstWarning = !this.announced.has(key);
    // Only remember actual repetition, so the first threshold warning is visible.
    if (cycleLength) this.announced.add(key);
    while (this.announced.size > 8) this.announced.delete(this.announced.values().next().value!);
    return { repeated: this.count > 1 || cycleLength > 0, count: this.count, fingerprint, warningKey: key,
      cycleLength, cycleDetected: cycleLength > 0, firstWarning, reacquisition, repeatedFailure };
  }
}
