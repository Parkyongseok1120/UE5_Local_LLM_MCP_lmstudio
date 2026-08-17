"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const {
  filesystemPathIdentity,
  normalizePortablePath,
} = require("./filesystem-path-identity");
const { deriveSynthesisReadiness } = require("./synthesis-readiness");

const SOURCE_EVIDENCE_TASK_KINDS = new Set([
  "cpp_analysis",
  "inspect_only",
  "project_review",
  "source_analysis",
]);

function isSourceEvidenceTask(state = {}) {
  const taskKind = String(state.taskKind || "").trim().toLowerCase();
  if (SOURCE_EVIDENCE_TASK_KINDS.has(taskKind)) return true;
  const contract = state.inspectionContract && typeof state.inspectionContract === "object"
    ? state.inspectionContract
    : {};
  if (SOURCE_EVIDENCE_TASK_KINDS.has(String(contract.intent || "").trim().toLowerCase())) return true;
  const repositoryAudit = state.repoAuditLedger && typeof state.repoAuditLedger === "object"
    ? state.repoAuditLedger
    : {};
  return repositoryAudit.required === true;
}

const DISCOVERY_TOOLS = new Set([
  "unreal_rag_search",
  "unreal_symbol_lookup",
  "list_directory",
  "search_files",
  "read_file",
  "read_file_range",
  "read_symbol",
  "read_unreal_logs",
]);
const REPEATED_GATE_REDISCOVERY_TOOLS = new Set([
  "list_directory",
  "search_files",
  "read_file",
  "read_file_range",
  "read_symbol",
  "read_unreal_logs",
]);

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, stable(value[key])])
  );
}

function canonicalHash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

function cleanStrings(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((item) => String(item || "").trim()).filter(Boolean))];
}

function nonNegativeInt(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
}

function normalizePath(value) {
  return normalizePortablePath(value, { trimOuterSlashes: true });
}

function transitionPathIdentity(value, hostPlatform = process.platform) {
  return filesystemPathIdentity(value, hostPlatform, { trimOuterSlashes: true });
}

function authoritativeProjectRoot(state) {
  const workspaceRoot = String(state.workspaceRoot || "").trim();
  const routeScope = state.routeScope && typeof state.routeScope === "object"
    ? state.routeScope
    : {};
  const rawProject = String(routeScope.projectFile || state.projectFile || "").trim();
  if (rawProject) {
    const projectBase = String(
      routeScope.workspaceRoot || workspaceRoot || ""
    ).trim();
    const resolvedProject = path.resolve(
      !path.isAbsolute(rawProject) && projectBase
        ? path.join(projectBase, rawProject)
        : rawProject
    );
    return path.extname(resolvedProject).toLowerCase() === ".uproject"
      ? path.dirname(resolvedProject)
      : resolvedProject;
  }
  return workspaceRoot ? path.resolve(workspaceRoot) : "";
}

function authoritativeProjectFile(state) {
  const workspaceRoot = String(state.workspaceRoot || "").trim();
  const routeScope = state.routeScope && typeof state.routeScope === "object"
    ? state.routeScope
    : {};
  const rawProject = String(routeScope.projectFile || state.projectFile || "").trim();
  if (!rawProject) return "";
  const projectBase = String(routeScope.workspaceRoot || workspaceRoot || "").trim();
  const resolvedProject = path.resolve(
    !path.isAbsolute(rawProject) && projectBase
      ? path.join(projectBase, rawProject)
      : rawProject
  );
  return path.extname(resolvedProject).toLowerCase() === ".uproject"
    ? resolvedProject
    : "";
}

function mutationToolForState(state, route, hostPlatform = process.platform) {
  const selectedSlice = route.selectedSlice && typeof route.selectedSlice === "object"
    ? route.selectedSlice
    : {};
  const files = cleanStrings(selectedSlice.files);
  if (!files.length) return "";
  if (files.length > 1) return "apply_edit_bundle";
  const selected = transitionPathIdentity(files[0], hostPlatform);
  const selectedSnapshots = Array.isArray(state.selectedTargetSnapshots)
    ? state.selectedTargetSnapshots
    : [];
  const snapshots = selectedSnapshots.length
    ? selectedSnapshots
    : (Array.isArray(state.featureTargetSnapshots) ? state.featureTargetSnapshots : []);
  const snapshot = snapshots.find((item) => (
    item && typeof item === "object"
    && transitionPathIdentity(item.path || item.relativePath, hostPlatform) === selected
  ));
  if (!snapshot) return "apply_edit_bundle";
  return snapshot.exists === true ? "replace_in_file" : "write_file";
}

