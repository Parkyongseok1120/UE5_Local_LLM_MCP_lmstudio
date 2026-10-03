import { createHash } from "node:crypto";
import type { Chat, ChatMessage, ToolCallRequest } from "@lmstudio/sdk";
import type { ToolScope } from "./tool-scope";
import { capabilityScope, type RemoteToolLike } from "./tool-capability-registry";
import { toolMemory } from "./context-ports";
import { fileObservation, pathApiFor, projectDescriptor, projectRoot } from "./continuity-file-observations.js";
import { sanitizeDerivedOperationalText } from "./durable-memory-sanitizer.js";
import { exchangeIndex } from "./tool-exchange-index.js";

/** All additional reference work is bounded here, independently of source
 * archival. These limits never grant an execution or context-budget allowance. */
export const REFERENCE_LIMITS = Object.freeze({ observations: 16, diagnosticGroups: 3, diagnostics: 12,
  metadata: 8, sections: 6, candidates: 4, decodeChars: 262144, textChars: 700, seenCalls: 2048 });
export type ReferenceSignal = "diagnostic_present" | "diagnostic_recurred" | "observed_source_changed"
  | "source_body_unavailable" | "compilation_pending" | "operation_outcome_unknown";
type Delivery = "returned" | "transport_error" | "canceled" | "unknown";
type OperationStatus = "accepted" | "pending" | "completed" | "failed" | "canceled" | "outcome_unknown" | "unknown";
type Origin = { provider: string; tool: string; executionId: string; toolCallId: string };
export type ReferenceItem = {
  id: string; origin: Origin; project: string; engine: "unity" | "unreal";
  kind: "diagnostics" | "operation" | "file" | "project";
  data: Record<string, unknown>;
};
type ObservedItem = ReferenceItem & {
  fileKey?: string; sourceBody?: boolean; engineVersion?: string; packages?: Record<string, string>;
  engineIdentity?: string;
  groupKey?: string; signature?: string; runId?: string; diagnosticCount?: number;
};
type Observation = { id: string; fingerprint: string; batch: number; items: ObservedItem[];
  session?: string; runtime: boolean; engine: "unity" | "unreal" };
export type ReferenceState = { observations: Observation[]; seen: Set<string>; disabled: boolean; batch: number };
export type ReferenceSnapshot = {
  items: ReferenceItem[]; omittedItems: number; signals: ReferenceSignal[];
  autoSignals?: Array<{ signal: ReferenceSignal; observationIds: string[]; order: "last_observation" | "order_unknown" }>;
  applicability: { engineVersion?: string; packages: Record<string, string>; networkPackagesAmbiguous: boolean };
};
export function emptyReferenceState(): ReferenceState { return { observations: [], seen: new Set(), disabled: false, batch: 0 }; }
export function emptyReferenceSnapshot(): ReferenceSnapshot {
  return { items: [], omittedItems: 0, signals: [], applicability: { packages: {}, networkPackagesAmbiguous: false } };
}
const record = (value: unknown): Record<string, any> | null => value && typeof value === "object" && !Array.isArray(value)
  ? value as Record<string, any> : null;
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
const text = (value: unknown, max: number = REFERENCE_LIMITS.textChars): string | undefined => typeof value === "string"
  ? sanitizeDerivedOperationalText(value).slice(0, max) : undefined;
