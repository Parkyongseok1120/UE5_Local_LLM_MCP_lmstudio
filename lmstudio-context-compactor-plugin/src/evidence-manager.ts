import {
  Chat,
  ChatMessage,
  type ToolCallRequest
} from "@lmstudio/sdk";
import { inputAvailability, toolMemory, workingContextModule } from "./context-ports";
import { telemetryFingerprint } from "./evidence-telemetry";
import { isObservationOnlyToolCall, type RemoteToolLike } from "./tool-capability-registry";
const identity = require("./evidence-identity.js") as {
  sourceIdentity(value: Record<string, unknown>): string;
  sourceVersion(value: Record<string, unknown>): string;
  queryKey(value: Record<string, unknown>): string;
  normalizeObservation(value: Record<string, unknown>, request: ToolCallRequest, provider: string): Record<string, unknown>;
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
  constructor(readonly store: InstanceType<typeof workingContextModule.WorkingContext> | null,
    readonly tools: Array<RemoteToolLike>) { }
  seed(history: Chat) { const state = seedRecoveryProgress(history, this.tools); this.fingerprints = state.fingerprints; this.coverage = state.coverage; }
  observe(messages: Array<ChatMessage>) {
    return observeRecoveryProgress(messages, this.fingerprints, this.coverage, this.currentCoverage, this.tools);
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
  captureReturned(messages: Array<ChatMessage>, executionId: string) {
    const history = Chat.empty(); for (const message of messages) history.append(message);
    return this.store?.captureReturned(history, request => {
      const matches = this.tools.filter(tool => tool.name === request.name);
      return matches.length === 1 && isObservationOnlyToolCall(matches[0], request);
    }, { executionId, proofLevel: "returned_tool_result",
      toolProviders: Object.fromEntries(this.tools.map(t => [t.name, t.pluginIdentifier || "local"])) }) || 0;
  }
  view(value: Record<string, unknown>, provider: string): ContentView {
    const kind = String(value.kind || "observation");
    const projected = kind === "archived_tool_result_projection", archived = kind === "historical_evidence_range";
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

export function completedRequestFingerprints(history: Chat): Set<string> {
  const completed = new Set<string>();
  let active: Map<string, ToolCallRequest> | null = null;
  const consumed = new Set<string>();
  for (const message of history.getMessagesArray()) {
    const requests = message.getToolCallRequests();
    if (requests.length) {
      active = new Map();
      consumed.clear();
      for (const request of requests) {
        const id = String(request.id || "");
        if (!id || active.has(id)) {
          active = null;
          break;
        }
        active.set(id, request);
      }
    }
    if (!active) continue;
    for (const result of message.getToolCallResults()) {
      const id = String(result.toolCallId || "");
      const request = active.get(id);
      if (!request || consumed.has(id)) continue;
      consumed.add(id);
      const decoded = toolMemory.decodeToolResultRecord(result.content);
      const value = decoded.value;
      const status = String(value?.status || "").toLowerCase();
      const currentRaw = value && !["archived_tool_result_projection", "historical_evidence_index"].includes(
        String(value.kind || ""),
      );
      const succeeded = currentRaw && !sourceObservationFailed(value)
        && !["error", "failed", "timeout", "timed_out", "canceled", "cancelled"].includes(status);
      if (succeeded) completed.add(telemetryFingerprint({
        name: request.name,
        arguments: request.arguments || {},
      }));
    }
    if (active && consumed.size === active.size) {
      active = null;
      consumed.clear();
    }
  }
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

export function subtractCoverageRanges(
  candidate: RecoveryCoverageRange,
  prior: Array<RecoveryCoverageRange>,
): Array<RecoveryCoverageRange> {
  let remaining: Array<RecoveryCoverageRange> = [candidate];
  for (const [coveredStart, coveredEnd] of normalizedCoverageRanges(prior)) {
    remaining = remaining.flatMap(([start, end]) => {
      if (coveredEnd < start || coveredStart > end) return [[start, end]];
      const parts: Array<RecoveryCoverageRange> = [];
      if (start < coveredStart) parts.push([start, coveredStart - 1]);
      if (coveredEnd < end) parts.push([coveredEnd + 1, end]);
      return parts;
    });
  }
  return normalizedCoverageRanges(remaining);
}

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
        representation: "sanitized_archived_tool_envelope", rangeUnit: ranged.rangeUnit
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
  return value.ok === false || value.resultStatus === false || Boolean(value.errorCode)
    // LM Studio SDK guard denials and implementation exceptions use {error}.
    || (value.error !== undefined && value.error !== null && value.error !== false)
    || [value.resultStatus, value.status].some(status =>
      ["error", "failed", "timeout", "timed_out", "canceled", "cancelled", "denied"].includes(String(status).toLowerCase()));
}

/** Normalize native producer payloads using the actual paired request and trusted
 * provider. A payload cannot grant itself read authority. Pagination/size fields
 * identify a range within the query, rather than a different semantic query. */
export function observationRecords(messages: Array<ChatMessage>, tools: Array<RemoteToolLike> = []) {
  const records: Array<{ content: unknown; value: Record<string, unknown> | undefined }> = [];
  const history = Chat.empty(); for (const message of messages) history.append(message);
  const pairs = workingContextModule.exchangeIndex(history);
  // Do not manufacture observations out of canceled, orphaned or duplicate
  // callback sequences. The same pairing contract protects durable commit.
  // Pure range-algebra callers can supply already decoded result-only samples.
  // Runtime adapters always supply a registry and must prove complete pairing.
  if (pairs.ambiguous && (tools.length || messages.some(m => m.getToolCallRequests().length))) return records;
  for (const [mi, message] of messages.entries()) {
    for (const [ri, result] of message.getToolCallResults().entries()) {
      const decoded = toolMemory.decodeToolResultRecord(result.content).value;
      const request = pairs.matches.get(`${mi}:${ri}`);
      const matches = tools.filter(t => t.name === request?.name);
      const trusted = request && matches.length === 1 && isObservationOnlyToolCall(matches[0], request);
      const archived = ["archived_tool_result_projection", "historical_evidence_range"].includes(String(decoded?.kind));
      records.push({ content: result.content, value: decoded && trusted && !archived ? {
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
  coverage: Array<Record<string, unknown>>;
};

export function observeRecoveryProgress(
  messages: Array<ChatMessage>,
  seen: Set<string>,
  coverageLedger: RecoveryCoverageLedger = new Map(),
  currentCoverage?: RecoveryCoverageLedger,
  tools: Array<RemoteToolLike> = [],
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
  const coverage: Array<Record<string, unknown>> = [];
  for (const result of observationRecords(messages, tools)) {
      const value = result.value;
      const fingerprint = telemetryFingerprint(result.content);
      const isNewResult = !seen.has(fingerprint);
      if (isNewResult) { seen.add(fingerprint); newResultCount += 1; }
      else duplicateCount += 1;
      if (!value || typeof value !== "object") { errorCount += 1; continue; }
      if (sourceObservationFailed(value)) { errorCount += 1; continue; }
      const descriptors = recoveryCoverageDescriptors(value);
      let resultAddedCoverage = false;
      for (const descriptor of descriptors) {
        const delta = recordRecoveryCoverage(coverageLedger, descriptor);
        if (currentCoverage && recordRecoveryCoverage(currentCoverage, descriptor).uncovered.length) {
          restoredWorkingEvidenceUnits++;
        }
        if (delta.uncovered.length) {
          resultAddedCoverage = true;
          if (descriptor.category === "source") newSourceUnits += 1;
          else newArchiveUnits += 1;
        } else if (delta.overlapped) partialOverlapCount += 1;
        coverage.push({
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
    newResultCount, newResponseFingerprintCount: newResultCount, newSuccessfulObservationCount,
    newSourceUnits, newArchiveUnits, currentInputAvailabilityGain, restoredWorkingEvidenceUnits,
    usefulProgress: progressed, progressed, errorCount, duplicateCount, partialOverlapCount, coverage
  };
}