function failedGateAttemptForCurrentScope(state, gate) {
  const attempt = state.failedGateAttempts?.[gate]
    && typeof state.failedGateAttempts[gate] === "object"
    ? state.failedGateAttempts[gate]
    : {};
  if (!Object.keys(attempt).length) return {};
  if (!["gateSetHash", "planRevision", "activeSliceId", "mutationGeneration"]
    .every((field) => Object.prototype.hasOwnProperty.call(attempt, field))) return {};
  return (
    String(attempt.gateSetHash || "") === String(state.requiredGateSetHash || "")
    && String(attempt.planRevision || "") === String(state.planRevision || "")
    && String(attempt.activeSliceId || "") === String(state.activeSliceId || "")
    && nonNegativeInt(attempt.mutationGeneration) === nonNegativeInt(state.mutationGeneration)
  ) ? attempt : {};
}

function completedSketchForScope(state) {
  const completed = state.completedGates && typeof state.completedGates === "object"
    ? state.completedGates
    : {};
  const record = completed.unreal_code_sketch_claim_validate;
  if (!record || typeof record !== "object" || record.status !== "completed") return null;
  if (String(record.gateSetHash || "") !== String(state.requiredGateSetHash || "")) return null;
  if (String(record.planRevision || "") !== String(state.planRevision || "")) return null;
  if (String(record.activeSliceId || "") !== String(state.activeSliceId || "")) return null;
  return record;
}

function preGateSourceReadPath(state, pendingGates, hostPlatform = process.platform) {
  if (!pendingGates.length || pendingGates[0] !== "unreal_code_sketch_claim_validate") return "";
  if (state.writeGate?.mustReadBeforeWrite !== true) return "";
  const evidenceFiles = state.directSourceEvidence?.files && typeof state.directSourceEvidence.files === "object"
    ? state.directSourceEvidence.files
    : {};
  const evidencePaths = new Set(Object.entries(evidenceFiles)
    .filter(([, item]) => item && typeof item === "object")
    .map(([key, item]) => transitionPathIdentity(item.path || key, hostPlatform)));
  const snapshots = Array.isArray(state.selectedTargetSnapshots) ? state.selectedTargetSnapshots : [];
  for (const snapshot of snapshots) {
    if (!snapshot || typeof snapshot !== "object" || snapshot.exists !== true) continue;
    const targetPath = normalizePath(snapshot.path || snapshot.relativePath);
    if (targetPath && !evidencePaths.has(transitionPathIdentity(targetPath, hostPlatform))) return targetPath;
  }
  return "";
}

const SOURCE_DECLARATION_EXTENSIONS = new Set([".h", ".hpp", ".inl"]);
const SOURCE_IMPLEMENTATION_EXTENSIONS = new Set([".cpp", ".c", ".cc", ".cxx"]);

function usableSourcePath(value) {
  const candidate = normalizePath(value);
  if (!candidate || candidate.includes("://") || candidate.split("/").includes("..")) return "";
  return candidate;
}

function sourceEvidenceRows(state) {
  const planRevision = String(state.planRevision || "");
  const rows = [];
  for (const field of ["sourceEvidence", "directSourceEvidence"]) {
    const ledger = state[field] && typeof state[field] === "object" ? state[field] : {};
    if (String(ledger.planRevision || "") !== planRevision) continue;
    const files = ledger.files && typeof ledger.files === "object" ? ledger.files : {};
    for (const [key, raw] of Object.entries(files)) {
      if (!raw || typeof raw !== "object") continue;
      const sourcePath = usableSourcePath(raw.path || key);
      if (sourcePath) rows.push({ ...raw, path: sourcePath });
    }
  }
  return rows;
}

function sourceAbsentPaths(state) {
  const ledger = state.absentEvidence && typeof state.absentEvidence === "object"
    ? state.absentEvidence
    : {};
  const files = ledger.files && typeof ledger.files === "object" ? ledger.files : {};
  return new Set(Object.entries(files).map(([key, raw]) => (
    transitionPathIdentity(usableSourcePath(raw?.path || key))
  )).filter(Boolean));
}

function sourceRecoveryCandidates(state) {
  const progress = state.inspectionProgress && typeof state.inspectionProgress === "object"
    ? state.inspectionProgress
    : {};
  const values = [];
  for (const container of [progress, state]) {
    for (const key of [
      "remainingFrontier", "discoveredCandidates", "discoveryCandidates",
      "knownSourceCandidates", "sourceCandidates", "pairCandidates", "declarationCandidates",
    ]) {
      const raw = container[key];
      for (const value of (Array.isArray(raw) ? raw.slice(0, 64) : [raw])) {
        const candidate = usableSourcePath(
          value && typeof value === "object"
            ? value.path || value.relativePath || value.projectRelativePath
            : value,
        );
        if (candidate) values.push(candidate);
      }
    }
  }
  const discovery = state.inspectionDiscovery && typeof state.inspectionDiscovery === "object"
    ? state.inspectionDiscovery
    : {};
  for (const key of ["candidates", "paths", "files", "remainingFrontier"]) {
    for (const value of (Array.isArray(discovery[key]) ? discovery[key].slice(0, 64) : [])) {
      const candidate = usableSourcePath(value?.path || value?.relativePath || value);
      if (candidate) values.push(candidate);
    }
  }
  return [...new Set(values)].slice(0, 64);
}