const integer = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
const readFailureTool = (tool: string) => ["read_file", "read_file_range", "read_symbol"].includes(tool);
function pathIdentity(value: string) {
  const api = pathApiFor(value);
  if (!api.isAbsolute(value)) return "";
  const normalized = api.normalize(value).replace(/[\\/]+$/, "");
  return api.sep === "\\" ? normalized.toLowerCase() : normalized;
}
function provenProject(value: Record<string, any>, scope: ToolScope): string {
  const descriptor = projectDescriptor(value) || (record(value.project) ? value.project.projectPath : "");
  if (typeof descriptor !== "string" || descriptor.length > 4096) return "";
  const expected = pathIdentity(scope.projectIdentity), observed = pathIdentity(descriptor);
  if (!expected || !observed) return "";
  // A .uproject descriptor cannot silently match a different descriptor in the
  // same directory. Directory-vs-descriptor matching uses the existing root rule.
  const bothDescriptors = scope.projectIdentity.toLowerCase().endsWith(".uproject") && descriptor.toLowerCase().endsWith(".uproject");
  return (bothDescriptors ? expected === observed
    : pathIdentity(projectRoot(scope.projectIdentity)) === pathIdentity(projectRoot(descriptor))) ? descriptor : "";
}
function resultProject(value: Record<string, any>, scope: ToolScope): string {
  const direct = provenProject(value, scope);
  if (direct) return direct;
  // Bundle results carry canonical scope per changed file, not on the outer
  // envelope. Every constituent must prove the same execution scope.
  if (value.operation !== "bundle_applied" || !Array.isArray(value.files) || !value.files.length) return "";
  const projects = value.files.map((file: unknown) => record(file) ? provenProject(file as Record<string, any>, scope) : "");
  return projects.every(Boolean) ? projects[0] : "";
}
function originalResult(value: Record<string, any>) {
  return !["archived_tool_result_projection", "historical_evidence_index", "historical_evidence_range", "no_new_information"]
    .includes(String(value.kind || "")) && value.no_new_information !== true && !value.repeatReceipt
    && value.status !== "no_new_information";
}
/** Use the shared causal batch matcher, including consecutive request blocks
 * before the first result. Orphans and duplicate IDs are not facts. */
function pairedResults(messages: readonly ChatMessage[]) {
  const index = exchangeIndex([...messages]);
  const results: Array<{ request: ToolCallRequest; content: string }> = [];
  messages.forEach((message, mi) => message.getToolCallResults().forEach((result, ri) => {
    const request = index.matches.get(`${mi}:${ri}`);
    if (request && typeof result.content === "string") results.push({ request, content: result.content });
  }));
  return { pairs: results, duplicateIds: index.duplicateIds };
}
function deliveryStatus(value: Record<string, any>): { delivery: Delivery; operationStatus: OperationStatus } {
  if (value.status === "outcome_unknown") return { delivery: "transport_error", operationStatus: "outcome_unknown" };
  if (["canceled", "cancelled"].includes(value.status)) return { delivery: "canceled", operationStatus: "canceled" };
  if (value.timedOut === true) return { delivery: "returned", operationStatus: "outcome_unknown" };
  if (value.status === "accepted") return { delivery: "returned", operationStatus: "accepted" };
  if (["pending", "running", "compiling"].includes(value.status)) return { delivery: "returned", operationStatus: "pending" };
  if (value.ok === false || value.status === "failed") return { delivery: "returned", operationStatus: "failed" };
  if (value.ok === true || value.status === "completed") return { delivery: "returned", operationStatus: "completed" };
  return { delivery: "returned", operationStatus: "unknown" };
}
function fileMetadata(path: string, body: string, project: string): { engineVersion?: string; packages?: Record<string, string>; formatting?: Record<string, unknown> } {
  const normalized = path.replace(/\\/g, "/");
  const api = pathApiFor(project), root = projectRoot(project);
  if (pathIdentity(path) === pathIdentity(api.join(root, "ProjectSettings", "ProjectVersion.txt"))) {
    const version = /^m_EditorVersion:\s*(\d+\.\d+\.\d+[abfp]\d+)\s*$/m.exec(body)?.[1];
    return version ? { engineVersion: version } : {};
  }
  if (pathIdentity(path) === pathIdentity(api.join(root, "Packages", "packages-lock.json"))) {
    try {
      const parsed = record(JSON.parse(body.replace(/^\uFEFF/, ""))), packages: Record<string, string> = {};
      for (const [id, entry] of Object.entries(record(parsed?.dependencies) || {}).slice(0, 512)) {
        const version = record(entry)?.version;
        if (/^com\.unity\.[a-z0-9.-]+$/.test(id) && typeof version === "string" && /^\d+\.\d+\.\d+$/.test(version)) packages[id] = version;
      }
      return { packages };
    } catch { return {}; }
  }
  if (/\/(?:\.editorconfig|\.clang-format)$/.test(normalized)) {
    // Configuration excerpts only: section matching, inheritance and commands
    // are never executed or inferred from these single-file observations.
    const lines = body.split(/\r?\n/).filter(line => /^\s*(?:\[[^\]]+\]|(?:root|indent_style|indent_size|tab_width|end_of_line|charset|insert_final_newline|IndentWidth|UseTab|BasedOnStyle)\s*[:=])/i.test(line));
    return { formatting: { entries: lines.slice(0, 16).map(line => text(line, 180)), omittedEntries: Math.max(0, lines.length - 16),
      applicability: "single_observed_file; inheritance_and_overrides_unverified" } };
  }
  return {};
}

function normalize(value: Record<string, any>, origin: Origin, project: string, engine: "unity" | "unreal",
  request: ToolCallRequest): { items: ObservedItem[]; runtime: boolean; session?: string; invalidateFiles: boolean } {
  const items: ObservedItem[] = [];
  const add = (kind: ReferenceItem["kind"], data: Record<string, unknown>, extra: Partial<ObservedItem> = {}) => {
    items.push({ id: `${origin.executionId}:${origin.toolCallId}:${items.length}`, origin, project, engine, kind, data, ...extra });
  };
  const tool = origin.tool, args = record(request.arguments) || {};
  const session = typeof value.editorSessionId === "string" && integer(value.domainGeneration) !== undefined
    ? `${value.editorSessionId}:${value.domainGeneration}` : undefined;
  const status = deliveryStatus(value);
  let runtime = false, invalidateFiles = false;
  if (engine === "unreal" && tool === "unreal_symbol_lookup" && value.sourceMode === "engine_local") {
    const observed = record(value.observedEngineIdentity);
    const verified = value.ok === true && observed?.status === "observed" && typeof observed.engineRoot === "string"
      && pathIdentity(observed.engineRoot) && observed.hashScope === "file" && /^[a-f0-9]{64}$/.test(observed.sha256 || "")
      && /^\d+\.\d+(?:\.\d+)?$/.test(observed.version || "");
    const identity = verified ? JSON.stringify([pathIdentity(observed.engineRoot), observed.version, observed.sha256]) : undefined;
    add("project", { sourceMode: "engine_local", evidenceSource: text(value.evidenceSource),
      engineIdentityStatus: verified ? "observed" : "unknown", requestedEngineAssociation: text(value.requestedEngineAssociation),
      observedEngineRoot: verified ? text(observed.engineRoot) : undefined, observedEngineVersion: verified ? observed.version : undefined,
      coverage: record(value.coverage) ? { completeness: text(value.coverage.completeness), scope: text(value.coverage.scope) } : { completeness: "unknown" } },
      { engineVersion: verified ? observed.version : undefined, engineIdentity: identity,
        groupKey: JSON.stringify([pathIdentity(project), "unreal_engine_source"]) });
  } else if (engine === "unreal" && tool === "build_unreal_project" && record(value.project)) {
    const p = value.project;
    const raw = Array.isArray(value.diagnostics) ? value.diagnostics : [];
    const diagnostics = raw.slice(0, REFERENCE_LIMITS.diagnostics).flatMap(d => typeof d === "string" ? [text(d)] : []);
    add("diagnostics", { ...status, operationKind: "unreal_build", target: text(p.target), platform: text(p.platform),
      configuration: text(p.configuration), requestedEngineAssociation: text(p.engineAssociation), engineRoot: text(p.engineRoot),
      diagnostics, coverage: { scope: "producer_diagnostics", completeness: "partial_or_unknown",
        producerTruncated: value.diagnosticCoverage?.truncated ?? "unknown", omittedHere: Math.max(0, raw.length - diagnostics.length) },
      fullLogPath: text(value.fullLogPath), firstError: text(value.firstError),
      proof: { level: text(value.proof?.level), upToDate: value.proof?.upToDate === true,
        actionsExecuted: integer(value.proof?.actionsExecuted), sourceCompilationVerified: "unknown" } },
      { groupKey: JSON.stringify([pathIdentity(project), "build", p.target, p.platform, p.configuration,
          ...([p.target, p.platform, p.configuration].every(v => typeof v === "string" && v) ? [] : [origin.toolCallId])]),
        signature: diagnostics.length ? digest(JSON.stringify(diagnostics)) : undefined, runId: origin.toolCallId, diagnosticCount: diagnostics.length });
  } else if (engine === "unity" && tool === "unity_logs" && session) {
    runtime = true;
    const rows = Array.isArray(value.items) ? value.items : [];
    // Rows can belong to an earlier compilation than the response's current
    // outcome. Only matching session/compilation rows join this current group.
    const current = rows.filter(r => record(r) && r.source === "compiler" && r.editorSessionId === value.editorSessionId
      && typeof r.compilationId === "string" && r.compilationId === value.compilationId
      && (r.domainGeneration === undefined || r.domainGeneration === value.domainGeneration));
    const diagnostics = current.slice(0, REFERENCE_LIMITS.diagnostics).map(r => ({
      message: text(r.message), file: text(r.file), line: integer(r.line), column: integer(r.column),
      severity: text(r.severity), assembly: text(r.assembly), compilationId: text(r.compilationId),
      domainGeneration: integer(r.domainGeneration) ?? "unknown", sequence: integer(r.sequence),
      truncated: r.truncated === true || String(r.message || "").length > REFERENCE_LIMITS.textChars,
    }));
    add("diagnostics", { delivery: "returned", operationKind: "unity_script_compilation_observation",
      operationStatus: value.compilationOutcome === "compiling" ? "pending" : value.compilationOutcome === "failed" ? "failed" : "unknown",
      observedOutcome: text(value.compilationOutcome), compilationId: text(value.compilationId), session,
      diagnostics, sourceAssemblyVerification: "unknown", playerBuildVerification: "unknown",
      coverage: { scope: text(value.collectionScope), completeness: "partial", subscribedAt: text(value.subscribedAt),
        droppedCount: integer(value.droppedCount), producerTruncated: value.truncated === true,
        excludedRows: rows.length - current.length, omittedHere: current.length - diagnostics.length,
        rowGenerationVerification: "unknown_when_not_supplied" } },
      { groupKey: JSON.stringify([pathIdentity(project), "unity_compilation", session]), runId: text(value.compilationId),
        signature: diagnostics.length ? digest(JSON.stringify(diagnostics.map(({ sequence, compilationId, ...d }) => d))) : undefined,
        diagnosticCount: diagnostics.length });
  } else if (engine === "unity" && tool === "unity_editor" && args.action === "compile") {
    runtime = true;
    add("operation", { ...status, operationKind: "unity_script_compilation_request", session: session || "unknown",
      compilationIdAtRequest: text(value.compilationIdAtRequest), operationId: text(value.operationId),
      sourceAssemblyVerification: "unknown", playerBuildVerification: "unknown" });
  } else if (engine === "unity" && tool === "unity_status") {
    runtime = true;
    add("project", { connection: text(value.connection), session: session || "unknown", editorVersion: text(value.editorVersion),
      compiling: typeof value.compiling === "boolean" ? value.compiling : "unknown", compilationId: text(value.compilationId) },
      { engineVersion: session ? text(value.editorVersion, 64) : undefined, groupKey: JSON.stringify([pathIdentity(project), "unity_status"]) });
  } else if (value.status === "outcome_unknown") {
    runtime = engine === "unity";
    add("operation", { ...status, operationKind: "tool_transport", errorCode: text(value.errorCode), operationId: text(value.operationId) });
  }
  const fileTools = engine === "unreal" ? ["read_file", "read_file_range", "read_symbol", "write_file", "replace_in_file", "apply_edit_bundle", "delete_file"]
    : ["read_file", "patch_file", "create_file", "structured_data_patch"];
  if (fileTools.includes(tool)) {
    const files = Array.isArray(value.files) ? value.files : [value];
    // Oversized/partial mutation results cannot leave earlier configuration
    // assertions active. A subsequent real read is needed to recover them.
    invalidateFiles = files.length > REFERENCE_LIMITS.metadata || value.status === "partially_applied"
      || value.errorCode === "ROLLBACK_INCOMPLETE"
      || ["FILE_VERSION_CONFLICT", "receipt_conflict"].includes(value.errorCode) && files.every((f: unknown) => typeof record(f)?.path !== "string");
    for (const raw of files.slice(0, REFERENCE_LIMITS.metadata)) {
      if (!record(raw) || typeof raw.path !== "string") continue;
      const descriptor = projectDescriptor(raw, project);
      if (!provenProject({ canonicalProject: descriptor }, { projectIdentity: project } as ToolScope)) { invalidateFiles = true; continue; }
      const file = fileObservation({ ...raw, sha256: raw.sha256 || raw.hash }, project, value.operation);
      if (!file) continue;
      if ((value.ok === false || value.errorCode) && readFailureTool(tool)) {
        file.observationState = "unavailable";
        delete file.sha256AtObservation; delete file.observedLineRanges; delete file.readCoverageState;
      } else if ((value.ok === false || value.errorCode) && file.observationState !== "conflict_observed") continue;
      const fileKey = pathIdentity(String(file.canonicalPath));
      const body = typeof raw.content === "string" ? raw.content : typeof raw.text === "string" ? raw.text : undefined;
      const read = ["read_file", "read_file_range", "read_symbol"].includes(tool) && file.observationState === "observed";
      const completeBytes = integer(raw.offsetBytes) === 0 && integer(raw.size) !== undefined
        && integer(raw.nextOffsetBytes) === raw.size;
      const complete = read && (file.readCoverageState === "complete" || completeBytes) && file.sha256AtObservation
        && raw.truncated !== true && raw.hasMore !== true
        && raw.pageHasMore !== true && body !== undefined && body.length <= REFERENCE_LIMITS.decodeChars;
      const metadata = complete ? fileMetadata(String(file.canonicalPath), body!, project) : {};
      // Unity project/package formats must not assert an Unreal version merely
      // because an Unreal project contains a similarly named sample file.
      const { formatting } = metadata;
      const engineVersion = engine === "unity" ? metadata.engineVersion : undefined;
      const packages = engine === "unity" ? metadata.packages : undefined;
      add("file", { path: text(file.canonicalPath, 4096), hash: text(file.sha256AtObservation, 128),
        observationState: file.observationState, ranges: file.observedLineRanges || [],
        coverage: file.readCoverageState || "unknown", engineVersion, packages, formatting,
        validity: "last_observation_only", editReceipt: "not_provided" },
        { fileKey, sourceBody: read && body !== undefined, engineVersion, packages });
    }
  }
  return { items, runtime, session, invalidateFiles };
}