function sourcePairHeaderCandidates(value) {
  const sourcePath = usableSourcePath(value);
  const suffix = path.posix.extname(sourcePath).toLowerCase();
  if (!sourcePath || !SOURCE_IMPLEMENTATION_EXTENSIONS.has(suffix)) return [];
  const stem = sourcePath.slice(0, -suffix.length);
  const parts = stem.split("/").filter(Boolean);
  const candidates = [];
  const add = (candidate) => {
    const normalized = usableSourcePath(candidate);
    if (normalized && !candidates.includes(normalized)) candidates.push(normalized);
  };
  const sourceIndex = parts.map((part) => part.toLowerCase()).lastIndexOf("source");
  if (sourceIndex >= 0 && parts.length > sourceIndex + 2) {
    const moduleRoot = parts.slice(0, sourceIndex + 2);
    let relative = parts.slice(sourceIndex + 2);
    if (["public", "private", "classes"].includes(String(relative[0] || "").toLowerCase())) {
      relative = relative.slice(1);
    }
    if (relative.length) {
      for (const directory of ["Public", "Classes", ""]) {
        const prefix = directory ? [...moduleRoot, directory] : moduleRoot;
        for (const declarationSuffix of [".h", ".hpp", ".inl"]) {
          add([...prefix, ...relative].join("/") + declarationSuffix);
        }
      }
    }
  }
  const parent = sourcePath.includes("/") ? sourcePath.slice(0, sourcePath.lastIndexOf("/")) : "";
  const basename = path.posix.basename(stem);
  for (const declarationSuffix of [".h", ".hpp", ".inl"]) {
    add(parent ? `${parent}/${basename}${declarationSuffix}` : `${basename}${declarationSuffix}`);
  }
  return candidates.slice(0, 24);
}

function nextEvidenceRecovery(state, readiness) {
  if (readiness?.ready === true) return null;
  const rows = sourceEvidenceRows(state);
  const accepted = new Set(rows.map((row) => transitionPathIdentity(row.path)).filter(Boolean));
  const absent = sourceAbsentPaths(state);
  const declaration = (value) => {
    const candidate = usableSourcePath(value);
    const suffix = path.posix.extname(candidate).toLowerCase();
    if (!candidate || !SOURCE_DECLARATION_EXTENSIONS.has(suffix)) return "";
    const identity = transitionPathIdentity(candidate);
    return !accepted.has(identity) && !absent.has(identity) ? candidate : "";
  };
  for (const candidate of sourceRecoveryCandidates(state)) {
    const header = declaration(candidate);
    if (header) return { name: "read_file", args: { path: header } };
  }
  const implementations = rows.filter((row) => (
    String(row.sourceKind || "").toLowerCase() === "implementation"
      || SOURCE_IMPLEMENTATION_EXTENSIONS.has(path.posix.extname(String(row.path || "")).toLowerCase())
  ));
  for (const row of implementations) {
    for (const key of [
      "includePath", "headerPath", "declarationPath", "includedHeader", "includedHeaders",
      "includePaths", "pairCandidates", "declarationCandidates",
    ]) {
      const values = Array.isArray(row[key]) ? row[key] : [row[key]];
      for (const value of values) {
        const header = declaration(value?.path || value?.relativePath || value);
        if (header) return { name: "read_file", args: { path: header } };
      }
    }
    for (const candidate of sourcePairHeaderCandidates(row.path)) {
      const header = declaration(candidate);
      if (!header) continue;
      const root = authoritativeProjectRoot(state);
      if (root && fs.existsSync(path.join(root, ...header.split("/")))) {
        return { name: "read_file", args: { path: header } };
      }
    }
    const sourcePath = usableSourcePath(row.path);
    if (sourcePath) {
      const basename = path.posix.basename(sourcePath, path.posix.extname(sourcePath));
      const parts = sourcePath.split("/");
      const sourceIndex = parts.map((part) => part.toLowerCase()).lastIndexOf("source");
      const searchPath = sourceIndex >= 0 && parts.length > sourceIndex + 1
        ? parts.slice(0, sourceIndex + 2).join("/")
        : "Source";
      return {
        name: "search_files",
        args: {
          query: `${basename}.h`,
          path: searchPath,
          regex: false,
          matchFileNames: true,
          maxResults: 8,
        },
      };
    }
  }
  const progress = state.inspectionProgress && typeof state.inspectionProgress === "object"
    ? state.inspectionProgress
    : {};
  const frontier = Array.isArray(progress.remainingFrontier)
    ? progress.remainingFrontier
    : (Array.isArray(state.remainingFrontier) ? state.remainingFrontier : []);
  for (const value of frontier) {
    const candidate = usableSourcePath(value);
    const identity = transitionPathIdentity(candidate);
    if (candidate && !accepted.has(identity) && !absent.has(identity)) {
      return { name: "read_file", args: { path: candidate } };
    }
  }
  return null;
}