/** Called only for newly returned execution messages, never for model notes,
 * checkpoints or historical replay. State is owned by one EvidenceManager. */
export function ingestReferenceObservations(state: ReferenceState, messages: readonly ChatMessage[],
  tools: readonly RemoteToolLike[], scope: ToolScope, executionId: string): void {
  if (state.disabled) return;
  const batch = ++state.batch;
  const batchFiles = new Set<string>(), ambiguousFiles = new Set<string>();
  const batchSessions = new Set<string>();
  let batchFileUncertainty = false, batchSessionUncertainty = false;
  const invalidateFileFacts = () => {
    batchFileUncertainty = true;
    state.observations = state.observations.map(o => ({ ...o, items: o.items.filter(i => !i.fileKey) })).filter(o => o.items.length);
  };
  const { pairs, duplicateIds } = pairedResults(messages);
  for (const id of duplicateIds) {
    if (state.seen.size >= REFERENCE_LIMITS.seenCalls) { state.observations = []; state.disabled = true; return; }
    state.observations = state.observations.filter(o => o.id !== id);
    state.seen.add(id);
  }
  for (const { request, content } of pairs) {
    const id = String(request.id || "");
    if (state.seen.has(id)) { state.observations = state.observations.filter(o => o.id !== id); continue; }
    if (state.seen.size >= REFERENCE_LIMITS.seenCalls) { state.observations = []; state.disabled = true; return; }
    state.seen.add(id);
    const matches = tools.filter(tool => tool.name === request.name);
    if (matches.length !== 1) continue;
    const tool = matches[0], engine = capabilityScope(tool);
    if (engine === "common" || scope.engine !== engine || ["available_tools", "ambiguous"].includes(scope.source)) continue;
    if (content.length > REFERENCE_LIMITS.decodeChars) { state.observations = []; batchFileUncertainty = true; continue; }
    const value = record(toolMemory.decodeToolResultRecord(content).value);
    if (!value || !originalResult(value)) continue;
    const project = resultProject(value, scope);
    if (!project) {
      // Older mutation adapters report a real version conflict without a
      // canonical target. Invalidate prior file-derived assertions rather than
      // guessing a target from pre-binding model arguments. No fresh fact added.
      if (["FILE_VERSION_CONFLICT", "receipt_conflict", "ROLLBACK_INCOMPLETE"].includes(value.errorCode)
        || value.status === "partially_applied") {
        invalidateFileFacts();
      }
      continue;
    }
    const normalized = normalize(value, { provider: tool.pluginIdentifier!, tool: request.name, executionId, toolCallId: id }, project, engine, request);
    if (normalized.invalidateFiles) invalidateFileFacts();
    if (normalized.runtime && normalized.session) batchSessions.add(normalized.session);
    if (batchSessions.size > 1) batchSessionUncertainty = true;
    if (batchSessionUncertainty) {
      state.observations = state.observations.filter(o => !o.runtime);
      if (normalized.runtime) continue;
    }
    if (normalized.runtime && normalized.session) {
      state.observations = state.observations.filter(o => !o.runtime || o.session === normalized.session);
    } else if (normalized.runtime && request.name === "unity_status") {
      state.observations = state.observations.filter(o => !o.runtime);
    }
    const touched = new Set(normalized.items.flatMap(i => i.fileKey ? [i.fileKey] : []));
    for (const key of touched) {
      if (batchFiles.has(key)) ambiguousFiles.add(key);
      batchFiles.add(key);
    }
    state.observations = state.observations.map(o => ({ ...o, items: o.items.filter(i => !i.fileKey
      || !normalized.invalidateFiles && !touched.has(i.fileKey)) })).filter(o => o.items.length);
    // A captured batch may contain parallel calls. Arrival order proves neither
    // file version order nor that a read followed a mutation. Drop both views.
    normalized.items = normalized.items.filter(i => !i.fileKey || !batchFileUncertainty && !ambiguousFiles.has(i.fileKey));
    if (normalized.items.length) state.observations.push({ id, fingerprint: digest(content), batch,
      items: normalized.items, runtime: normalized.runtime, session: normalized.session, engine });
    state.observations = state.observations.slice(-REFERENCE_LIMITS.observations);
  }
}