function prepareSynthesisHandoff(state, readiness) {
  if (
    readiness?.ready !== true
    || !isSourceEvidenceTask(state)
    || String(state.status || "running").toLowerCase() !== "running"
    || String(state.mode || "").toLowerCase() !== "read_only"
  ) return false;
  const route = state.toolRoute && typeof state.toolRoute === "object" ? state.toolRoute : {};
  if (Array.isArray(route.pendingGates) && route.pendingGates.length > 0) return false;
  const recovery = state.recoveryObligation && typeof state.recoveryObligation === "object"
    ? state.recoveryObligation
    : {};
  const recoveryStatus = String(recovery.status || "").toLowerCase();
  if (!["", "evidence_complete"].includes(recoveryStatus)) return false;
  if (String(route.phase || "").toLowerCase() === "synthesis") return false;
  const action = state.postBudgetAction && typeof state.postBudgetAction === "object"
    ? state.postBudgetAction
    : {};
  if (String(action.name || "") === "synthesize_current_evidence") return false;
  if (!recoveryStatus) {
    state.recoveryObligation = {
      source: "evidence",
      status: "evidence_complete",
      scopeDisposition: "in_slice",
      errorCode: "EVIDENCE_COMPLETE",
      requiredTool: {},
      targetFiles: [],
    };
  }
  state.postBudgetAction = {
    name: "synthesize_current_evidence",
    isTool: false,
    controlEpoch: nonNegativeInt(state.controlEpoch),
    planRevision: String(state.planRevision || ""),
    acceptedEvidenceHash: String(readiness.acceptedEvidenceHash || ""),
    remainingFrontierHash: String(readiness.remainingFrontierHash || ""),
    remainingFrontierRequired: readiness.coverageIncomplete === true,
    coverageIncomplete: readiness.coverageIncomplete === true,
  };
  state.toolRoute = {
    ...route,
    phase: "synthesis",
    roleSession: "synthesis",
    activeTools: [],
    maxToolCallsPerPhase: 0,
  };
  return true;
}

function validationFindingRecovery(firstFinding) {
  const finding = firstFinding && typeof firstFinding === "object" ? firstFinding : {};
  const targetPath = normalizePath(finding.path);
  const line = nonNegativeInt(finding.line);
  if (targetPath) {
    if (line > 0) {
      return {
        status: "evidence_required",
        scopeDisposition: "in_slice",
        requiredTool: {
          name: "read_file_range",
          args: {
            path: targetPath,
            startLine: Math.max(1, line - 20),
            endLine: line + 20,
          },
        },
        targetFiles: [targetPath],
      };
    }
    return {
      status: "evidence_required",
      scopeDisposition: "in_slice",
      requiredTool: { name: "read_file", args: { path: targetPath } },
      targetFiles: [targetPath],
    };
  }
  const symbol = String(
    finding.symbol || finding.ownerSymbol || finding.missingSymbol || ""
  ).trim();
  if (symbol) {
    return {
      status: "evidence_required",
      scopeDisposition: "in_slice",
      requiredTool: {
        name: "unreal_symbol_lookup",
        args: { query: symbol, access: "read" },
      },
      targetFiles: [],
    };
  }
  const rawLog = String(
    finding.buildLogPath || finding.logPath || finding.logFile || ""
  ).trim();
  const diagnosticSource = String(finding.diagnosticSource || "").toLowerCase();
  if (rawLog || ["build", "automation", "ubt", "uat", "log"].includes(diagnosticSource)) {
    const logArgs = {
      mode: "first_error",
      maxFiles: 1,
      maxLines: 200,
      summaryOnly: true,
    };
    if (rawLog) logArgs.fileName = path.posix.basename(rawLog.replace(/\\/g, "/"));
    return {
      status: "evidence_required",
      scopeDisposition: "infrastructure",
      requiredTool: { name: "read_unreal_logs", args: logArgs },
      targetFiles: [],
    };
  }
  return {
    status: "checkpoint_rebase_required",
    scopeDisposition: "in_slice",
    requiredTool: {
      name: "unreal_task_checkpoint",
      args: {
        action: "rebase",
        acceptCurrentFiles: true,
        includeGitChanges: false,
      },
    },
    targetFiles: [],
  };
}