/** Re-evaluate availability against this round's actual source history.
 * References are never receipts, fresh reads or a second historical archive. */
export function referenceSnapshot(state: ReferenceState, history: Chat): ReferenceSnapshot {
  const snapshot = emptyReferenceSnapshot();
  if (!state.observations.length) return snapshot;
  const available = new Map(pairedResults(history.getMessagesArray()).pairs.map(p => [p.request.id, digest(p.content)]));
  const signals = new Set<ReferenceSignal>(), groups = new Set<string>();
  let diagnosticGroups = 0, diagnosticEntries = 0, metadata = 0;
  const ordered = [...state.observations].reverse();
  const localEngineObservations = ordered.filter(o => o.items.some(i => i.data.sourceMode === "engine_local"));
  const engineBatch = localEngineObservations[0]?.batch;
  const currentEngineIdentities = localEngineObservations.filter(o => o.batch === engineBatch)
    .flatMap(o => o.items.filter(i => i.data.sourceMode === "engine_local").map(i => i.engineIdentity));
  const ambiguousEngine = currentEngineIdentities.some(i => !i) || new Set(currentEngineIdentities).size > 1;
  const autoSignals: NonNullable<ReferenceSnapshot["autoSignals"]> = [];
  for (const observation of ordered) {
    const present = available.get(observation.id) === observation.fingerprint;
    for (const item of observation.items) {
      if (item.groupKey && groups.has(item.groupKey)) continue;
      if (item.groupKey) groups.add(item.groupKey);
      if (item.kind === "diagnostics") {
        if (++diagnosticGroups > REFERENCE_LIMITS.diagnosticGroups) { snapshot.omittedItems++; continue; }
      } else if (++metadata > REFERENCE_LIMITS.metadata) { snapshot.omittedItems++; continue; }
      let data = { ...item.data, returnedBodyInCurrentInput: present, hostInputVerification: "unknown" } as Record<string, any>;
      const sameWork = (o: Observation) => o.items.some(i => item.groupKey ? i.groupKey === item.groupKey
        : i.origin.tool === item.origin.tool && i.kind === item.kind);
      const orderKnown = ordered.filter(o => o.batch === observation.batch && sameWork(o)).length === 1
        && !ordered.some(o => o.batch > observation.batch && sameWork(o));
      const laterUnityCompile = observation.runtime && ordered.some(o => o.batch >= observation.batch
        && o !== observation && o.runtime && o.session === observation.session && o.items.some(i =>
          ["project", "diagnostics"].includes(i.kind) && (o.batch === observation.batch
            || !item.runId || i.data.compilationId !== item.runId)));
      const autoSignal = (signal: ReferenceSignal) => {
        if (!orderKnown || laterUnityCompile) return;
        const existing = autoSignals.find(s => s.signal === signal);
        if (existing) existing.observationIds = [...new Set([...existing.observationIds, item.id])].slice(0, 4);
        else autoSignals.push({ signal, observationIds: [item.id], order: "last_observation" });
      };
      if (item.kind === "diagnostics") {
        const diagnostics = Array.isArray(data.diagnostics) ? data.diagnostics : [];
        const shown = diagnostics.slice(0, REFERENCE_LIMITS.diagnostics - diagnosticEntries);
        diagnosticEntries += shown.length;
        data = { ...data, diagnostics: shown, coverage: { ...data.coverage, omittedBySnapshot: diagnostics.length - shown.length } };
        if (shown.length) { signals.add("diagnostic_present"); autoSignal("diagnostic_present"); }
        const peers = ordered.filter(o => o.batch === observation.batch && o.items.some(p => p.groupKey === item.groupKey));
        if (peers.length > 1) data.ordering = "multiple_results_in_same_batch; latest_run_unknown";
        const previous = ordered.find(o => o.batch < observation.batch && o.items.some(p => p.groupKey === item.groupKey));
        const prior = previous?.items.find(p => p.groupKey === item.groupKey);
        if (peers.length === 1 && item.signature && prior?.signature === item.signature && item.runId && prior.runId !== item.runId) {
          signals.add("diagnostic_recurred");
          autoSignal("diagnostic_recurred");
          data.recurrence = { previousToolCallId: prior.origin.toolCallId, comparedScope: "bounded_displayed_diagnostics",
            sameRootCause: "unknown", mutationCausality: "unknown" };
        }
      }
      const laterCompilationObservation = observation.runtime && ordered.some(o => o.batch > observation.batch
        && o.runtime && o.session === observation.session && o.items.some(i => i.kind === "project" || i.kind === "diagnostics"));
      if (laterCompilationObservation && (["pending", "accepted"].includes(String(data.operationStatus)) || data.compiling === true)) {
        data.currentCompilationStatus = "not_inferred_from_prior_acceptance_or_status";
      } else if (data.operationStatus === "pending" || data.operationStatus === "accepted" || data.compiling === true) {
        signals.add("compilation_pending");
        autoSignal("compilation_pending");
      }
      if (data.operationStatus === "outcome_unknown") { signals.add("operation_outcome_unknown"); autoSignal("operation_outcome_unknown"); }
      if (item.kind === "file") {
        data.sourceBodyInCurrentInput = Boolean(present && item.sourceBody);
        if (!data.sourceBodyInCurrentInput) signals.add("source_body_unavailable");
        if (["modified", "deleted", "conflict_observed"].includes(String(data.observationState))) signals.add("observed_source_changed");
      }
      if (item.engineVersion && !snapshot.applicability.engineVersion && !(item.engine === "unreal" && ambiguousEngine))
        snapshot.applicability.engineVersion = item.engineVersion;
      if (item.packages) snapshot.applicability.packages = { ...item.packages };
      snapshot.items.push({ id: item.id, origin: item.origin, project: item.project, engine: item.engine, kind: item.kind, data });
    }
  }
  const packages = snapshot.applicability.packages;
  snapshot.applicability.networkPackagesAmbiguous = Boolean(packages["com.unity.netcode.gameobjects"] && packages["com.unity.netcode"]);
  snapshot.signals = [...signals];
  snapshot.autoSignals = autoSignals;
  return snapshot;
}