function deriveNextObligation(state) {
  const synthesisReadiness = deriveSynthesisReadiness(state);
  state.synthesisReadiness = synthesisReadiness;
  prepareSynthesisHandoff(state, synthesisReadiness);
  const route = state.toolRoute && typeof state.toolRoute === "object" ? state.toolRoute : {};
  const status = String(state.status || "running").trim().toLowerCase();
  const phase = String(route.phase || "unknown");
  const activeTools = cleanStrings(route.activeTools);
  const pendingGates = cleanStrings(route.pendingGates);
  let requiredName = "";
  let requiredArgs = {};
  let disposition = "continue";
  let retryValue = "allowed";
  let blocker = null;
  let discoveryOnly = false;
  let noToolsForSynthesis = false;

  if (status === "completed") disposition = "complete";
  else if (["cancelled", "failed", "cancellation_uncertain"].includes(status)) disposition = "workflow_stop";
  else if (["pending_approval", "awaiting_approval"].includes(status)) disposition = "await_user";
  else if (status === "running") {
    const recoveryObligation = state.recoveryObligation
      && typeof state.recoveryObligation === "object"
      ? state.recoveryObligation
      : {};
    const buildRecovery = state.buildRecovery && typeof state.buildRecovery === "object" ? state.buildRecovery : {};
    const buildVerification = state.buildVerification && typeof state.buildVerification === "object" ? state.buildVerification : {};
    const checkpoint = state.continuity?.checkpoint && typeof state.continuity.checkpoint === "object"
      ? state.continuity.checkpoint
      : {};
    const preGateReadPath = preGateSourceReadPath(state, pendingGates);
    const taskKind = String(state.taskKind || "").trim().toLowerCase();
    const initialCompileDiagnostic = (
      ["compile_fix", "reflection_fix", "module_fix"].includes(taskKind)
      && pendingGates.length > 0
      && nonNegativeInt(state.mutationGeneration) === 0
      && Object.keys(buildRecovery).length === 0
      && !(
        state.buildBlocker
        && typeof state.buildBlocker === "object"
        && Object.keys(state.buildBlocker).length > 0
      )
      && !(Array.isArray(state.buildProofHistory) && state.buildProofHistory.length)
    );
    const recoveryStatus = String(recoveryObligation.status || "").trim().toLowerCase();
    const recoveryTool = recoveryObligation.requiredTool
      && typeof recoveryObligation.requiredTool === "object"
      ? recoveryObligation.requiredTool
      : {};
    const recoveryToolName = String(recoveryTool.name || "").trim();
    const recoveryToolArgs = recoveryTool.args && typeof recoveryTool.args === "object"
      ? { ...recoveryTool.args }
      : {};
    const recoveryFingerprint = String(recoveryObligation.fingerprint || "");
    const repoAudit = state.repoAuditLedger && typeof state.repoAuditLedger === "object"
      ? state.repoAuditLedger
      : {};
    const repoAuditRequired = repoAudit.required === true;
    const repoAuditStatus = String(repoAudit.status || "").toLowerCase();
    const repoAuditQueue = cleanStrings(repoAudit.queuedTargets);
    const repoAuditCursor = Math.min(repoAuditQueue.length, nonNegativeInt(repoAudit.cursor));
    const pendingGate = String(pendingGates[0] || "");
    const failedGateAttempt = pendingGate
      ? failedGateAttemptForCurrentScope(state, pendingGate)
      : {};
    const repeatedGateBlocker = Boolean(
      pendingGate
      && recoveryToolName === pendingGate
      && Number(failedGateAttempt.attemptCount || 0) >= 2
      && !failedGateAttempt.recoverySatisfiedAt
    );
    if (repeatedGateBlocker) {
      disposition = "rediscover";
      retryValue = "forbidden";
      blocker = {
        code: "REPEATED_GATE_BLOCKER",
        fingerprint: String(failedGateAttempt.fingerprint || ""),
      };
    } else if (["external_blocker", "await_user"].includes(recoveryStatus)) {
      disposition = "await_user";
      retryValue = "forbidden";
      blocker = {
        code: String(recoveryObligation.errorCode || "RECOVERY_EXTERNAL_BLOCKER"),
        fingerprint: recoveryFingerprint,
      };
    } else if (["phase_budget_checkpoint_required", "phase_budget_replan_required"].includes(recoveryStatus)) {
      if (recoveryToolName) {
        requiredName = recoveryToolName;
        requiredArgs = recoveryToolArgs;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = { code: "RECOVERY_REQUIRED_TOOL_MISSING", fingerprint: recoveryFingerprint };
      }
    } else if (repoAuditRequired && repoAuditStatus === "inventory_overflow") {
      disposition = "workflow_stop";
      retryValue = "forbidden";
      blocker = {
        code: "REPO_AUDIT_INVENTORY_OVERFLOW",
        fingerprint: String(repoAudit.inventoryHash || ""),
      };
    } else if (repoAuditRequired && repoAuditStatus !== "complete") {
      if (repoAuditCursor < repoAuditQueue.length) {
        requiredName = "read_file";
        requiredArgs = { path: repoAuditQueue[repoAuditCursor] };
        retryValue = "once";
      } else {
        disposition = "workflow_stop";
        retryValue = "forbidden";
        blocker = {
          code: "REPO_AUDIT_FRONTIER_INCONSISTENT",
          fingerprint: String(repoAudit.inventoryHash || ""),
        };
      }
    } else if (repoAuditRequired && repoAuditStatus === "complete") {
      if (synthesisReadiness.ready) {
        disposition = "continue";
        retryValue = "forbidden";
        noToolsForSynthesis = true;
      } else {
        const evidenceAction = nextEvidenceRecovery(state, synthesisReadiness);
        if (evidenceAction) {
          requiredName = evidenceAction.name;
          requiredArgs = evidenceAction.args;
        } else {
          requiredName = "unreal_agent_plan";
          requiredArgs = { request: String(state.objective || state.request || "Continue bounded source analysis") };
        }
        retryValue = "once";
      }
    } else if (recoveryStatus === "evidence_complete") {
      if (synthesisReadiness.ready) {
        disposition = "continue";
        retryValue = "forbidden";
        noToolsForSynthesis = true;
        blocker = {
          code: "EVIDENCE_COMPLETE",
          fingerprint: recoveryFingerprint,
        };
      } else {
        const evidenceAction = nextEvidenceRecovery(state, synthesisReadiness);
        if (evidenceAction) {
          requiredName = evidenceAction.name;
          requiredArgs = evidenceAction.args;
        } else {
          requiredName = "unreal_agent_plan";
          requiredArgs = { request: String(state.objective || state.request || "Continue bounded source analysis") };
        }
        retryValue = "once";
      }
    } else if (recoveryStatus === "environment_recovery") {
      const attemptCount = nonNegativeInt(recoveryObligation.attemptCount);
      if (recoveryToolName && attemptCount <= 1) {
        requiredName = recoveryToolName;
        requiredArgs = recoveryToolArgs;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = {
          code: String(recoveryObligation.errorCode || "RECOVERY_ENVIRONMENT_BLOCKED"),
          fingerprint: recoveryFingerprint,
        };
      }
    } else if ([
      "evidence_required",
      "repair_planning_required",
      "revalidate_required",
      "checkpoint_rebase_required",
    ].includes(recoveryStatus)) {
      if (recoveryToolName) {
        requiredName = recoveryToolName;
        requiredArgs = recoveryToolArgs;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = { code: "RECOVERY_REQUIRED_TOOL_MISSING", fingerprint: recoveryFingerprint };
      }
    } else if (recoveryStatus === "repair_required") {
      // A repair may only mutate while its current-scope sketch approval is
      // still valid. Gate expiry repopulates pendingGates atomically; that fact
      // must outrank the otherwise-ready recovery mutation or the public
      // control would require a mutation that authorization rejects.
      if (pendingGate) {
        requiredName = pendingGate;
        retryValue = "allowed";
      } else {
        requiredName = mutationToolForState(state, route);
        retryValue = "once";
        if (!requiredName) {
          disposition = "await_user";
          retryValue = "forbidden";
          blocker = { code: "RECOVERY_MUTATION_SCOPE_MISSING", fingerprint: recoveryFingerprint };
        }
      }
    } else if (state.slicePlanningRequired === true) {
      discoveryOnly = true;
    } else if (String(buildRecovery.status || "") === "evidence_required") {
      requiredName = String(buildRecovery.requiredNextTool || "").trim();
      requiredArgs = buildRecovery.requiredNextToolArgs && typeof buildRecovery.requiredNextToolArgs === "object"
        ? { ...buildRecovery.requiredNextToolArgs }
        : {};
    } else if (String(buildVerification.status || "") === "pending_automation") {
      requiredName = "run_unreal_automation_tests";
      const testFilters = Array.isArray(buildVerification.testFilters)
        ? buildVerification.testFilters.map(String).map((item) => item.trim()).filter(Boolean)
        : [];
      const testFilter = String(buildVerification.testFilter || "").trim();
      requiredArgs = testFilters.length
        ? { testFilters }
        : (testFilter ? { testFilter } : {});
    } else if (initialCompileDiagnostic) {
      requiredName = "build_unreal_project";
    } else if (preGateReadPath) {
      requiredName = "read_file";
      requiredArgs = { path: preGateReadPath };
    } else if (pendingGates.length) {
      const gate = pendingGates[0];
      const attempt = failedGateAttemptForCurrentScope(state, gate);
      const recoverySatisfied = Boolean(attempt.recoverySatisfiedAt);
      if (Number(attempt.attemptCount || 0) >= 2 && !recoverySatisfied) {
        disposition = "rediscover";
        retryValue = "forbidden";
        blocker = { code: "REPEATED_GATE_BLOCKER", fingerprint: String(attempt.fingerprint || "") };
      } else {
        const recoveryTool = recoverySatisfied ? "" : String(attempt.nextAction || "").trim();
        requiredName = activeTools.includes(recoveryTool) ? recoveryTool : gate;
        if (requiredName === recoveryTool && attempt.nextActionArgs && typeof attempt.nextActionArgs === "object") {
          requiredArgs = { ...attempt.nextActionArgs };
        }
        retryValue = recoveryTool ? "once" : "allowed";
      }
    } else {
      let checkpointAction = String(checkpoint.requiredNextAction || "").trim();
      const completedNames = new Set(Object.entries(state.completedGates || {})
        .filter(([, record]) => record && record.status === "completed")
        .map(([name]) => name));
      if (completedNames.has(checkpointAction)) checkpointAction = "";
      const sketch = completedSketchForScope(state);
      const mutationGeneration = nonNegativeInt(state.mutationGeneration);
      const sketchGeneration = nonNegativeInt(sketch?.mutationGeneration);
      const checkpointGeneration = nonNegativeInt(checkpoint.mutationGeneration);
      const validation = checkpoint.validation && typeof checkpoint.validation === "object"
        ? checkpoint.validation
        : {};
      const validationStatus = String(validation.status || "").trim().toLowerCase();
      const validationRecovery = validation.recovery && typeof validation.recovery === "object"
        ? validation.recovery
        : {};
      const validationRecoverySatisfied = Boolean(
        String(validationRecovery.status || "") === "evidence_satisfied"
        && nonNegativeInt(validationRecovery.mutationGeneration) === mutationGeneration
      );
      const mutationRequired = Boolean(
        phase === "executor"
        && sketch
        && sketchGeneration === mutationGeneration
      );
      const currentMutationCheckpoint = Boolean(
        phase === "executor"
        && sketch
        && checkpointGeneration === mutationGeneration
        && mutationGeneration > sketchGeneration
      );
      if (mutationRequired) requiredName = mutationToolForState(state, route);
      else if (currentMutationCheckpoint && validationStatus === "passed") {
        requiredName = "build_unreal_project";
      } else if (currentMutationCheckpoint && validationStatus === "failed") {
        if (validationRecoverySatisfied) {
          requiredName = mutationToolForState(state, route);
        } else {
          const fallback = validationFindingRecovery(validation.firstFinding);
          requiredName = String(fallback.requiredTool?.name || "");
          requiredArgs = fallback.requiredTool?.args
            && typeof fallback.requiredTool.args === "object"
            ? { ...fallback.requiredTool.args }
            : {};
        }
      } else if (currentMutationCheckpoint) {
        requiredName = "static_validate_project";
      } else if (checkpointAction && activeTools.includes(checkpointAction)) {
        requiredName = checkpointAction;
      }
    }
    // Never publish an incomplete read-only task as an unqualified
    // planner/continue state. If all ordinary branches failed to reconstruct
    // the next evidence action, fail closed with a durable blocker.
    if (
      String(state.mode || "").toLowerCase() === "read_only"
      && isSourceEvidenceTask(state)
      && synthesisReadiness.ready !== true
      && !requiredName
      && !blocker
    ) {
      const evidenceAction = nextEvidenceRecovery(state, synthesisReadiness);
      if (evidenceAction) {
        requiredName = evidenceAction.name;
        requiredArgs = evidenceAction.args;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = {
          code: "EVIDENCE_FRONTIER_LOST",
          fingerprint: canonicalHash({
            taskSessionId: String(state.taskSessionId || ""),
            planRevision: String(state.planRevision || ""),
            acceptedEvidenceHash: String(synthesisReadiness.acceptedEvidenceHash || ""),
            remainingFrontierHash: String(synthesisReadiness.remainingFrontierHash || ""),
            reason: String(synthesisReadiness.reason || ""),
          }),
        };
      }
    }
    if (requiredName === "static_validate_project") {
      const projectRoot = authoritativeProjectRoot(state);
      if (projectRoot) requiredArgs = { projectRoot, fullAudit: false };
    }
    if (requiredName === "build_unreal_project") {
      const projectFile = authoritativeProjectFile(state);
      if (projectFile) {
        requiredArgs = {
          ...requiredArgs,
          project: projectFile,
          allowAbsoluteProject: true,
          allowEngineFallback: false,
        };
      }
      const buildContract = state.buildContract && typeof state.buildContract === "object"
        ? state.buildContract
        : {};
      for (const key of ["engineRoot", "target", "platform", "configuration"]) {
        const value = String(buildContract[key] || "").trim();
        if (value) requiredArgs[key] = value;
      }
    }
    if (requiredName === "run_unreal_automation_tests") {
      const projectFile = authoritativeProjectFile(state);
      if (projectFile) requiredArgs = { ...requiredArgs, project: projectFile };
      const engineRoot = String(buildVerification.engineRoot || "").trim();
      if (engineRoot) requiredArgs = { ...requiredArgs, engineRoot: path.resolve(engineRoot) };
    }
    if (requiredName) disposition = requiredName === "unreal_task_checkpoint" ? "checkpoint" : "require_tool";
  }

  const allowedTools = requiredName
    ? [requiredName]
    : ["complete", "workflow_stop", "await_user"].includes(disposition)
      ? []
      : noToolsForSynthesis
        ? []
      : disposition === "rediscover" || discoveryOnly
        ? activeTools.filter((name) => (
          (blocker?.code === "REPEATED_GATE_BLOCKER"
            ? REPEATED_GATE_REDISCOVERY_TOOLS.has(name)
            : DISCOVERY_TOOLS.has(name))
          || (discoveryOnly && name === String(pendingGates[0] || ""))
        ))
        : activeTools;
  return {
    version: 2,
    authoritative: true,
    taskSessionId: String(state.taskSessionId || ""),
    taskMode: String(state.mode || "").trim().toLowerCase(),
    planRevision: String(state.planRevision || ""),
    activeSliceId: String(state.activeSliceId || ""),
    phase,
    disposition,
    requiredTool: requiredName ? { name: requiredName, args: requiredArgs } : null,
    allowedTools,
    routeHash: String(route.routeHash || ""),
    pendingGates,
    retryPolicy: { sameSemanticInput: retryValue },
    blocker,
    mutationGeneration: nonNegativeInt(state.mutationGeneration),
  };
}

function commitControlTransition(state) {
  const readiness = deriveSynthesisReadiness(state);
  prepareSynthesisHandoff(state, readiness);
  const control = deriveNextObligation(state);
  control.synthesisReadiness = { ...readiness };
  const previousControl = state.controlState && typeof state.controlState === "object"
    ? state.controlState
    : null;
  const semanticView = (value) => {
    const material = value && typeof value === "object" ? JSON.parse(JSON.stringify(value)) : {};
    delete material.epoch;
    delete material.fingerprint;
    if (material.synthesisReadiness && typeof material.synthesisReadiness === "object") {
      delete material.synthesisReadiness.controlEpoch;
    }
    if (material.synthesisLatch && typeof material.synthesisLatch === "object") {
      delete material.synthesisLatch.controlEpoch;
    }
    return material;
  };
  const baseEpoch = nonNegativeInt(state.controlEpoch);
  control.synthesisReadiness.controlEpoch = baseEpoch;
  if (control.phase === "synthesis" && readiness.ready === true) {
    control.synthesisLatch = {
      version: 1,
      name: "synthesize_current_evidence",
      controlEpoch: baseEpoch,
      planRevision: String(readiness.planRevision || ""),
      acceptedEvidenceHash: String(readiness.acceptedEvidenceHash || ""),
      remainingFrontierHash: String(readiness.remainingFrontierHash || ""),
      commitEligible: true,
      pendingEvidenceObligation: false,
    };
  }
  let fingerprint = canonicalHash(control);
  const semanticChanged = previousControl
    ? canonicalHash(semanticView(control)) !== canonicalHash(semanticView(previousControl))
    : fingerprint !== String(state.controlFingerprint || "");
  let epoch = baseEpoch;
  if (semanticChanged) epoch += 1;
  control.synthesisReadiness.controlEpoch = epoch;
  fingerprint = canonicalHash(control);
  if (control.phase === "synthesis" && readiness.ready === true) {
    control.synthesisReadiness.controlEpoch = epoch;
    control.synthesisLatch.controlEpoch = epoch;
    fingerprint = canonicalHash(control);
  } else {
    fingerprint = canonicalHash(control);
  }
  control.epoch = epoch;
  control.fingerprint = fingerprint;
  state.controlEpoch = epoch;
  state.controlFingerprint = fingerprint;
  state.controlState = control;
  return state;
}

module.exports = {
  canonicalHash,
  commitControlTransition,
  deriveNextObligation,
  failedGateAttemptForCurrentScope,
  mutationToolForState,
  preGateSourceReadPath,
  transitionPathIdentity,
  validationFindingRecovery,
  authoritativeProjectFile,
  authoritativeProjectRoot,
  isSourceEvidenceTask,
};
