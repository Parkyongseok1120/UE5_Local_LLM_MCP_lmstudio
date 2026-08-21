import { Chat, type ChatMessage, type GeneratorController } from "@lmstudio/sdk";
import { configSchematics } from "./config";

// The core is intentionally dependency-free so it can be unit-tested outside LM Studio.
// @ts-ignore CommonJS core is shipped beside the plugin entrypoint.
import core = require("./compaction-core.js");
// @ts-ignore CommonJS store is shipped beside the plugin entrypoint.
import store = require("./checkpoint-store.js");

const activeGenerationFlights = new Map<string, Promise<void>>();

function configValue(ctl: GeneratorController, key: any, fallback: unknown) {
  try {
    const value = ctl.getPluginConfig(configSchematics).get(key);
    return value === undefined || value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

function finiteNumber(value: unknown, fallback: number, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function isoNow(): string {
  return new Date().toISOString();
}

function finiteTelemetryNumber(
  value: unknown,
  fallback = 0,
  minimum = -Number.MAX_SAFE_INTEGER,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function optionalConfigValue(ctl: GeneratorController, key: any): unknown {
  try {
    return ctl.getPluginConfig(configSchematics).get(key);
  } catch {
    return undefined;
  }
}

function resolveCurrentTurnCapConfig(ctl: GeneratorController): {
  configuredValue: number | null;
  source: "sdk_effective_value" | "migrated_default";
  explicitnessObservable: false;
  effectiveValue: number;
  configVersion: number;
} {
  const rawVersion = optionalConfigValue(ctl, "configVersion");
  const parsedVersion = Number(rawVersion);
  const configVersion = Number.isFinite(parsedVersion) && parsedVersion >= 2
    ? Math.floor(parsedVersion)
    : 2;
  const rawCap = optionalConfigValue(ctl, "maxCurrentTurnMessages");
  const parsedCap = Number(rawCap);
  const present = rawCap !== undefined && rawCap !== null && Number.isFinite(parsedCap);
  const effectiveValue = present
    ? Math.floor(finiteNumber(parsedCap, 12, 0, 256))
    : 12;
  return {
    configuredValue: present ? parsedCap : null,
    // ParsedConfig.get() exposes only the effective value. It deliberately does
    // not reveal whether that value came from the user or the schematic default.
    source: present ? "sdk_effective_value" : "migrated_default",
    explicitnessObservable: false,
    effectiveValue,
    configVersion,
  };
}

type ReasoningEffort = "low" | "medium" | "xhigh";

function isQwen38_27b(modelIdentifier: unknown): boolean {
  const identity = String(modelIdentifier || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return identity.includes("qwen38") && identity.includes("27b");
}

function normalizeReasoningEffort(value: unknown): ReasoningEffort {
  const normalized = String(value || "low").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized === "medium") return "medium";
  if (["xhigh", "extra_high", "extrahigh"].includes(normalized)) return "xhigh";
  return "low";
}

function resolvedPredictionPhase(checkpoint: any, architectureValidationRequired: boolean): string {
  const controlPhase = String(checkpoint?.serverControl?.phase || "").trim().toLowerCase();
  const requiredTool = String(checkpoint?.serverControl?.requiredTool?.name || "").trim();
  const recoverySource = String(
    checkpoint?.serverControl?.recoveryObligation?.source
    || checkpoint?.buildState?.recovery?.source
    || "",
  ).trim().toLowerCase();
  if (requiredTool) {
    return recoverySource === "build" || recoverySource === "automation"
      ? "compile_fix_patch"
      : "execute";
  }
  if (controlPhase === "synthesis") return "synthesis_final";
  if (architectureValidationRequired || controlPhase === "architecture") return "plan";
  if (recoverySource === "build" || recoverySource === "automation") return "compile_fix_analyze";
  if (controlPhase === "api_lookup") return "api_lookup";
  return "plan";
}

function qwen38ReasoningRawConfig(
  modelIdentifier: unknown,
  thinkingEnabled: boolean,
  effort: ReasoningEffort | null,
): { fields: Array<{ key: string; value: unknown }> } | undefined {
  if (!isQwen38_27b(modelIdentifier)) return undefined;
  const fields: Array<{ key: string; value: unknown }> = [
    { key: "llm.prediction.reasoning.enableThinking", value: thinkingEnabled },
    {
      key: "ext.virtualModel.customField.qwen.qwen3.827b.enableThinking",
      value: thinkingEnabled,
    },
  ];
  if (thinkingEnabled && effort) {
    fields.push({
      key: "ext.virtualModel.customField.qwen.qwen3.827b.reasoningEffort",
      value: effort,
    });
  }
  return {
    fields,
  };
}

type ModelFenceSnapshot = {
  version: 1;
  identifier: string;
  instanceReference: string;
  contextLength: number;
  observationLevel: "instance" | "identifier";
  loadConfigObservable: false;
};

type ModelFenceCaptureOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
  timeoutCode?: string;
};

function modelFenceWaitError(code: string, elapsedMs: number): Error {
  const error: any = new Error(`${code} after ${Math.max(0, Math.floor(elapsedMs))}ms`);
  error.code = code;
  error.elapsedMs = Math.max(0, Math.floor(elapsedMs));
  return error;
}

async function captureModelFence(
  model: any,
  resolvedTargetModel: string,
  contextLength: number,
  options: ModelFenceCaptureOptions = {},
): Promise<ModelFenceSnapshot> {
  let info: any = null;
  if (typeof model?.getModelInfo === "function") {
    const startedAt = performance.now();
    const timeoutMs = Math.max(0, Number(options.timeoutMs || 0));
    const signal = options.signal;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let abortListener: (() => void) | null = null;
    const modelInfoResult = Promise.resolve()
      .then(() => model.getModelInfo())
      .then(
        (value) => ({ available: true, value }),
        () => ({ available: false, value: null }),
      );
    const boundary = new Promise<never>((_resolve, reject) => {
      if (signal && typeof signal.addEventListener === "function") {
        abortListener = () => reject(
          signal.reason instanceof Error
            ? signal.reason
            : modelFenceWaitError("MODEL_FENCE_ABORTED", performance.now() - startedAt),
        );
        if (signal.aborted) {
          abortListener();
          return;
        }
        signal.addEventListener("abort", abortListener, { once: true });
      }
      if (timeoutMs > 0) {
        timer = setTimeout(() => reject(
          modelFenceWaitError(
            String(options.timeoutCode || "MODEL_FENCE_TIMEOUT"),
            performance.now() - startedAt,
          ),
        ), timeoutMs);
      }
    });
    try {
      const observed = await Promise.race([modelInfoResult, boundary]);
      if (signal?.aborted) {
        throw signal.reason instanceof Error
          ? signal.reason
          : modelFenceWaitError("MODEL_FENCE_ABORTED", performance.now() - startedAt);
      }
      const elapsedMs = performance.now() - startedAt;
      if (timeoutMs > 0 && elapsedMs >= timeoutMs) {
        throw modelFenceWaitError(
          String(options.timeoutCode || "MODEL_FENCE_TIMEOUT"),
          elapsedMs,
        );
      }
      if (observed.available) info = observed.value;
      // Only an actual SDK lookup failure falls back to identifier fencing.
      // Abort and timeout are raised by the independent boundary promise.
    } finally {
      if (timer) clearTimeout(timer);
      if (abortListener && signal && typeof signal.removeEventListener === "function") {
        signal.removeEventListener("abort", abortListener);
      }
    }
  }
  const identifier = String(
    info?.identifier || model?.identifier || resolvedTargetModel || "",
  ).trim();
  const instanceReference = String(info?.instanceReference || "").trim();
  return {
    version: 1,
    identifier,
    instanceReference,
    contextLength: Math.max(0, Math.floor(Number(contextLength) || 0)),
    observationLevel: instanceReference ? "instance" : "identifier",
    // @lmstudio/sdk 1.5.0 exposes instanceReference but keeps getLoadConfig
    // protected. Record that limitation instead of fabricating a config hash.
    loadConfigObservable: false,
  };
}

function modelFencesMatch(expected: ModelFenceSnapshot, actual: ModelFenceSnapshot): boolean {
  if (expected.instanceReference) {
    return Boolean(
      actual.instanceReference
      && actual.instanceReference === expected.instanceReference
      && actual.identifier === expected.identifier
      && actual.contextLength === expected.contextLength,
    );
  }
  return Boolean(
    expected.identifier
    && actual.identifier === expected.identifier
    && actual.contextLength === expected.contextLength,
  );
}

function modelFenceChangedError(
  expected: ModelFenceSnapshot,
  actual: ModelFenceSnapshot,
): Error {
  const error: any = new Error(
    "The loaded LM Studio model instance changed during the prediction transaction; "
    + "all buffered output was discarded before commit.",
  );
  error.code = "MODEL_INSTANCE_CHANGED";
  error.expectedModelFence = expected;
  error.actualModelFence = actual;
  return error;
}

function checkpointLifecycleIdentity(checkpoint: any): {
  taskSessionId?: string;
  objectiveHash?: string;
  controlEpoch?: number;
} {
  const taskSessionId = String(
    checkpoint?.serverControl?.taskSessionId
    || checkpoint?.taskRouteOwnership?.taskSessionId
    || "",
  ).trim().slice(0, 160);
  const objectiveHash = String(checkpoint?.objectiveHash || "").trim().toLowerCase();
  const rawEpoch = checkpoint?.serverControl?.epoch
    ?? checkpoint?.controlEpoch
    ?? checkpoint?.checkpointGeneration;
  const controlEpoch = Number(rawEpoch);
  return {
    ...(taskSessionId ? { taskSessionId } : {}),
    ...(/^[a-f0-9]{64}$/.test(objectiveHash) ? { objectiveHash } : {}),
    ...(Number.isInteger(controlEpoch) && controlEpoch >= 0 ? { controlEpoch } : {}),
  };
}

function lifecycleState(
  checkpoint: any,
  status: "pending" | "prepared" | "completed" | "commit_sent" | "commit_acked" | "delivery_pending" | "delivery_uncertain" | "delivery_reemit_authorized" | "committed" | "delivered" | "rejected_stale" | "evidence_recovery",
  options: { outputDigest?: string; stopReason?: string } = {},
): any {
  return {
    version: 1,
    status,
    ...checkpointLifecycleIdentity(checkpoint),
    ...(/^[a-f0-9]{64}$/.test(String(options.outputDigest || ""))
      ? { outputDigest: String(options.outputDigest) }
      : {}),
    ...(options.stopReason ? { stopReason: String(options.stopReason).slice(0, 80) } : {}),
    updatedAt: isoNow(),
  };
}

function mergeLifecycleState(prior: any, next: any): any {
  const merge = (core as any).mergeLifecycleState;
  return typeof merge === "function" ? merge(prior, next) : next;
}

function predictionOutputDigest(events: any[], requests: any[]): string {
  return core.sha256(core.stableStringify({
    events: events.map((event: any) => ({
      kind: String(event?.kind || ""),
      callId: event?.callId,
      toolCallId: event?.toolCallId,
      name: event?.name,
      content: event?.content,
      request: event?.request,
      error: event?.error,
    })),
    requests: requests.map((entry: any) => entry?.request || entry),
  }));
}

async function waitForAbortableDelay(ctl: GeneratorController, delayMs: number): Promise<void> {
  guardGeneratorAbort(ctl);
  const signal = (ctl as any)?.abortSignal;
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (signal && typeof signal.removeEventListener === "function") {
        signal.removeEventListener("abort", onAbort);
      }
      if (error) reject(error);
      else resolve();
    };
    const onAbort = () => finish(
      signal?.reason instanceof Error ? signal.reason : new Error("Generation aborted"),
    );
    const timer = setTimeout(() => finish(), Math.max(1, delayMs));
    if (signal && typeof signal.addEventListener === "function") {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
  guardGeneratorAbort(ctl);
}

async function resolveTargetModel(
  ctl: GeneratorController,
  configuredTargetModel: string,
  options: { timeoutSeconds: number; pollIntervalSeconds: number },
): Promise<{ model: any; resolvedTargetModel: string; autoSelected: boolean }> {
  guardGeneratorAbort(ctl);
  if (configuredTargetModel) {
    const startedMonotonicAt = performance.now();
    const timeoutMs = Math.max(0, Number(options.timeoutSeconds) * 1000);
    const intervalMs = Math.max(10, Number(options.pollIntervalSeconds) * 1000);
    // model(modelKey) is LM Studio's load-or-get operation. Keep the request
    // handled even after a local timeout so a late loader rejection cannot
    // become an unhandled promise while the durable task waits for retry.
    const loadingOutcome: Promise<{ model?: any; error?: any }> = Promise.resolve()
      .then(() => {
        guardGeneratorAbort(ctl);
        return ctl.client.llm.model(configuredTargetModel);
      }).then(
      (model: any) => ({ model }),
      (error: any) => ({ error }),
    );
    let lastHeartbeatAt = 0;
    while (true) {
      const elapsedMs = performance.now() - startedMonotonicAt;
      if (elapsedMs >= timeoutMs) {
        throw new Error(
          `Configured targetModel did not become ready within modelReadinessTimeoutSeconds: ${configuredTargetModel}`,
        );
      }
      if (elapsedMs - lastHeartbeatAt >= Math.max(1000, intervalMs)) {
        lastHeartbeatAt = elapsedMs;
        ctl.fragmentGenerated(
          `\n[Loading configured LM Studio model - ${Math.max(1, Math.floor(elapsedMs / 1000))}s elapsed]\n`,
          { reasoningType: "reasoning", containsDrafted: false, isStructural: true },
        );
      }
      const outcome = await Promise.race([
        loadingOutcome,
        waitForAbortableDelay(
          ctl,
          Math.min(intervalMs, Math.max(1, timeoutMs - elapsedMs)),
        ).then(() => null),
      ]);
      if (!outcome) continue;
      if (outcome.error) throw outcome.error;
      const model = outcome.model;
      return {
        model,
        resolvedTargetModel: String(model?.identifier || model?.modelKey || configuredTargetModel),
        autoSelected: false,
      };
    }
  }

  const startedMonotonicAt = performance.now();
  const timeoutMs = Math.max(0, Number(options.timeoutSeconds) * 1000);
  const intervalMs = Math.max(10, Number(options.pollIntervalSeconds) * 1000);
  let lastHeartbeatAt = 0;
  let lastListError = "";
  while (true) {
    guardGeneratorAbort(ctl);
    const elapsedBeforeListMs = performance.now() - startedMonotonicAt;
    if (elapsedBeforeListMs >= timeoutMs) {
      throw new Error(
        "No loaded LLM became ready before modelReadinessTimeoutSeconds elapsed. "
        + "Load exactly one model in LM Studio or configure targetModel, then continue the same task."
        + (lastListError ? ` Last readiness error: ${lastListError}` : ""),
      );
    }
    let loaded: any[] = [];
    try {
      loaded = await boundedPredictionSetupValue(
        ctl,
        Math.max(1, timeoutMs - elapsedBeforeListMs),
        startedMonotonicAt,
        () => ctl.client.llm.listLoaded(),
      );
      lastListError = "";
    } catch (error: any) {
      if ((ctl as any)?.abortSignal?.aborted) throw error;
      if (String(error?.code || "") === "PREDICTION_WALL_CLOCK_EXCEEDED") {
        throw new Error(
          "No loaded LLM became ready before modelReadinessTimeoutSeconds elapsed. "
          + "Load exactly one model in LM Studio or configure targetModel, then continue the same task."
          + (lastListError ? ` Last readiness error: ${lastListError}` : ""),
        );
      }
      lastListError = String(error?.message || error);
    }
    if (loaded.length === 1) {
      const model = loaded[0];
      return {
        model,
        resolvedTargetModel: String(model?.identifier || model?.modelKey || "auto-selected"),
        autoSelected: true,
      };
    }
    if (loaded.length > 1) {
      const names = loaded.map((item: any) => item.identifier || item.modelKey).join(", ");
      throw new Error(
        `Set targetModel because automatic selection requires exactly one loaded LLM. Loaded: ${names}`,
      );
    }
    const elapsedMs = performance.now() - startedMonotonicAt;
    if (elapsedMs >= timeoutMs) {
      throw new Error(
        "No loaded LLM became ready before modelReadinessTimeoutSeconds elapsed. "
        + "Load exactly one model in LM Studio or configure targetModel, then continue the same task."
        + (lastListError ? ` Last readiness error: ${lastListError}` : ""),
      );
    }
    if (elapsedMs - lastHeartbeatAt >= Math.max(1000, intervalMs)) {
      lastHeartbeatAt = elapsedMs;
      ctl.fragmentGenerated(
        `\n[Waiting for one LM Studio model to become ready - ${Math.max(1, Math.floor(elapsedMs / 1000))}s elapsed]\n`,
        { reasoningType: "reasoning", containsDrafted: false, isStructural: true },
      );
    }
    await waitForAbortableDelay(ctl, Math.min(intervalMs, Math.max(1, timeoutMs - elapsedMs)));
  }
}

type ToolCallCallbackState = {
  toolCallId?: string;
  name?: string;
  endFingerprint?: string;
  failure?: string;
  terminal?: "end" | "failure";
};

function createToolCallCallbackFsm(): {
  start: (callId: number, toolCallId: unknown) => boolean;
  name: (callId: number, name: string) => boolean;
  args: (callId: number, content: string) => boolean;
  end: (callId: number, request: any) => boolean;
  failure: (callId: number, error: unknown) => boolean;
} {
  const states = new Map<number, ToolCallCallbackState>();
  const stateFor = (callId: number) => {
    const existing = states.get(callId) || {};
    states.set(callId, existing);
    return existing;
  };
  const requireOpen = (state: ToolCallCallbackState, callId: number, stage: string) => {
    if (state.terminal) {
      throw new Error(`Tool-call callback ${callId} received ${stage} after terminal ${state.terminal}.`);
    }
  };
  return {
    start(callId, rawToolCallId) {
      const state = stateFor(callId);
      const toolCallId = String(rawToolCallId || "").trim();
      if (state.terminal) {
        if (!toolCallId || state.toolCallId === toolCallId) return false;
        throw new Error(`Tool-call callback ${callId} received conflicting start after terminal ${state.terminal}.`);
      }
      if (state.toolCallId !== undefined) {
        if (!state.toolCallId && toolCallId) {
          state.toolCallId = toolCallId;
          return false;
        }
        if (!toolCallId || state.toolCallId === toolCallId) return false;
        throw new Error(
          `Conflicting duplicate tool-call start for callback ${callId}: ${state.toolCallId} != ${toolCallId}`,
        );
      }
      if (toolCallId) state.toolCallId = toolCallId;
      else state.toolCallId = "";
      return true;
    },
    name(callId, rawName) {
      const state = stateFor(callId);
      const name = String(rawName || "").trim();
      if (state.terminal) {
        if (state.name === name) return false;
        throw new Error(`Tool-call callback ${callId} received conflicting name after terminal ${state.terminal}.`);
      }
      if (state.name !== undefined) {
        if (state.name === name) return false;
        throw new Error(
          `Conflicting duplicate tool-call name for callback ${callId}: ${state.name} != ${name}`,
        );
      }
      state.name = name;
      return true;
    },
    args(callId, content) {
      const state = stateFor(callId);
      requireOpen(state, callId, "arguments");
      return String(content || "").length > 0;
    },
    end(callId, request) {
      const state = stateFor(callId);
      if (state.terminal === "failure") {
        throw new Error(`Tool-call callback ${callId} received end after terminal failure.`);
      }
      const requestId = String(request?.id || "").trim();
      const requestName = String(request?.name || "").trim();
      if (state.toolCallId && requestId && state.toolCallId !== requestId) {
        throw new Error(
          `Tool-call callback ${callId} ended with a different id: ${state.toolCallId} != ${requestId}`,
        );
      }
      if (state.name && requestName && state.name.toLowerCase() !== requestName.toLowerCase()) {
        throw new Error(
          `Tool-call callback ${callId} ended with a different name: ${state.name} != ${requestName}`,
        );
      }
      const fingerprint = core.sha256(core.stableStringify({
        id: String(request?.id || ""),
        name: String(request?.name || "").trim().toLowerCase(),
        arguments: request?.arguments && typeof request.arguments === "object"
          && !Array.isArray(request.arguments) ? request.arguments : {},
      }));
      if (state.endFingerprint !== undefined) {
        if (state.endFingerprint === fingerprint) return false;
        throw new Error(`Conflicting duplicate tool-call end for callback ${callId}.`);
      }
      state.endFingerprint = fingerprint;
      state.terminal = "end";
      return true;
    },
    failure(callId, rawError) {
      const state = stateFor(callId);
      if (state.terminal === "end") {
        throw new Error(`Tool-call callback ${callId} received failure after terminal end.`);
      }
      const error = String((rawError as any)?.message || rawError || "unknown tool-call failure");
      if (state.failure !== undefined) return false;
      state.failure = error;
      state.terminal = "failure";
      return true;
    },
  };
}

function authoritativeSynthesisFinality(control: any, checkpoint: any): {
  eligible: boolean;
  reason: string;
} {
  const readiness = control?.synthesisReadiness;
  const latch = control?.synthesisLatch;
  const controlEpoch = Number(control?.epoch);
  const planRevision = String(control?.planRevision || "").trim();
  const sourceEvidencePlanRevision = String(
    readiness?.sourceEvidencePlanRevision || checkpoint?.sourceEvidence?.planRevision || "",
  ).trim();
  const checkpointReadiness = checkpoint?.synthesisReadiness;
  const hashesMatch = Boolean(
    /^[a-f0-9]{64}$/.test(String(readiness?.acceptedEvidenceHash || ""))
    && String(readiness.acceptedEvidenceHash) === String(latch?.acceptedEvidenceHash)
    && /^[a-f0-9]{64}$/.test(String(readiness?.remainingFrontierHash || ""))
    && String(readiness.remainingFrontierHash) === String(latch?.remainingFrontierHash)
    && /^[a-f0-9]{64}$/.test(String(readiness?.synthesisEvidenceBundleHash || ""))
    && String(readiness.synthesisEvidenceBundleHash) === String(latch?.synthesisEvidenceBundleHash)
    && core.compactSynthesisEvidenceBundle(readiness?.synthesisEvidenceBundle)?.bundleHash
      === String(readiness.synthesisEvidenceBundleHash),
  );
  const eligible = Boolean(
    String(control?.phase || "").trim().toLowerCase() === "synthesis"
    && String(control?.disposition || "").trim().toLowerCase() === "continue"
    && !control?.requiredTool
    && Array.isArray(control?.allowedTools)
    && control.allowedTools.length === 0
    && (!Array.isArray(control?.pendingGates) || control.pendingGates.length === 0)
    && readiness
    && readiness.ready === true
    && readiness.commitEligible === true
    && readiness.pendingEvidenceObligation === false
    && latch
    && Number(latch.version) === 1
    && String(latch.name || "") === "synthesize_current_evidence"
    && Number.isInteger(controlEpoch)
    && controlEpoch >= 0
    && Number(readiness.controlEpoch) === controlEpoch
    && Number(latch.controlEpoch) === controlEpoch
    && planRevision
    && String(readiness.planRevision || "") === planRevision
    && String(latch.planRevision || "") === planRevision
    && (!sourceEvidencePlanRevision || sourceEvidencePlanRevision === planRevision)
    && (!checkpointReadiness || (
      String(checkpointReadiness.planRevision || "") === planRevision
      && String(checkpointReadiness.acceptedEvidenceHash || "") === String(readiness.acceptedEvidenceHash)
      && String(checkpointReadiness.remainingFrontierHash || "") === String(readiness.remainingFrontierHash)
      && String(checkpointReadiness.synthesisEvidenceBundleHash || "")
        === String(readiness.synthesisEvidenceBundleHash)
    ))
    && hashesMatch
    && latch.commitEligible === true
    && latch.pendingEvidenceObligation === false
  );
  if (eligible) return { eligible: true, reason: "authoritative_synthesis_latch_valid" };
  if (String(control?.phase || "").trim().toLowerCase() !== "synthesis") {
    return { eligible: false, reason: "server_phase_not_synthesis" };
  }
  if (control?.requiredTool || (Array.isArray(control?.allowedTools) && control.allowedTools.length > 0)) {
    return { eligible: false, reason: "authoritative_tool_obligation_present" };
  }
  if (readiness?.ready !== true || readiness?.commitEligible !== true) {
    return { eligible: false, reason: "synthesis_readiness_not_committable" };
  }
  return { eligible: false, reason: "synthesis_latch_identity_mismatch" };
}

function preparedSynthesisRecord(
  checkpoint: any,
  output: string,
  outputDigest: string,
  synthesisTransactionId: string,
  control: any,
): any {
  return {
    version: 1,
    status: "prepared",
    output,
    outputDigest,
    synthesisTransactionId,
    taskSessionId: String(control?.taskSessionId || "").trim(),
    objectiveHash: String(checkpoint?.objectiveHash || "").trim().toLowerCase(),
    controlEpoch: Number(control?.epoch),
    controlFingerprint: String(control?.controlFingerprint || control?.fingerprint || "").trim().toLowerCase(),
    planRevision: String(control?.planRevision || control?.synthesisReadiness?.planRevision || "").trim(),
    acceptedEvidenceHash: String(control?.synthesisReadiness?.acceptedEvidenceHash || "").trim().toLowerCase(),
    remainingFrontierHash: String(control?.synthesisReadiness?.remainingFrontierHash || "").trim().toLowerCase(),
    synthesisEvidenceBundleHash: String(
      control?.synthesisReadiness?.synthesisEvidenceBundleHash || "",
    ).trim().toLowerCase(),
    mutationGeneration: Math.max(0, Number(control?.mutationGeneration ?? checkpoint?.mutationGeneration ?? 0)),
    preparedAt: isoNow(),
    dispatchedAt: "",
    rejectedAt: "",
    rejectionCode: "",
    staleReason: "",
    rejectedByControlEpoch: 0,
    rejectedByControlFingerprint: "",
  };
}

type HostToolCallStage = "start" | "name" | "args" | "end" | "failure";

type HostToolCallLifecycleEvent = {
  stage: HostToolCallStage;
  hostToolCallId: string;
  outerGenerateRequestId?: string;
  predictionAttemptId?: string;
  modelLocalCallId?: string;
  rawToolCallId?: string;
  toolCallId?: string;
  toolName?: string;
  source?: string;
  callId?: number;
  name?: string;
  content?: string;
  request?: any;
  error?: string;
  durableIdentity?: boolean;
  allowImplicitStart?: boolean;
};

type HostToolCallTransaction = {
  hostToolCallId: string;
  outerGenerateRequestId: string;
  predictionAttemptId: string;
  modelLocalCallId: string;
  rawToolCallId: string;
  toolCallId: string;
  source: string;
  started: boolean;
  name?: string;
  startEvent?: any;
  nameEvent?: any;
  argumentFragments: any[];
  terminal?: "end" | "failure";
  terminalEvent?: any;
  requestFingerprint?: string;
  emittedEvents?: any[];
  emitted: boolean;
};

type HostToolCallLifecycleDiagnostic = {
  outerGenerateRequestId: string;
  predictionAttemptId: string;
  modelLocalCallId: string;
  rawToolCallId: string;
  hostToolCallId: string;
  toolName: string;
  stage: HostToolCallStage;
  sequenceNumber: number;
  source: string;
  accepted: boolean;
  outcome: string;
  anomaly?: string;
};

type HostToolCallLifecycleManager = {
  record: (event: HostToolCallLifecycleEvent) => boolean;
  replaceArguments: (hostToolCallId: string, args: any) => boolean;
  replaceTerminalWithFailure: (hostToolCallId: string, error: string) => boolean;
  setEmitter: (emit: (event: any) => void) => void;
  setActivePredictionAttempt: (predictionAttemptId: string) => void;
  closePredictionAttempt: (predictionAttemptId: string) => void;
  settle: (reason?: string) => void;
  diagnostics: () => HostToolCallLifecycleDiagnostic[];
  snapshot: () => {
    acceptedHostTransactionCount: number;
    hostStartCount: number;
    hostNameCount: number;
    hostTerminalCount: number;
    lmStudioNameEventCount: number;
    transactions: any[];
  };
};

function createHostToolCallTransactionManager(options: {
  outerGenerateRequestId?: string;
  emit?: (event: any) => void;
  onDiagnostic?: (diagnostic: HostToolCallLifecycleDiagnostic) => void;
} = {}): HostToolCallLifecycleManager {
  const outerGenerateRequestId = String(
    options.outerGenerateRequestId || "outer-generate-unknown",
  ).slice(0, 160);
  const transactions = new Map<string, HostToolCallTransaction>();
  const order: string[] = [];
  const emittedTransactions = new Set<string>();
  const diagnosticLog: HostToolCallLifecycleDiagnostic[] = [];
  let emit = typeof options.emit === "function" ? options.emit : () => {};
  let activePredictionAttemptId = "";
  let sequenceNumber = 0;
  let active = true;

  const text = (value: unknown, limit = 160): string => String(value || "").trim().slice(0, limit);
  const normalizedName = (value: unknown): string => text(value, 240).toLowerCase();
  const eventToolName = (event: HostToolCallLifecycleEvent): string => text(
    event.toolName || event.name || event.request?.name,
    240,
  );
  const eventHostId = (event: HostToolCallLifecycleEvent): string => text(event.hostToolCallId, 240);
  const report = (
    event: HostToolCallLifecycleEvent,
    accepted: boolean,
    outcome: string,
    anomaly = "",
  ): HostToolCallLifecycleDiagnostic => {
    const diagnostic: HostToolCallLifecycleDiagnostic = {
      outerGenerateRequestId,
      predictionAttemptId: text(event.predictionAttemptId, 160),
      modelLocalCallId: text(event.modelLocalCallId, 120),
      rawToolCallId: text(event.rawToolCallId, 240),
      hostToolCallId: eventHostId(event),
      toolName: eventToolName(event),
      stage: event.stage,
      sequenceNumber: sequenceNumber += 1,
      source: text(event.source || "model_callback", 80),
      accepted,
      outcome: text(outcome, 80),
      ...(anomaly ? { anomaly: text(anomaly, 240) } : {}),
    };
    diagnosticLog.push(diagnostic);
    while (diagnosticLog.length > 256) diagnosticLog.shift();
    try { options.onDiagnostic?.(diagnostic); } catch { /* diagnostics never change lifecycle */ }
    return diagnostic;
  };
  const anomaly = (event: HostToolCallLifecycleEvent, message: string): never => {
    report(event, false, "rejected", message);
    throw new Error(message);
  };
  const lifecycleEvent = (
    event: HostToolCallLifecycleEvent,
    transaction: HostToolCallTransaction,
  ): any => ({
    kind: event.stage,
    callId: event.callId,
    toolCallId: event.toolCallId || transaction.toolCallId || transaction.hostToolCallId,
    hostToolCallId: transaction.hostToolCallId,
    outerGenerateRequestId: transaction.outerGenerateRequestId,
    predictionAttemptId: transaction.predictionAttemptId,
    modelLocalCallId: transaction.modelLocalCallId,
    rawToolCallId: transaction.rawToolCallId,
    source: transaction.source,
    ...(event.stage === "name" ? { name: transaction.name || event.name || "" } : {}),
    ...(event.stage === "args" ? { content: String(event.content || "") } : {}),
    ...(event.stage === "end" ? { request: event.request } : {}),
    ...(event.stage === "failure" ? { error: String(event.error || "unknown tool-call failure") } : {}),
  });
  const makeTransaction = (
    event: HostToolCallLifecycleEvent,
    implicitStart = false,
  ): HostToolCallTransaction => {
    const hostToolCallId = eventHostId(event);
    const transaction: HostToolCallTransaction = {
      hostToolCallId,
      outerGenerateRequestId,
      predictionAttemptId: text(event.predictionAttemptId, 160),
      modelLocalCallId: text(event.modelLocalCallId, 120),
      rawToolCallId: text(event.rawToolCallId || event.toolCallId, 240),
      toolCallId: text(event.toolCallId || hostToolCallId, 240),
      source: text(event.source || "model_callback", 80),
      started: implicitStart,
      argumentFragments: [],
      emitted: false,
    };
    if (implicitStart) {
      transaction.startEvent = lifecycleEvent({
        ...event,
        stage: "start",
        toolCallId: transaction.toolCallId,
        durableIdentity: true,
      }, transaction);
    }
    transactions.set(hostToolCallId, transaction);
    order.push(hostToolCallId);
    return transaction;
  };
  const ensureAttempt = (event: HostToolCallLifecycleEvent): void => {
    const attempt = text(event.predictionAttemptId, 160);
    if (
      activePredictionAttemptId
      && attempt
      && attempt !== activePredictionAttemptId
      && text(event.source || "model_callback", 80) === "model_callback"
    ) {
      report(event, false, "late_attempt_discarded", "callback belongs to an inactive prediction attempt");
      throw new Error(
        `Tool-call callback from stale prediction attempt ${attempt} cannot enter ${activePredictionAttemptId}.`,
      );
    }
  };
  const flush = (): void => {
    while (order.length > 0) {
      const hostToolCallId = order[0];
      const transaction = transactions.get(hostToolCallId);
      if (!transaction || !transaction.terminal) break;
      if (transaction.emitted) {
        order.shift();
        continue;
      }
      if (!transaction.started) {
        throw new Error(`Host tool-call transaction ${hostToolCallId} has no durable start.`);
      }
      if (transaction.terminal === "end" && !transaction.name) {
        throw new Error(`Host tool-call transaction ${hostToolCallId} ended without a name.`);
      }
      const output = [
        transaction.startEvent,
        ...(transaction.nameEvent ? [transaction.nameEvent] : []),
        ...transaction.argumentFragments,
        transaction.terminalEvent,
      ].filter(Boolean);
      transaction.emittedEvents = output;
      transaction.emitted = true;
      emittedTransactions.add(hostToolCallId);
      for (const outputEvent of output) emit(outputEvent);
      order.shift();
    }
  };
  const validateTransactionIdentity = (
    event: HostToolCallLifecycleEvent,
    transaction: HostToolCallTransaction,
  ): void => {
    const identityFields: Array<[string, unknown, unknown]> = [
      ["predictionAttemptId", event.predictionAttemptId, transaction.predictionAttemptId],
      ["modelLocalCallId", event.modelLocalCallId, transaction.modelLocalCallId],
      ["rawToolCallId", event.rawToolCallId, transaction.rawToolCallId],
      ["source", event.source, transaction.source],
    ];
    for (const [field, incomingValue, existingValue] of identityFields) {
      const incoming = text(incomingValue, 240);
      const existing = text(existingValue, 240);
      if (incoming && existing && incoming !== existing) {
        anomaly(
          event,
          `Conflicting ${field} for host tool-call ${transaction.hostToolCallId}.`,
        );
      }
    }
  };
  const record = (event: HostToolCallLifecycleEvent): boolean => {
    const hostToolCallId = eventHostId(event);
    if (!active) {
      report(event, false, "late_callback_discarded", "host lifecycle already settled");
      return false;
    }
    if (!hostToolCallId) return anomaly(event, "Tool-call lifecycle event has no hostToolCallId.");
    try {
      ensureAttempt(event);
    } catch (error: any) {
      if (String(error?.message || "").includes("stale prediction attempt")) return false;
      throw error;
    }
    const stage = event.stage;
    let transaction = transactions.get(hostToolCallId);
    if (stage === "start") {
      if (!transaction) transaction = makeTransaction(event);
      if (transaction.terminal) {
        if (transaction.rawToolCallId === text(event.rawToolCallId || event.toolCallId, 240)) {
          report(event, false, "duplicate_start_coalesced");
          return false;
        }
        return anomaly(event, `Conflicting duplicate start for host tool-call ${hostToolCallId}.`);
      }
      if (transaction.started) {
        const incomingRawId = text(event.rawToolCallId || event.toolCallId, 240);
        if (incomingRawId && transaction.rawToolCallId && incomingRawId !== transaction.rawToolCallId) {
          return anomaly(event, `Conflicting duplicate start for host tool-call ${hostToolCallId}.`);
        }
        report(event, false, "duplicate_start_coalesced");
        return false;
      }
      transaction.started = true;
      transaction.rawToolCallId = text(event.rawToolCallId || event.toolCallId, 240);
      transaction.toolCallId = text(event.toolCallId || transaction.toolCallId, 240);
      transaction.startEvent = lifecycleEvent(event, transaction);
      report(event, true, "started");
      return true;
    }
    if (!transaction) {
      if (stage === "name" && event.durableIdentity === true) {
        transaction = makeTransaction(event, true);
      } else if (stage === "failure" && event.allowImplicitStart === true) {
        transaction = makeTransaction(event, true);
      } else {
        return anomaly(
          event,
          `Tool-call callback ${hostToolCallId} received ${stage} before a durable start.`,
        );
      }
    }
    validateTransactionIdentity(event, transaction);
    if (transaction.terminal) {
      const replayIdentity = event.durableIdentity === true
        || /(?:^|_)replay$/u.test(text(event.source || "", 80));
      if (replayIdentity && transaction.emitted) {
        if (stage === "name" && normalizedName(transaction.name) === normalizedName(event.name || event.toolName)) {
          report(event, false, "replay_name_coalesced");
          return false;
        }
        if (stage === "args") {
          const content = String(event.content || "");
          const prior = transaction.argumentFragments.some((fragment) => String(fragment.content || "") === content);
          if (prior) {
            report(event, false, "replay_args_coalesced");
            return false;
          }
        }
      }
      if (stage === "end" && transaction.terminal === "end") {
        const fingerprint = core.sha256(core.stableStringify(event.request || {}));
        if (transaction.requestFingerprint === fingerprint) {
          report(event, false, replayIdentity ? "replay_end_coalesced" : "duplicate_end_coalesced");
          return false;
        }
      }
      if (stage === "failure" && transaction.terminal === "failure"
          && String(transaction.terminalEvent?.error || "") === String(event.error || "")) {
        report(event, false, replayIdentity ? "replay_failure_coalesced" : "duplicate_failure_coalesced");
        return false;
      }
      return anomaly(event, `Tool-call callback ${hostToolCallId} received ${stage} after terminal ${transaction.terminal}.`);
    }
    if (stage === "name") {
      const name = text(event.name || event.toolName, 240);
      if (!name) return anomaly(event, `Tool-call callback ${hostToolCallId} received an empty name.`);
      if (transaction.name !== undefined) {
        if (normalizedName(transaction.name) === normalizedName(name)) {
          report(event, false, "duplicate_name_coalesced");
          return false;
        }
        return anomaly(
          event,
          `Conflicting duplicate tool-call name for host tool-call ${hostToolCallId}: ${transaction.name} != ${name}`,
        );
      }
      transaction.name = name;
      transaction.nameEvent = lifecycleEvent({ ...event, name }, transaction);
      report(event, true, "named");
      return true;
    }
    if (stage === "args") {
      const content = String(event.content || "");
      if (!content) {
        report(event, false, "empty_argument_fragment_ignored");
        return false;
      }
      const argumentEvent = lifecycleEvent({ ...event, content }, transaction);
      transaction.argumentFragments.push(argumentEvent);
      report(event, true, "argument_fragment_accepted");
      return true;
    }
    if (stage === "end") {
      if (!transaction.started) return anomaly(event, `Tool-call callback ${hostToolCallId} ended before start.`);
      const requestId = text(event.request?.id || event.toolCallId, 240);
      if (transaction.toolCallId && requestId && transaction.toolCallId !== requestId) {
        return anomaly(event, `Host tool-call ${hostToolCallId} ended with a different id.`);
      }
      const requestName = text(event.request?.name || event.toolName || event.name, 240);
      if (!transaction.name || !requestName) {
        return anomaly(event, `Host tool-call ${hostToolCallId} ended without a valid start/name pair.`);
      }
      if (normalizedName(transaction.name) !== normalizedName(requestName)) {
        return anomaly(event, `Host tool-call ${hostToolCallId} ended with a different name.`);
      }
      transaction.requestFingerprint = core.sha256(core.stableStringify(event.request || {}));
      transaction.terminal = "end";
      transaction.terminalEvent = lifecycleEvent({ ...event, request: event.request }, transaction);
      report(event, true, "ended");
      flush();
      return true;
    }
    if (stage === "failure") {
      transaction.terminal = "failure";
      transaction.terminalEvent = lifecycleEvent({
        ...event,
        error: String(event.error || "unknown tool-call failure"),
      }, transaction);
      report(event, true, "failed");
      flush();
      return true;
    }
    return anomaly(event, `Unknown tool-call lifecycle stage ${stage}.`);
  };
  return {
    record,
    replaceArguments(hostToolCallId, args) {
      const transaction = transactions.get(String(hostToolCallId || ""));
      if (!transaction) return false;
      const serialized = JSON.stringify(args || {});
      const fragment = transaction.argumentFragments[0];
      if (fragment) {
        fragment.content = serialized;
        for (const later of transaction.argumentFragments.slice(1)) later.content = "";
      }
      if (transaction.emittedEvents) {
        const emittedArgs = transaction.emittedEvents.filter((event) => event.kind === "args");
        if (emittedArgs[0]) emittedArgs[0].content = serialized;
        for (const later of emittedArgs.slice(1)) later.content = "";
      }
      return Boolean(fragment || transaction.emittedEvents);
    },
    replaceTerminalWithFailure(hostToolCallId, error) {
      const transaction = transactions.get(String(hostToolCallId || ""));
      if (!transaction || transaction.terminal !== "end" || !transaction.emittedEvents) return false;
      const failureEvent: HostToolCallLifecycleEvent = {
        stage: "failure",
        hostToolCallId: transaction.hostToolCallId,
        outerGenerateRequestId: transaction.outerGenerateRequestId,
        predictionAttemptId: transaction.predictionAttemptId,
        modelLocalCallId: transaction.modelLocalCallId,
        rawToolCallId: transaction.rawToolCallId,
        toolCallId: transaction.toolCallId,
        toolName: transaction.name || "",
        source: transaction.source,
        error: String(error || "tool call rejected by control plane"),
      };
      const replacement = lifecycleEvent(failureEvent, transaction);
      const terminalIndex = transaction.emittedEvents.findIndex((event) => event.kind === "end");
      if (terminalIndex < 0) return false;
      const priorEvent = transaction.emittedEvents[terminalIndex];
      for (const key of Object.keys(priorEvent)) delete priorEvent[key];
      Object.assign(priorEvent, replacement);
      transaction.terminal = "failure";
      transaction.terminalEvent = priorEvent;
      report(failureEvent, true, "control_plane_rejection_terminal");
      return true;
    },
    setEmitter(nextEmit) {
      emit = typeof nextEmit === "function" ? nextEmit : () => {};
    },
    setActivePredictionAttempt(predictionAttemptId) {
      activePredictionAttemptId = text(predictionAttemptId, 160);
    },
    closePredictionAttempt(predictionAttemptId) {
      if (!predictionAttemptId || activePredictionAttemptId === text(predictionAttemptId, 160)) {
        activePredictionAttemptId = "";
      }
    },
    settle(_reason = "settled") {
      active = false;
    },
    diagnostics() {
      return diagnosticLog.map((item) => ({ ...item }));
    },
    snapshot() {
      const completed = [...emittedTransactions]
        .map((id) => transactions.get(id))
        .filter(Boolean) as HostToolCallTransaction[];
      return {
        acceptedHostTransactionCount: completed.length,
        hostStartCount: completed.filter((item) => Boolean(item.startEvent)).length,
        hostNameCount: completed.filter((item) => Boolean(item.nameEvent)).length,
        hostTerminalCount: completed.filter((item) => Boolean(item.terminalEvent)).length,
        lmStudioNameEventCount: completed.filter((item) => Boolean(item.nameEvent)).length,
        transactions: completed.map((item) => ({
          hostToolCallId: item.hostToolCallId,
          predictionAttemptId: item.predictionAttemptId,
          modelLocalCallId: item.modelLocalCallId,
          rawToolCallId: item.rawToolCallId,
          toolCallId: item.toolCallId,
          toolName: item.name || "",
          source: item.source,
          started: item.started,
          name: Boolean(item.nameEvent),
          argumentFragments: item.argumentFragments.length,
          terminal: item.terminal,
          emitted: item.emitted,
        })),
      };
    },
  };
}

type HostFacingLifecycleCounts = {
  start: number;
  name: number;
  args: number;
  end: number;
  failure: number;
};

function emitToolLifecycleEvent(
  ctl: GeneratorController,
  event: any,
  onHostFacingEmit?: (event: any) => void,
): void {
  const kind = String(event?.kind || "");
  if (!["start", "name", "args", "end", "failure"].includes(kind)) return;
  // Count at the actual LM Studio callback boundary.  Transaction acceptance
  // is intentionally not used here: buffered events are not host-facing until
  // this function is reached during the durable flush.
  onHostFacingEmit?.(event);
  if (kind === "start") ctl.toolCallGenerationStarted({ toolCallId: event.toolCallId });
  else if (kind === "name") ctl.toolCallGenerationNameReceived(event.name);
  else if (kind === "args") ctl.toolCallGenerationArgumentFragmentGenerated(event.content);
  else if (kind === "end") ctl.toolCallGenerationEnded(event.request);
  else if (kind === "failure") ctl.toolCallGenerationFailed(new Error(event.error));
}

function debugAgentLog(hypothesisId: string, location: string, message: string, data: Record<string, unknown>) {
  // #region agent log
  try {
    if (process.env.LMS_CONTEXT_COMPACTOR_DEBUG_INGEST !== "1") return;
    const fs = require("node:fs") as typeof import("node:fs");
    const payload = {
      sessionId: String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_SESSION_ID || "context-compactor"),
      runId: String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_RUN || "release-harden"),
      hypothesisId,
      location,
      message,
      data,
      timestamp: Date.now(),
    };
    const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG;
    if (debugLog) {
      fs.appendFileSync(debugLog, `${JSON.stringify(payload)}\n`);
    }
    const endpoint = String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_INGEST_URL || "").trim();
    if (endpoint) {
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": payload.sessionId },
        body: JSON.stringify(payload),
      }).catch(() => {});
    }
  } catch {
    /* ignore */
  }
  // #endregion
}

function tryInjectSessionMarker(history: Chat, marker: string): boolean {
  const tag = core.formatSessionMarker(marker);
  if (!tag) return false;
  try {
    const messages = history.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const text = String(message.getText() || "");
      if (core.SESSION_MARKER_RE.test(text)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${tag}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${text}\n${tag}`);
        return true;
      }
    }
    if (typeof (history as any).append === "function") {
      (history as any).append("system", tag);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

async function loadCheckpointBestEffort(sessionId: string): Promise<any | null> {
  try {
    const checkpoint = await store.loadCheckpoint(sessionId);
    if (checkpoint && !core.validateCheckpoint(checkpoint)) {
      console.warn(`[unreal-context-compactor] Ignoring invalid checkpoint for ${sessionId}.`);
      return null;
    }
    return checkpoint;
  } catch (error: any) {
    console.warn(`[unreal-context-compactor] Checkpoint load failed; continuing without it: ${error?.message || error}`);
    return null;
  }
}

async function saveCheckpointBestEffort(
  sessionId: string,
  checkpoint: any,
): Promise<{ ok: boolean; error?: string; code?: string }> {
  try {
    await store.saveCheckpoint(sessionId, checkpoint);
    return { ok: true };
  } catch (error: any) {
    const message = String(error?.message || error);
    console.warn(`[unreal-context-compactor] Checkpoint save failed: ${message}`);
    return { ok: false, error: message, code: String(error?.code || "") };
  }
}

async function persistCheckpoint(
  sessionId: string,
  checkpoint: any,
  required: boolean,
  stage: string,
): Promise<void> {
  const saved = await saveCheckpointBestEffort(sessionId, checkpoint);
  if (!saved.ok && (required || String(saved.code || "") === "STALE_CHECKPOINT_WRITER")) {
    const persistenceError: any = new Error(
      `Context safety checkpoint could not be persisted (${stage}): ${saved.error || "unknown error"}. `
      + "Generation was stopped before unsafe output was committed.",
    );
    persistenceError.code = String(saved.code || "CHECKPOINT_PERSIST_FAILED");
    throw persistenceError;
  }
}

async function appendEventBestEffort(sessionId: string, event: any): Promise<void> {
  try {
    await store.appendEvent(sessionId, event);
  } catch (error: any) {
    console.warn(`[unreal-context-compactor] Telemetry write failed; generation will continue: ${error?.message || error}`);
  }
}

function fragmentOptions(fragment: any): any {
  return {
    tokenCount: Number.isFinite(Number(fragment?.tokensCount)) ? Number(fragment.tokensCount) : undefined,
    containsDrafted: typeof fragment?.containsDrafted === "boolean" ? fragment.containsDrafted : undefined,
    reasoningType: fragment?.reasoningType,
    isStructural: typeof fragment?.isStructural === "boolean" ? fragment.isStructural : undefined,
  };
}

function plainMessages(history: Chat): ChatMessage[] {
  return history.getMessagesArray();
}

function snapshotToChatContent(snapshot: any): any[] {
  const role = String(snapshot?.role || "assistant");
  const text = String(snapshot?.text || "");
  const toolCalls = Array.isArray(snapshot?.toolCalls) ? snapshot.toolCalls : [];
  const toolResults = Array.isArray(snapshot?.toolResults) ? snapshot.toolResults : [];
  if (role === "tool") {
    return toolResults.map((result: any) => ({
      type: "toolCallResult",
      toolCallId: result.toolCallId || null,
      content: String(result.content || ""),
    }));
  }
  const content: any[] = [];
  if (text) content.push({ type: "text", text });
  for (const call of toolCalls) {
    content.push({
      type: "toolCallRequest",
      toolCallRequest: {
        id: call.id || null,
        type: "function",
        name: call.name || "",
        arguments: call.arguments || {},
      },
    });
  }
  if (content.length === 0) content.push({ type: "text", text: "" });
  return content;
}

function buildCompactedChat(
  history: Chat,
  checkpoint: any,
  recentTurns: number,
  options: {
    trailingMetaUser?: ChatMessage | null;
    maxCurrentTurnMessages?: number;
  } = {},
): Chat {
  const source = plainMessages(history);
  const trailingMetaText = options.trailingMetaUser
    ? String(options.trailingMetaUser.getText() || "").trim()
    : "";
  const snapshots = core.compactSnapshots(source, checkpoint, {
    recentCompleteTurns: recentTurns,
    maxCurrentTurnMessages: options.maxCurrentTurnMessages,
    trailingMetaUser: trailingMetaText
      ? { role: "user", text: trailingMetaText, toolCalls: [], toolResults: [] }
      : undefined,
  });

  // LM Studio's applyPromptTemplate/respond IPC only serializes the inbound history
  // Chat (or an asMutableCopy of it). Chat.empty()/Chat.from() created inside the
  // generator produce getText()-visible messages but ~10-token empty prompts.
  const mutableCopy = typeof (history as any).asMutableCopy === "function"
    ? (history as any).asMutableCopy()
    : null;
  const result: Chat = mutableCopy || Chat.empty();
  while (result.getLength() > 0) result.pop();
  for (const snapshot of snapshots) {
    const role = String(snapshot?.role || "assistant");
    const content = snapshotToChatContent(snapshot);
    if (role === "tool" && content.length === 0) continue;
    if ((role === "system" || role === "user" || role === "assistant")
      && content.every((part: any) => part?.type === "text" && !String(part.text || "").trim())
      && !content.some((part: any) => part?.type === "toolCallRequest")) {
      continue;
    }
    if (mutableCopy) {
      result.append({ role, content } as any);
    } else if (role === "system" || role === "user" || role === "assistant") {
      const text = content.filter((part: any) => part?.type === "text").map((part: any) => part.text).join("");
      if (role === "assistant" && content.some((part: any) => part?.type === "toolCallRequest")) {
        result.append({ role, content } as any);
      } else {
        result.append(role, text);
      }
    } else {
      result.append({ role, content } as any);
    }
  }

  const users = result.getMessagesArray().filter((m) => m.getRole() === "user");
  const systems = result.getMessagesArray().filter((m) => m.getRole() === "system");
  debugAgentLog("H18", "generator.ts:buildCompactedChat", "compacted chat via asMutableCopy", {
    recentTurns,
    usedMutableCopy: Boolean(mutableCopy),
    snapshotCount: snapshots.length,
    resultLength: result.getMessagesArray().length,
    systemCount: systems.length,
    systemLen: String(systems[0]?.getText() || "").length,
    userCount: users.length,
    latestUserTextLen: String(users.at(-1)?.getText() || "").trim().length,
    hasTrailingMeta: Boolean(trailingMetaText),
  });

  return result;
}

function measureCurrentTurnLength(history: Chat): number {
  const source = plainMessages(history);
  let latestUserIndex = -1;
  for (let index = 0; index < source.length; index += 1) {
    const message = source[index];
    const text = String(message.getText() || "");
    if (message.getRole() === "user" && text.trim() && !core.isMetaUserMessage(text)) {
      latestUserIndex = index;
    }
  }
  if (latestUserIndex < 0) return 0;
  let count = 0;
  for (let index = latestUserIndex + 1; index < source.length; index += 1) {
    const message = source[index];
    const role = message.getRole();
    const text = String(message.getText() || "");
    if (role === "system") continue;
    if (role === "user" && core.isMetaUserMessage(text)) continue;
    count += 1;
  }
  return count;
}

async function compactToTarget(
  model: any,
  history: Chat,
  checkpoint: any,
  config: any,
  contextLength: number,
  reservedTokens: number,
  options: {
    trailingMetaUser?: ChatMessage | null;
    maxCurrentTurnMessages?: number | null;
    measure?: (operation: () => Promise<any> | any) => Promise<any>;
    exhaustionContext?: Record<string, unknown>;
  } = {},
): Promise<{
  chat: Chat;
  inputTokens: number;
  remainingTokens: number;
  retainedTurns: number;
  currentTurnCap: number | null;
  exactTemplateMeasurements: number;
  measurementDurationMs: number;
  targetReached: boolean;
  removedCurrentTurnMessages: number;
}> {
  const measurementStartedAt = performance.now();
  let retainedTurns = Math.max(0, Math.floor(Number(config.recentCompleteTurns || 0)));
  const requestedCurrentTurnCap = options.maxCurrentTurnMessages;
  let currentTurnCap: number | null = typeof requestedCurrentTurnCap === "number"
    && Number.isFinite(requestedCurrentTurnCap)
    && requestedCurrentTurnCap >= 0
    ? Math.floor(requestedCurrentTurnCap)
    : null;
  let best: {
    chat: Chat;
    inputTokens: number;
    remainingTokens: number;
    retainedTurns: number;
    currentTurnCap: number | null;
    exactTemplateMeasurements: number;
    measurementDurationMs: number;
    targetReached: boolean;
    removedCurrentTurnMessages: number;
  } | null = null;
  const target = Math.max(Number(config.hardRemainingTokens), Number(config.targetRemainingTokensAfterCompaction));
  const hard = Number(config.hardRemainingTokens);
  const currentTurnLength = measureCurrentTurnLength(history);

  const candidateKeys = new Set<string>();
  const candidates: Array<{ retainedTurns: number; currentTurnCap: number | null }> = [];
  const addCandidate = (candidateRetainedTurns: number, candidateCurrentTurnCap: number | null) => {
    const normalized = {
      retainedTurns: Math.max(0, Math.floor(candidateRetainedTurns)),
      currentTurnCap: candidateCurrentTurnCap === null
        ? null
        : Math.max(0, Math.floor(candidateCurrentTurnCap)),
    };
    const key = `${normalized.retainedTurns}:${normalized.currentTurnCap === null ? "all" : normalized.currentTurnCap}`;
    if (candidateKeys.has(key) || candidates.length >= 3) return;
    candidateKeys.add(key);
    candidates.push(normalized);
  };
  addCandidate(retainedTurns, currentTurnCap);
  addCandidate(0, currentTurnLength > 0
    ? Math.max(0, Math.min(currentTurnLength, Math.floor(currentTurnLength / 4)))
    : 0);
  addCandidate(0, 0);

  let exactTemplateMeasurements = 0;
  const measurementSummaries: Array<Record<string, number | null>> = [];
  for (const candidate of candidates) {
    retainedTurns = candidate.retainedTurns;
    currentTurnCap = candidate.currentTurnCap;
    const chat = buildCompactedChat(history, checkpoint, retainedTurns, {
      trailingMetaUser: options.trailingMetaUser || null,
      maxCurrentTurnMessages: currentTurnCap === null ? undefined : currentTurnCap,
    });
    let formatted: string;
    let inputTokens: number;
    try {
      const measure = options.measure || ((operation: () => Promise<any> | any) => (
        Promise.resolve().then(operation)
      ));
      formatted = await measure(() => model.applyPromptTemplate(chat));
      inputTokens = await measure(() => model.countTokens(formatted));
      exactTemplateMeasurements += 1;
    } catch (error) {
      debugAgentLog("H14", "generator.ts:compactToTarget", "applyPromptTemplate failed", {
        retainedTurns,
        chatLen: chat.getMessagesArray().length,
        roles: chat.getMessagesArray().map((m) => m.getRole()),
        userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").length,
        error: String((error as any)?.message || error).slice(0, 300),
      });
      throw error;
    }
    debugAgentLog("H14", "generator.ts:compactToTarget", "compact iteration tokens", {
      retainedTurns,
      inputTokens,
      chatLen: chat.getMessagesArray().length,
      systemCount: chat.getMessagesArray().filter((m) => m.getRole() === "system").length,
      userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").trim().length,
      fmtPreview: String(formatted || "").slice(0, 160),
    });
    const userPreview = String(
      chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "",
    ).trim();
    const promptLostUser = Boolean(
      userPreview.length >= 8
      && !String(formatted).includes(userPreview.slice(0, Math.min(16, userPreview.length))),
    );
    if (inputTokens < 20 || promptLostUser) {
      throw new Error(
        `Context compaction produced an near-empty/desynced model prompt (${inputTokens} tokens`
        + `${promptLostUser ? ", user text missing from template" : ""}). `
        + "Refusing to continue with an empty/amnesiac chat.",
      );
    }
    const remainingTokens = Number(contextLength) - Number(inputTokens) - Number(reservedTokens);
    measurementSummaries.push({
      retainedTurns,
      currentTurnCap,
      inputTokens: finiteTelemetryNumber(inputTokens),
      remainingTokens: finiteTelemetryNumber(remainingTokens),
    });
    if (!best || remainingTokens > best.remainingTokens) {
      best = {
        chat,
        inputTokens,
        remainingTokens,
        retainedTurns,
        currentTurnCap,
        exactTemplateMeasurements,
        measurementDurationMs: performance.now() - measurementStartedAt,
        targetReached: remainingTokens >= target,
        removedCurrentTurnMessages: currentTurnCap === null
          ? 0
          : Math.max(0, currentTurnLength - currentTurnCap),
      };
    }
    debugAgentLog("H8c", "generator.ts:compactToTarget", "compact iteration", {
      retainedTurns,
      currentTurnLength,
      currentTurnCap,
      inputTokens,
      remainingTokens,
      target,
      hard,
      chatLen: chat.getMessagesArray().length,
      hasTrailingMeta: Boolean(options.trailingMetaUser),
    });
    if (remainingTokens >= target) break;
  }

  if (!best) throw new Error("Context compaction could not construct a model-facing chat.");
  best.exactTemplateMeasurements = exactTemplateMeasurements;
  best.measurementDurationMs = performance.now() - measurementStartedAt;
  best.targetReached = best.remainingTokens >= target;
  if (best.remainingTokens < hard) {
    const error: any = new Error(
      `Context remains below the hard safety margin after maximum compaction: ${best.remainingTokens} tokens remain. `
      + "A bounded partial report is required before more evidence collection.",
    );
    error.code = "CONTEXT_COMPACTION_EXHAUSTED";
    const supplied = options.exhaustionContext && typeof options.exhaustionContext === "object"
      ? options.exhaustionContext : {};
    error.context = {
      hardRemainingTokens: finiteTelemetryNumber(hard, 0, 0),
      targetRemainingTokens: finiteTelemetryNumber(target, 0, 0),
      remainingTokens: finiteTelemetryNumber(best.remainingTokens),
      inputTokens: finiteTelemetryNumber(best.inputTokens, 0, 0),
      inputTokensBefore: finiteTelemetryNumber(
        (supplied as any).inputTokensBefore ?? best.inputTokens, 0, 0,
      ),
      bestInputTokensAfter: finiteTelemetryNumber(best.inputTokens, 0, 0),
      systemPromptTokens: (supplied as any).systemPromptTokens !== null
        && (supplied as any).systemPromptTokens !== undefined
        && Number.isFinite(Number((supplied as any).systemPromptTokens))
        ? finiteTelemetryNumber((supplied as any).systemPromptTokens, 0, 0) : null,
      checkpointProjectionTokens: (supplied as any).checkpointProjectionTokens !== null
        && (supplied as any).checkpointProjectionTokens !== undefined
        && Number.isFinite(Number((supplied as any).checkpointProjectionTokens))
        ? finiteTelemetryNumber((supplied as any).checkpointProjectionTokens, 0, 0) : null,
      toolSchemaTokens: finiteTelemetryNumber((supplied as any).toolSchemaTokens, 0, 0),
      outputReserveTokens: finiteTelemetryNumber((supplied as any).outputReserveTokens, 0, 0),
      toolResultReserveTokens: finiteTelemetryNumber((supplied as any).toolResultReserveTokens, 0, 0),
      safetyMarginTokens: finiteTelemetryNumber((supplied as any).safetyMarginTokens, 0, 0),
      retainedTurns: Math.floor(finiteTelemetryNumber(best.retainedTurns, 0, 0)),
      currentTurnCap: best.currentTurnCap === null
        ? null : Math.floor(finiteTelemetryNumber(best.currentTurnCap, 0, 0)),
      retainedRawMessageCount: Math.max(0, best.chat.getMessagesArray().length),
      retainedEvidenceCount: Math.floor(finiteTelemetryNumber(
        (supplied as any).retainedEvidenceCount, 0, 0,
      )),
      exactTemplateMeasurements,
      measurements: measurementSummaries.slice(0, 3),
      objectiveHash: String(checkpoint?.objectiveHash || ""),
      checkpointGeneration: Math.floor(finiteTelemetryNumber(
        checkpoint?.checkpointGeneration, 0, 0,
      )),
      taskSessionId: String((supplied as any).taskSessionId || "").slice(0, 160),
      controlEpoch: Number.isSafeInteger(Number((supplied as any).controlEpoch))
        ? Number((supplied as any).controlEpoch) : null,
      storeRevision: Math.floor(finiteTelemetryNumber((supplied as any).storeRevision, 0, 0)),
      compactionGeneration: Math.floor(finiteTelemetryNumber(
        (supplied as any).compactionGeneration, 0, 0,
      )),
      activeRequiredTool: String((supplied as any).activeRequiredTool || "").slice(0, 160),
      recoveryStrategy: String(
        (supplied as any).recoveryStrategy || "bounded_partial_report_then_resume_frontier",
      ).slice(0, 96),
    };
    throw error;
  }
  return best;
}
function requestedToolName(request: any): string {
  return String(request?.name || "").trim();
}

function observedToolResultCount(messages: ChatMessage[], toolName: string): number {
  const snapshots = core.snapshotMessages(messages);
  const callNames = new Map<string, string>();
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      if (call?.id) callNames.set(String(call.id), String(call.name || ""));
    }
  }
  let count = 0;
  for (const snapshot of snapshots) {
    for (const result of snapshot.toolResults || []) {
      const matchedName = String(
        result?.name
        || callNames.get(String(result?.toolCallId || ""))
        || "",
      );
      if (toolNamesMatch(toolName, matchedName)) count += 1;
    }
  }
  return count;
}

function observedToolResultCountForNames(messages: ChatMessage[], toolNames: string[]): number {
  const snapshots = core.snapshotMessages(messages);
  const callNames = new Map<string, string>();
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      if (call?.id) callNames.set(String(call.id), String(call.name || ""));
    }
  }
  let count = 0;
  for (const snapshot of snapshots) {
    for (const result of snapshot.toolResults || []) {
      const matchedName = String(
        result?.name
        || callNames.get(String(result?.toolCallId || ""))
        || "",
      );
      if (toolNames.some((name) => toolNamesMatch(name, matchedName))) count += 1;
    }
  }
  return count;
}

function successfulToolResultCountSinceLatest(
  messages: ChatMessage[],
  boundaryToolName: string,
  toolNames: string[],
): number {
  const snapshots = core.snapshotMessages(messages);
  const callNames = new Map<string, string>();
  let count = 0;
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      if (call?.id) callNames.set(String(call.id), String(call.name || ""));
    }
    for (const result of snapshot.toolResults || []) {
      const matchedName = String(
        result?.name
        || callNames.get(String(result?.toolCallId || ""))
        || "",
      );
      if (toolNamesMatch(boundaryToolName, matchedName)) {
        count = 0;
        continue;
      }
      if (
        core.toolResultSucceeded(result)
        && toolNames.some((name) => toolNamesMatch(name, matchedName))
      ) {
        count += 1;
      }
    }
  }
  return count;
}

function toolNamesMatch(expected: string, actual: string): boolean {
  return core.toolNamesMatch(expected, actual);
}

function guardGeneratorAbort(ctl: GeneratorController): void {
  const guard = (ctl as any)?.guardAbort;
  if (typeof guard === "function") {
    guard.call(ctl);
    return;
  }
  const signal = (ctl as any)?.abortSignal;
  if (signal?.aborted) {
    throw signal.reason instanceof Error
      ? signal.reason
      : new Error("Generation aborted");
  }
}

type PredictionSupervisionOptions = {
  wallClockMs?: number;
  noProgressMs?: number;
  initialNoProgressMs?: number;
  getLastProgressAt?: () => number;
  getInferenceStartedAt?: () => number | null;
  beforeCancel?: () => void;
  onResultSettled?: () => void;
  failurePromise?: Promise<never>;
  getFailure?: () => unknown;
};

function initialPredictionNoProgressMs(
  promptTokens: number,
  configuredNoProgressMs: number,
  wallClockMs: number,
): number {
  const noProgressMs = Math.max(0, Number(configuredNoProgressMs || 0));
  const wallMs = Math.max(0, Number(wallClockMs || 0));
  // LM Studio cannot surface semantic callbacks while the backend is still
  // evaluating the prompt. Treating that opaque prefill interval as a
  // serialization stall produced false cancellations on real 30k+ token MCP
  // prompts. Production values (the schema minimum is five seconds) receive
  // a conservative prompt-size allowance; sub-five-second deterministic test
  // overrides retain their exact historical timing.
  if (noProgressMs < 5_000) return noProgressMs;
  const tokens = Math.max(0, Math.floor(Number(promptTokens || 0)));
  const estimatedPrefillMs = Math.ceil((tokens / 128) * 1_000);
  const bounded = Math.max(noProgressMs, estimatedPrefillMs);
  return wallMs > 0 ? Math.min(wallMs, bounded) : bounded;
}

function predictionSupervisorError(code: string, elapsedMs: number): Error {
  const error: any = new Error(
    code === "PREDICTION_NO_PROGRESS_EXCEEDED"
      ? `Model prediction made no semantic progress for ${Math.max(1, Math.floor(elapsedMs / 1000))} seconds.`
      : code === "PREDICTION_PREFILL_NO_PROGRESS_EXCEEDED"
      ? `Model prompt prefill produced no observable inference activity for ${Math.max(1, Math.floor(elapsedMs / 1000))} seconds.`
      : `Model prediction exceeded its ${Math.max(1, Math.floor(elapsedMs / 1000))}-second wall-clock budget.`,
  );
  error.code = code;
  error.elapsedMs = elapsedMs;
  return error;
}

async function boundedPredictionSetupValue<T>(
  ctl: GeneratorController,
  timeoutMs: number,
  attemptStartedAt: number,
  operation: () => Promise<T> | T,
): Promise<T> {
  const signal = (ctl as any)?.abortSignal;
  const boundedMs = Math.max(0, Number(timeoutMs || 0));
  const setupStartedAt = performance.now();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let abortListener: (() => void) | null = null;
  const operationResult = Promise.resolve().then(() => {
    guardGeneratorAbort(ctl);
    return operation();
  });
  const boundary = new Promise<never>((_resolve, reject) => {
    if (signal && typeof signal.addEventListener === "function") {
      abortListener = () => reject(
        signal.reason instanceof Error ? signal.reason : new Error("Generation aborted"),
      );
      if (signal.aborted) {
        abortListener();
        return;
      }
      signal.addEventListener("abort", abortListener, { once: true });
    }
    if (boundedMs > 0) {
      timer = setTimeout(() => reject(predictionSupervisorError(
        "PREDICTION_WALL_CLOCK_EXCEEDED",
        performance.now() - attemptStartedAt,
      )), boundedMs);
    }
  });
  try {
    const value = await Promise.race([operationResult, boundary]);
    guardGeneratorAbort(ctl);
    if (boundedMs > 0 && performance.now() - setupStartedAt >= boundedMs) {
      throw predictionSupervisorError(
        "PREDICTION_WALL_CLOCK_EXCEEDED",
        performance.now() - attemptStartedAt,
      );
    }
    return value;
  } finally {
    if (timer) clearTimeout(timer);
    if (abortListener && signal && typeof signal.removeEventListener === "function") {
      signal.removeEventListener("abort", abortListener);
    }
  }
}

function compactionWorkflowProgressSignature(checkpoint: any): string {
  const stableUniqueFrontier = (entries: any[]): any[] => {
    const byIdentity = new Map<string, any>();
    for (const entry of entries) {
      const identity = core.stableStringify(entry);
      byIdentity.set(identity, entry);
    }
    return [...byIdentity.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, entry]) => entry);
  };
  const workingSetFrontier = stableUniqueFrontier(Array.isArray(checkpoint?.workingSet)
    ? checkpoint.workingSet.map((entry: any) => ({
      path: String(entry?.path || ""),
      contentHash: String(entry?.contentHash || ""),
      coveredRanges: Array.isArray(entry?.coveredRanges) ? entry.coveredRanges : [],
      truncated: Boolean(entry?.truncated),
    }))
    : []);
  const evidenceFrontier = stableUniqueFrontier(Array.isArray(checkpoint?.evidenceFacts)
    ? checkpoint.evidenceFacts.filter((entry: any) => (
      String(entry?.evidenceHash || "").length > 0
      || String(entry?.contentHash || "").length > 0
    )).map((entry: any) => ({
      tool: String(entry?.tool || ""),
      path: String(entry?.path || ""),
      evidenceHash: String(entry?.evidenceHash || ""),
      contentHash: String(entry?.contentHash || ""),
      coveredRanges: Array.isArray(entry?.coveredRanges) ? entry.coveredRanges : [],
      exactContentTruncated: Boolean(entry?.exactContentTruncated),
      repeatDetected: Boolean(entry?.repeatDetected),
    }))
    : []);
  const directoryFrontier = stableUniqueFrontier(Array.isArray(checkpoint?.evidenceFacts)
    ? checkpoint.evidenceFacts.filter((entry: any) => (
      String(entry?.tool || "").toLowerCase().endsWith("list_directory")
      && String(entry?.path || "").length > 0
    )).map((entry: any) => {
      const entries: string[] = [...new Set<string>(
        (Array.isArray(entry?.entries) ? entry.entries : [])
          .map((value: any) => String(value || "").trim())
          .filter(Boolean),
      )].sort((left: string, right: string) => (left < right ? -1 : left > right ? 1 : 0));
      return {
        tool: String(entry?.tool || ""),
        path: String(entry?.path || ""),
        entryCount: Math.max(entries.length, Number(entry?.entryCount || 0)),
        entries,
      };
    })
    : []);
  return core.sha256(core.stableStringify({
    objectiveHash: String(checkpoint?.objectiveHash || ""),
    taskSessionId: String(
      checkpoint?.serverControl?.taskSessionId
      || checkpoint?.taskRouteOwnership?.taskSessionId
      || "",
    ),
    controlEpoch: Number(checkpoint?.serverControl?.epoch || 0),
    controlFingerprint: String(checkpoint?.serverControl?.controlFingerprint || ""),
    routeHash: String(checkpoint?.serverControl?.routeHash || checkpoint?.toolRoute?.routeHash || ""),
    requiredNextTool: checkpoint?.requiredNextTool || null,
    mutationGeneration: Number(checkpoint?.mutationGeneration || 0),
    sourceEvidence: checkpoint?.sourceEvidence || null,
    absentEvidence: checkpoint?.absentEvidence || null,
    workingSetFrontier,
    evidenceFrontier,
    directoryFrontier,
    buildState: checkpoint?.buildState || null,
    buildVerification: checkpoint?.buildVerification || null,
    synthesisStatus: String(checkpoint?.synthesisState?.status || ""),
  }));
}

async function predictionResultWithSupervision(
  prediction: any,
  ctl: GeneratorController,
  options: PredictionSupervisionOptions = {},
): Promise<any> {
  guardGeneratorAbort(ctl);
  const signal = (ctl as any)?.abortSignal;
  const startedAt = performance.now();
  const wallClockMs = Math.max(0, Number(options.wallClockMs || 0));
  const noProgressMs = Math.max(0, Number(options.noProgressMs || 0));
  const initialNoProgressMs = Math.max(
    noProgressMs,
    Number(options.initialNoProgressMs || 0),
  );
  const getLastProgressAt = options.getLastProgressAt || (() => startedAt);
  // Direct helper callers historically supervise an already-created
  // prediction handle. The generator supplies an explicit nullable callback
  // so only its opaque prefill phase receives the extended allowance.
  const getInferenceStartedAt = options.getInferenceStartedAt || (() => startedAt + 0.001);
  let abortListener: (() => void) | null = null;
  let wallClockTimer: ReturnType<typeof setTimeout> | null = null;
  let noProgressTimer: ReturnType<typeof setInterval> | null = null;
  let cancellationStarted = false;
  const cancelPredictionOnce = () => {
    if (cancellationStarted) return;
    cancellationStarted = true;
    try {
      options.beforeCancel?.();
    } catch {
      // Callback invalidation is best-effort here; backend cancellation and
      // the supervisor error must still be delivered exactly once.
    }
    try {
      Promise.resolve(prediction?.cancel?.()).catch(() => {});
    } catch {
      // The supervisor still rejects this generator even if the backend's
      // cancellation hook is already closed or throws synchronously.
    }
  };
  const cancellationResult = new Promise<never>((_resolve, reject) => {
    const cancelAndReject = (error: Error) => {
      if (cancellationStarted) return;
      // Settle the supervisor failure before backend cancel can synchronously
      // resolve prediction.result(). The rejection microtask must win the race.
      reject(error);
      cancelPredictionOnce();
    };
    if (signal && typeof signal.addEventListener === "function") {
      abortListener = () => cancelAndReject(
        signal.reason instanceof Error ? signal.reason : new Error("Generation aborted"),
      );
      signal.addEventListener("abort", abortListener, { once: true });
    }
    if (wallClockMs > 0) {
      wallClockTimer = setTimeout(() => cancelAndReject(
        predictionSupervisorError("PREDICTION_WALL_CLOCK_EXCEEDED", performance.now() - startedAt),
      ), wallClockMs);
    }
    if (noProgressMs > 0) {
      const pollMs = Math.max(10, Math.min(1000, Math.floor(noProgressMs / 4)));
      noProgressTimer = setInterval(() => {
        const semanticProgressAt = Number(getLastProgressAt() || startedAt);
        const inferenceStartedAt = Number(getInferenceStartedAt() || 0);
        const inferenceObservable = (
          inferenceStartedAt > startedAt || semanticProgressAt > startedAt
        );
        const baselineAt = inferenceObservable
          ? Math.max(startedAt, inferenceStartedAt, semanticProgressAt)
          : startedAt;
        const applicableLimitMs = inferenceObservable
          ? noProgressMs
          : initialNoProgressMs;
        const stalledMs = performance.now() - baselineAt;
        if (applicableLimitMs > 0 && stalledMs >= applicableLimitMs) {
          cancelAndReject(predictionSupervisorError(
            inferenceObservable
              ? "PREDICTION_NO_PROGRESS_EXCEEDED"
              : "PREDICTION_PREFILL_NO_PROGRESS_EXCEEDED",
            stalledMs,
          ));
        }
      }, pollMs);
    }
  });
  try {
    const callbackFailureResult = options.failurePromise
      ? options.failurePromise.catch((error) => {
        cancelPredictionOnce();
        throw error;
      })
      : new Promise<never>(() => {});
    let rawPredictionResult: unknown;
    try {
      rawPredictionResult = prediction.result();
    } catch (error) {
      options.onResultSettled?.();
      throw error;
    }
    const predictionCompletion = Promise.resolve(rawPredictionResult).then(
      (value) => {
        options.onResultSettled?.();
        return value;
      },
      (error) => {
        options.onResultSettled?.();
        throw error;
      },
    );
    const result = await Promise.race([
      predictionCompletion,
      cancellationResult,
      callbackFailureResult,
    ]);
    // A backend may register on the shared AbortSignal before the supervisor
    // and resolve result() from that listener. Abort must still dominate even
    // when its fulfillment reaction was queued before our rejection reaction.
    if (signal?.aborted) {
      cancelPredictionOnce();
      throw signal.reason instanceof Error
        ? signal.reason
        : new Error("Generation aborted");
    }
    const callbackFailure = options.getFailure?.();
    if (callbackFailure) {
      cancelPredictionOnce();
      throw callbackFailure;
    }
    // A resolved Promise microtask can run before overdue timer callbacks when
    // the event loop was blocked. Recheck both hard deadlines synchronously
    // before accepting output, then cancel exactly once on either violation.
    const completedAt = performance.now();
    const elapsedMs = completedAt - startedAt;
    if (wallClockMs > 0 && elapsedMs >= wallClockMs) {
      cancelPredictionOnce();
      throw predictionSupervisorError("PREDICTION_WALL_CLOCK_EXCEEDED", elapsedMs);
    }
    const semanticProgressAt = Number(getLastProgressAt() || startedAt);
    const inferenceStartedAt = Number(getInferenceStartedAt() || 0);
    const inferenceObservable = (
      inferenceStartedAt > startedAt || semanticProgressAt > startedAt
    );
    const baselineAt = inferenceObservable
      ? Math.max(startedAt, inferenceStartedAt, semanticProgressAt)
      : startedAt;
    const applicableNoProgressMs = inferenceObservable
      ? noProgressMs
      : initialNoProgressMs;
    const stalledMs = completedAt - baselineAt;
    if (applicableNoProgressMs > 0 && stalledMs >= applicableNoProgressMs) {
      cancelPredictionOnce();
      throw predictionSupervisorError(
        inferenceObservable
          ? "PREDICTION_NO_PROGRESS_EXCEEDED"
          : "PREDICTION_PREFILL_NO_PROGRESS_EXCEEDED",
        stalledMs,
      );
    }
    return result;
  } finally {
    if (wallClockTimer) clearTimeout(wallClockTimer);
    if (noProgressTimer) clearInterval(noProgressTimer);
    if (abortListener && signal && typeof signal.removeEventListener === "function") {
      signal.removeEventListener("abort", abortListener);
    }
  }
}

const ARCHITECTURE_GATE_MARKER = "[UNREAL_ARCHITECTURE_VALIDATION_GATE]";
const ARCHITECTURE_SUBMISSION_MARKER = "[UNREAL_ARCHITECTURE_SUBMISSION_REQUIRED]";
const ARCHITECTURE_PAYLOAD_REPAIR_MARKER = "[UNREAL_ARCHITECTURE_PAYLOAD_REPAIR_REQUIRED]";
const ARCHITECTURE_TOOL_NAME = "unreal_architecture_reasoning";
const FEATURE_INTENT_ATOMIC_MARKER = "[UNREAL_FEATURE_INTENT_ATOMIC_GATE]";
const FEATURE_INTENT_EVIDENCE_MARKER = "[UNREAL_FEATURE_INTENT_TARGET_EVIDENCE]";
const FEATURE_INTENT_PAYLOAD_REPAIR_MARKER = "[UNREAL_FEATURE_INTENT_PAYLOAD_REPAIR_REQUIRED]";
const FEATURE_INTENT_RECOVERY_MARKER = "[UNREAL_FEATURE_INTENT_RECOVERY]";
const FEATURE_INTENT_EVIDENCE_REFILL_MARKER = "[UNREAL_FEATURE_INTENT_EVIDENCE_REFILL]";
const FEATURE_INTENT_POST_READ_MARKER = "[UNREAL_FEATURE_INTENT_POST_READ_REEVALUATION]";
const FEATURE_INTENT_TOOL_NAME = "unreal_feature_intent_resolve";
const EVIDENCE_FIRST_CONTRACT_MARKER = "[EVIDENCE_FIRST_CONTRACT_REQUIRED]";
const EVIDENCE_FIRST_CONTRACT_TOOL_NAME = "evidence_first_contract";
const TASK_PLANNER_TOOL_NAME = "unreal_agent_plan";
const TASK_ROUTE_OWNERSHIP_MARKER = "[UNREAL_TASK_ROUTE_OWNERSHIP_GATE]";
const PRE_ROUTE_PLANNER_HANDOFF_MARKER = "[UNREAL_PRE_ROUTE_PLANNER_HANDOFF]";
const INITIAL_ACTIVE_PROJECT_BOOTSTRAP_MARKER = "[UNREAL_INITIAL_ACTIVE_PROJECT_BOOTSTRAP]";
const SOURCE_ANALYSIS_ADMISSION_MARKER = "[UNREAL_SOURCE_ANALYSIS_ADMISSION_GATE]";
const TOOL_CATALOG_REFRESH_MARKER = "[UNREAL_TOOL_CATALOG_REFRESH]";
const SERVER_REQUIRED_TOOL_MARKER = "[UNREAL_SERVER_REQUIRED_TOOL]";
const SERVER_REQUIRED_TOOL_REPAIR_MARKER = "[UNREAL_SERVER_REQUIRED_TOOL_REPAIR]";
const DETACHED_SIDE_QUERY_MARKER = "[UNREAL_DETACHED_SIDE_QUERY]";
const WORKFLOW_STOP_MARKER = "[UNREAL_SERVER_WORKFLOW_STOP]";
const ARCHITECTURE_EVIDENCE_TOOLS = [
  "read_file",
  "read_file_range",
  "read_symbol",
  "unreal_symbol_lookup",
];
const DIRECT_SOURCE_FILE_TOOLS = ["read_file", "read_file_range"];
const DIRECT_SOURCE_READ_RECOVERY_PROPERTIES: Record<string, Record<string, any>> = {
  read_file: {
    path: { type: "string", description: "Server-selected project source path." },
    maxBytes: { type: "number" },
    detailLevel: { type: "string", enum: ["compact", "medium", "large", "full"] },
  },
  read_file_range: {
    path: { type: "string", description: "Server-selected project source path." },
    startLine: { type: "number" },
    endLine: { type: "number" },
    detailLevel: { type: "string", enum: ["compact", "medium", "large", "full"] },
  },
};
const ARCHITECTURE_DISCOVERY_TOOLS = [
  ...ARCHITECTURE_EVIDENCE_TOOLS,
  "search_files",
  "list_directory",
  "unreal_get_active_project",
  "get_workspace_info",
];

function serverControlledDirectReadDefinition(checkpoint: any): any | null {
  const name = String(checkpoint?.requiredNextTool?.name || "").trim();
  const canonicalName = DIRECT_SOURCE_FILE_TOOLS.find((candidate) => toolNamesMatch(candidate, name));
  const args = checkpoint?.requiredNextTool?.args;
  if (
    !canonicalName
    || !args
    || typeof args !== "object"
    || Array.isArray(args)
    || typeof args.path !== "string"
    || !args.path.trim()
  ) {
    return null;
  }
  if (
    canonicalName === "read_file_range"
    && (!Number.isFinite(Number(args.startLine)) || !Number.isFinite(Number(args.endLine)))
  ) {
    return null;
  }

  const supported = DIRECT_SOURCE_READ_RECOVERY_PROPERTIES[canonicalName];
  const unsupportedArgument = Object.keys(args).some((key) => !Object.prototype.hasOwnProperty.call(supported, key));
  if (unsupportedArgument) return null;

  const properties: Record<string, any> = { sessionId: { type: "string" } };
  for (const key of Object.keys(args)) properties[key] = supported[key];
  return {
    type: "function",
    function: {
      name,
      description: (
        "Execute the exact read-only project source request selected by server control. "
        + "All read arguments are injected by the control plane."
      ),
      parameters: {
        type: "object",
        properties,
        required: [],
        additionalProperties: false,
      },
    },
  };
}
const DETACHED_SIDE_QUERY_TOOLS = [
  "get_workspace_info",
  "get_active_project",
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
  "read_unreal_logs",
];

function detachedSideQueryToolAllowed(name: string): boolean {
  return DETACHED_SIDE_QUERY_TOOLS.some((tool) => toolNamesMatch(tool, name));
}

function injectDetachedSideQueryRule(chat: Chat, request: string): boolean {
  const rule = (
    `${DETACHED_SIDE_QUERY_MARKER}\n`
    + `The active write task is suspended while answering this detached read-only request: ${request.slice(0, 800)}\n`
    + "Use only the read-only observation tools currently exposed. Do not call task status/list/control, "
    + "do not complete a pending gate, do not plan or mutate, and do not replace the active task objective. "
    + "Answer from bounded evidence, then stop; a later continuation resumes the suspended task."
  );
  return upsertLeadingSystemRule(chat, DETACHED_SIDE_QUERY_MARKER, rule);
}

function injectWorkflowStopRule(chat: Chat, blocker: any): boolean {
  const errorCode = String(blocker?.errorCode || "SERVER_WORKFLOW_BLOCKED").slice(0, 120);
  const instruction = String(blocker?.agentInstruction || "").trim().slice(0, 800);
  const rule = (
    `${WORKFLOW_STOP_MARKER}\n`
    + `The latest server result stopped the current workflow (${errorCode}) and supplied no tool next action. `
    + "Do not call, propose, or simulate another tool. Give one concise final response that explains the verified "
    + "blocker and the exact missing user/project evidence, then end this turn. Do not claim success or a code change."
    + (instruction ? ` Server instruction: ${instruction}` : "")
  );
  return upsertLeadingSystemRule(chat, WORKFLOW_STOP_MARKER, rule);
}

function workflowStopFinalResponse(blocker: any, objective: string): string {
  const errorCode = String(blocker?.errorCode || "SERVER_WORKFLOW_BLOCKED")
    .replace(/[^A-Z0-9_.:-]/gi, "")
    .slice(0, 120) || "SERVER_WORKFLOW_BLOCKED";
  const korean = /[가-힣]/.test(String(objective || ""));
  if (korean) {
    return (
      `현재 작업은 서버 검증에서 중단되었습니다 (${errorCode}). `
      + "프로젝트 근거로 확인되지 않은 상태·정책을 추측해 구현할 수 없으므로 추가 도구 호출이나 수정을 진행하지 않습니다. "
      + "해당 동작의 소유자, 저장 위치, 기대 규칙에 대한 프로젝트 근거를 제공하거나 명시적으로 새 목표를 지정해 주세요."
    );
  }
  return (
    `The current workflow is stopped by server validation (${errorCode}). `
    + "The required project evidence for the state or policy is missing, so no additional tool call or speculative edit will be attempted. "
    + "Provide the project-owned semantic contract or explicitly set a new objective."
  );
}

function latestUserGoalText(messages: ChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.getRole() !== "user") continue;
    const value = String(message.getText() || "").trim();
    if (value && !core.isMetaUserMessage(value)) return value;
  }
  return "";
}

function latestUserMessageIdentity(messages: ChatMessage[]): string {
  const snapshots = core.snapshotMessages(messages);
  const index = core.findLatestRealUserIndex(snapshots);
  if (index < 0) return core.sha256("missing-user-message");
  return core.sha256(core.stableStringify({
    index,
    message: snapshots[index],
  }));
}

function normalizedDirectProjectStatusGoal(goal: string): string {
  return String(goal || "").normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase();
}

function directProjectStatusResponse(result: any, goal: string): {
  outcome: "success" | "failure";
  errorCode: string;
  responseText: string;
  resultDigest: string;
} {
  const rawContent = String(result?.content || "");
  const payloads = core.parseJsonObjects(rawContent);
  const payload = [...payloads].reverse().find((value: any) => (
    value && typeof value === "object" && !Array.isArray(value)
  ));
  const activeProject = String(
    payload?.activeProject
    || payload?.uprojectPath
    || payload?.projectFile
    || payload?.details?.projectFile
    || "",
  ).trim();
  const inferredProjectName = activeProject
    ? activeProject.replace(/\\/gu, "/").split("/").at(-1)?.replace(/\.uproject$/iu, "") || ""
    : "";
  const projectName = String(
    payload?.projectName || payload?.details?.projectName || inferredProjectName,
  ).trim();
  const succeeded = result?.isError !== true
    && core.toolResultSucceeded(result)
    && payload?.ok !== false
    && Boolean(activeProject);
  const korean = /[가-힣]/u.test(String(goal || ""));
  if (succeeded) {
    return {
      outcome: "success",
      errorCode: "",
      responseText: korean
        ? `현재 활성 프로젝트:\n${projectName || "이름 미확인"}\n${activeProject}`
        : `Current active project:\n${projectName || "Unknown project name"}\n${activeProject}`,
      resultDigest: core.sha256(rawContent),
    };
  }
  const explicitCode = String(payload?.errorCode || payload?.code || "")
    .trim().replace(/[^A-Za-z0-9_.:-]/gu, "_").slice(0, 120);
  let errorCode = "DIRECT_PROJECT_STATUS_INVALID_RESULT";
  if (payload) errorCode = "DIRECT_PROJECT_STATUS_EMPTY";
  if (result?.isError === true) errorCode = "DIRECT_PROJECT_STATUS_TOOL_ERROR";
  if (explicitCode) errorCode = explicitCode;
  return {
    outcome: "failure",
    errorCode,
    responseText: korean
      ? `현재 활성 프로젝트를 확인하지 못했습니다 (${errorCode}). 프로젝트 조회 도구의 결과가 비어 있거나 실패했습니다.`
      : `Could not determine the active project (${errorCode}). The project-status tool returned an empty or failed result.`,
    resultDigest: core.sha256(rawContent),
  };
}

function injectTaskRouteOwnershipRule(chat: Chat, plannerAvailable: boolean): boolean {
  const rule = (
    `${TASK_ROUTE_OWNERSHIP_MARKER}\n`
    + "Project mutation tools are unavailable until an MCP server returns a server-issued task route. "
    + "Never construct, guess, or repair taskAuthorization. "
    + (plannerAvailable
      ? "Before any write, call unreal_agent_plan once with the original user request and continue with its returned route."
      : "The mcp/unreal-rag planner provider is missing from this chat. You may inspect source, but do not claim implementation or attempt writes; report that mcp/unreal-rag must be enabled.")
  );
  return upsertLeadingSystemRule(chat, TASK_ROUTE_OWNERSHIP_MARKER, rule);
}

function architectureContractGoalText(
  messages: ChatMessage[],
  latestGoal: string,
  includeRecoveryHistory: boolean,
): string {
  if (!includeRecoveryHistory) return String(latestGoal || "");
  const goals: string[] = [];
  for (const message of messages) {
    if (message.getRole() !== "user") continue;
    const value = String(message.getText() || "").trim();
    if (!value || core.isMetaUserMessage(value)) continue;
    goals.push(value);
  }
  return goals.slice(-8).join("\n");
}

function requiresArchitectureValidation(goal: string, toolDefinitions: any[]): boolean {
  const hasTool = (toolDefinitions || []).some((tool: any) => {
    const name = tool?.function?.name || tool?.name || "";
    return toolNamesMatch("unreal_architecture_reasoning", name);
  });
  if (!hasTool) return false;
  const textValue = String(goal || "");
  return /authoritative\s+multiplayer|architecture|architectural|structure\s+design|design\s+validation|(?:new|independent|standalone|separate)\s+(?:[\w-]+\s+){0,3}(?:system|subsystem|component|service)|(?:system|subsystem|component|service)\s+(?:design|architecture)|구조\s*설계|설계\s*검증|아키텍처|(?:새(?:로운)?|신규|독립(?:적인)?|별도)\s*(?:\S+\s*){0,3}(?:시스템|서브시스템|컴포넌트|서비스)|(?:시스템|서브시스템|컴포넌트|서비스)(?:으로|을|를|의|\s)*(?:설계|구현|추가|신설)/i.test(textValue);
}

type RequestIntentContext = {
  requestIntent?: any;
  objectiveHash?: string;
  authoritativeObjectiveProjection?: boolean;
};

function matchingRequestIntentContext(goal: string, checkpoint: any): RequestIntentContext {
  const normalizedGoal = String(goal || "").trim();
  const checkpointObjective = String(checkpoint?.objective || "").trim();
  const checkpointObjectiveHash = String(checkpoint?.objectiveHash || "").trim().toLowerCase();
  const authoritativeObjectiveProjection = Boolean(
    normalizedGoal
    && normalizedGoal === checkpointObjective
    && /^[a-f0-9]{64}$/.test(checkpointObjectiveHash)
  );
  const requestIntent = core.matchingRequestIntent(String(goal || ""), {
    requestIntent: checkpoint?.requestIntent,
    objectiveHash: authoritativeObjectiveProjection ? checkpointObjectiveHash : "",
    authoritativeObjectiveProjection,
  });
  return requestIntent
    ? {
      requestIntent,
      ...(authoritativeObjectiveProjection
        ? {
          objectiveHash: checkpointObjectiveHash,
          authoritativeObjectiveProjection: true,
        }
        : {}),
    }
    : {};
}

function requiresTaskRoutePlanning(goal: string, context: RequestIntentContext = {}): boolean {
  return core.classifyMutationIntent(String(goal || ""), context).isMutation === true;
}

function requiresDurableInspectionPlanning(
  goal: string,
  context: RequestIntentContext = {},
): boolean {
  const intent = core.classifySourceWorkIntent(String(goal || ""), context);
  return intent.requiresDurableTask === true && !requiresTaskRoutePlanning(String(goal || ""), context);
}

function requiresFeatureCompletionAudit(goal: string): boolean {
  const source = String(goal || "");
  return /\b(?:current\s+implementation|implementation\s+status|earliest\s+incomplete|first\s+incomplete|what\s+remains)\b|현재\s*(?:구현\s*)?상태|구현\s*상태|가장\s*(?:앞선|이른|먼저인)\s*미완성|아직\s*완료되지\s*않은|미완성\s*(?:기능|단계)/i.test(source);
}

function injectPreRoutePlannerHandoffRule(chat: Chat): boolean {
  const rule = (
    `${PRE_ROUTE_PLANNER_HANDOFF_MARKER}\n`
    + "The bounded pre-route source discovery budget is complete. Call unreal_agent_plan exactly once now with "
    + "the latest real user request. Do not call another read, search, directory, architecture, or evidence tool "
    + "until the server returns taskAuthorization and toolRoute. Never invent those fields."
  );
  return upsertLeadingSystemRule(chat, PRE_ROUTE_PLANNER_HANDOFF_MARKER, rule);
}

function injectInitialActiveProjectBootstrapRule(chat: Chat, toolName: string): boolean {
  const rule = (
    `${INITIAL_ACTIVE_PROJECT_BOOTSTRAP_MARKER}\n`
    + `Call ${toolName} exactly once as the first tool. Do not call workspace, directory, read, search, `
    + "status, or mutation tools in parallel. The active-project response will bind the exact next planner "
    + "action when a project is selected."
  );
  return upsertLeadingSystemRule(chat, INITIAL_ACTIVE_PROJECT_BOOTSTRAP_MARKER, rule);
}

function injectSourceAnalysisAdmissionRule(chat: Chat, plannerAvailable: boolean): boolean {
  const rule = (
    `${SOURCE_ANALYSIS_ADMISSION_MARKER}\n`
    + "This is a project source-work request. A durable server-owned task must admit it before any source evidence "
    + "tool is callable. The only permitted transition is unreal_get_active_project when the project is unresolved, "
    + "then unreal_agent_plan with the exact latest user request copied into request and latestUserMessage. "
    + (plannerAvailable
      ? "Do not call search_files, list_directory, read_file, read_file_range, or read_symbol before that planner result."
      : "The planner provider is unavailable; stop and report the missing planner instead of inspecting source directly.")
  );
  return upsertLeadingSystemRule(chat, SOURCE_ANALYSIS_ADMISSION_MARKER, rule);
}

function injectToolCatalogRefreshRule(chat: Chat, toolName: string): boolean {
  const rule = (
    `${TOOL_CATALOG_REFRESH_MARKER}\n`
    + "A server-owned executor route is active, but LM Studio still has the Unreal Agent provider's pre-route "
    + `tool catalog. Call ${toolName} exactly once now to trigger the provider's tools/list refresh. `
    + "This is catalog synchronization only: do not call health, checkpoint, cancel, read, search, or any RAG tool, "
    + "and do not claim that implementation has started. Continue with the refreshed exact route after its result."
  );
  return upsertLeadingSystemRule(chat, TOOL_CATALOG_REFRESH_MARKER, rule);
}

function injectServerRequiredToolRule(chat: Chat, toolName: string, requiredArgs: any): boolean {
  const boundedArgs = requiredArgs && typeof requiredArgs === "object" && !Array.isArray(requiredArgs)
    ? JSON.stringify(requiredArgs).slice(0, 4_000)
    : "{}";
  const rule = (
    `${SERVER_REQUIRED_TOOL_MARKER}\n`
    + `The latest server result requires ${toolName} now. Call that tool exactly once before any other tool or final answer. `
    + `Required argument constraints: ${boundedArgs}. Do not call health, status, checkpoint, reads, or recovery controls instead.`
  );
  return upsertLeadingSystemRule(chat, SERVER_REQUIRED_TOOL_MARKER, rule);
}

function injectServerRequiredToolRepairRule(
  chat: Chat,
  toolName: string,
  receivedToolNames: string[],
): boolean {
  const received = receivedToolNames.length > 0
    ? receivedToolNames.join(", ")
    : "prose/no tool call";
  const rule = (
    `${SERVER_REQUIRED_TOOL_REPAIR_MARKER}\n`
    + `Your previous output requested ${received}, but the server requires ${toolName}. `
    + `That output was discarded and no tool ran. Serialize exactly one ${toolName} call now using the only `
    + "available tool schema. Derive model-owned fields from retained evidence; server-owned fields are injected. "
    + "Do not explain, read, search, checkpoint, call another function name, or return a final answer."
  );
  return upsertLeadingSystemRule(chat, SERVER_REQUIRED_TOOL_REPAIR_MARKER, rule);
}

function architectureRecoveryContinuationRequested(goal: string): boolean {
  return /\b(?:continue|resume|retry|same\s+validation|this\s+validation)\b|계속|이어|재개|검증|그대로/i.test(
    String(goal || ""),
  );
}

function injectArchitectureValidationRule(chat: Chat): boolean {
  const rule = (
    `${ARCHITECTURE_GATE_MARKER}\n`
    + "For an architecture/design-validation objective, investigate direct project source first, then submit the "
    + "self-derived proposal to unreal_architecture_reasoning. Do not provide the final design until "
    + "proposalValidation.ok is true. If validation fails, materially revise the rejected ownership, concrete "
    + "caller-to-authority path, truth-source inventory, lifecycle recovery, validation matrix, or slice contract "
    + "and validate once more. Set proposal.scope explicitly. Only when scope.networked=true, prove that a "
    + "client-originated Server RPC is invoked on an actor/component actually owned by that client's connection. "
    + "For local-only work, keep scope.networked=false and do not invent networking/RPC fields merely to describe "
    + "network exclusions. Ground every claimed existing participant/roster truth source in "
    + "direct project or inherited framework evidence. If proposalValidation.repairStrategy is full_replan or "
    + "repairSubmission.mode is fullProposal, reuse already-read direct-source evidence while the source snapshot "
    + "is unchanged; re-read only when source changed, evidence is missing, or needed lines were not covered. Then "
    + "submit one complete proposal; never use proposalPatch/proposalRepairs or preserve the rejected central owner. "
    + "Otherwise apply every returned repair "
    + "requirement at its exact jsonPath and prefer repairSubmission.argumentShape with one complete replacement value "
    + "per path. Never repeat a jsonPath for individual array rows. "
    + "Keep private reasoning brief and submit a compact proposal: one concise sentence per scalar field and only "
    + "the evidence needed for the contract, targeting under 2500 output tokens before the tool call completes. "
    + "Never replace a concrete callable path with an unresolved 'RPC or local call' option."
  );
  return upsertLeadingSystemRule(chat, ARCHITECTURE_GATE_MARKER, rule);
}

function injectFeatureIntentAtomicRule(chat: Chat, eligibleEvidencePaths: string[] = []): boolean {
  const normalizedEvidence = [...new Set(eligibleEvidencePaths
    .map((value) => String(value || "").replace(/\\/g, "/").trim())
    .filter(Boolean))].slice(0, 24);
  const evidenceRule = normalizedEvidence.length > 0
    ? (`\n${FEATURE_INTENT_EVIDENCE_MARKER}\nOnly select targetFiles/slices and completionFrontier evidence from these successful direct-source reads: `
      + `${JSON.stringify(normalizedEvidence)}. If the desired candidate is outside this set, read its owning declaration and implementation before resubmitting.`)
    : "";
  const rule = (
    `${FEATURE_INTENT_ATOMIC_MARKER}\n`
    + "Submit exactly one unreal_feature_intent_resolve model-facing call for this gate. If selectedSlice.files "
    + "is empty, include every already-discovered bounded 1-2 file slice in its slices argument and select one "
    + "with activeSliceId. SelectIntent, ResolveSlice, CaptureSnapshot, and BindIntent are server-owned internal "
    + "phases. Never call unreal_task_define_slices separately for feature intent. When the user asks for the "
    + "current implementation status or the earliest incomplete feature, bind only a candidate whose owning "
    + "declaration and implementation were both read and whose unmet behavior is concrete in that evidence. "
    + "For that audit, include completionFrontier in the same call with milestone, candidateFeature, "
    + "declarationEvidence[{sourcePath,locator}], implementationEvidence[{sourcePath,locator}], "
    + "implementedBehavior, unmetBehavior{statement,sourcePath,locator,evidenceType}, and "
    + "priorCandidatesComplete. The unmet behavior must be functional source behavior, never test coverage alone. "
    + "If the evidence shows a candidate is already complete, continue bounded discovery or select the next "
    + "proven gap; never invent a robustness-only mutation to make a completed feature look incomplete."
    + evidenceRule
  );
  return upsertLeadingSystemRule(chat, FEATURE_INTENT_ATOMIC_MARKER, rule);
}

function upsertLeadingSystemRule(chat: Chat, marker: string, rule: string): boolean {
  const replaceMarkedBlock = (current: string): string => {
    const start = current.indexOf(marker);
    if (start < 0) return current ? `${current.replace(/\s+$/u, "")}\n${rule}` : rule;
    const nextMarker = current.indexOf("\n[", start + marker.length);
    const end = nextMarker >= 0 ? nextMarker : current.length;
    return `${current.slice(0, start)}${rule}${current.slice(end)}`;
  };
  try {
    const current = String(chat.getSystemPrompt() || "");
    chat.replaceSystemPrompt(replaceMarkedBlock(current));
    return true;
  } catch {
    // Older SDK-compatible test doubles may not implement Chat's system-prompt
    // helpers. Update an existing system message in place, but never append a
    // system message after user/assistant/tool history because Qwen/ChatML
    // templates require system content to remain at the beginning.
    try {
      const messages = chat.getMessagesArray();
      for (const message of messages) {
        if (message.getRole() !== "system") continue;
        const updated = replaceMarkedBlock(String(message.getText() || ""));
        if (typeof (message as any).replaceText === "function") {
          (message as any).replaceText(updated);
          return true;
        }
      }
      if (messages.length === 0 && typeof (chat as any).append === "function") {
        (chat as any).append("system", rule);
        return true;
      }
    } catch {
      return false;
    }
  }
  return false;
}

function injectFeatureIntentPayloadRepairRule(chat: Chat, missingPaths: string[]): boolean {
  const paths = [...new Set(missingPaths.map((value) => String(value || "").trim()).filter(Boolean))]
    .slice(0, 32);
  const rule = (
    `${FEATURE_INTENT_PAYLOAD_REPAIR_MARKER}\n`
    + "The previous Feature Intent call was discarded before MCP dispatch because required JSON fields were missing. "
    + `Serialize exactly one unreal_feature_intent_resolve call and include these schema paths: ${JSON.stringify(paths)}. `
    + "Use retained direct-source evidence for every value. Do not explain, read, checkpoint, or omit completionFrontier."
  );
  return upsertLeadingSystemRule(chat, FEATURE_INTENT_PAYLOAD_REPAIR_MARKER, rule);
}

function injectFeatureIntentRecoveryRule(
  chat: Chat,
  recovery: any,
  semanticDiscoveryRemaining = 0,
): boolean {
  const bounded = recovery && typeof recovery === "object" && !Array.isArray(recovery)
    ? JSON.stringify(recovery).slice(0, 8_000)
    : "{}";
  const semanticDiscovery = recovery?.semanticDiscoveryRequired === true;
  const remaining = Math.max(0, Math.min(2, Number(semanticDiscoveryRemaining || 0)));
  const rule = semanticDiscovery && remaining > 0
    ? (
      `${FEATURE_INTENT_RECOVERY_MARKER}\n`
      + "Feature Intent remains pending because the prior semantic claim was contradicted by source or cited the "
      + "wrong owning function. Do not repair or restate that candidate yet. Inspect a different bounded candidate "
      + `with at most ${remaining} additional discovery call(s), then submit a materially new completionFrontier. `
      + `Recovery contract: ${bounded}. Keep the current task/session and do not checkpoint to refresh this allowance.`
    )
    : (
      `${FEATURE_INTENT_RECOVERY_MARKER}\n`
      + "Feature Intent remains pending, but the prior unchanged call must not be repeated. Follow this structured "
      + `recovery contract: ${bounded}. Read any listed missing target evidence first; otherwise submit one materially `
      + "changed completionFrontier using the required fields/template. Do not call checkpoint as a substitute for recovery."
    );
  return upsertLeadingSystemRule(chat, FEATURE_INTENT_RECOVERY_MARKER, rule);
}

function injectFeatureIntentPostReadRule(chat: Chat, remainingDiscoveryCalls: number): boolean {
  const remaining = Math.max(0, Math.min(2, Number(remainingDiscoveryCalls || 0)));
  const rule = (
    `${FEATURE_INTENT_POST_READ_MARKER}\n`
    + "The direct-source read requested for a proposed Feature Intent has completed. That read is evidence, not "
    + "approval of the pre-read semantic claim. Do not replay the old completionFrontier or its old target binding. "
    + "If the new source disproves that candidate, inspect the next bounded candidate or submit a materially new "
    + `frontier. At most ${remaining} additional discovery call(s) remain before Feature Intent must be attempted again. `
    + "Keep the current task/session ownership and do not checkpoint merely to refresh this allowance."
  );
  return upsertLeadingSystemRule(chat, FEATURE_INTENT_POST_READ_MARKER, rule);
}

function injectFeatureIntentEvidenceRefillRule(
  chat: Chat,
  threshold: number,
  declarationCount: number,
  implementationCount: number,
): boolean {
  const rule = (
    `${FEATURE_INTENT_EVIDENCE_REFILL_MARKER}\n`
    + "Feature Intent is temporarily unavailable because the current implementation audit is not grounded yet. "
    + `Collect at least ${threshold} distinct direct-source reads, including at least two owning declarations and two `
    + `owning implementations (currently declarations=${declarationCount}, implementations=${implementationCount}). `
    + "Use only the exposed bounded read/search/symbol/list tools. Inspect the real project paths and declaration/implementation "
    + "pairs for the earliest candidate feature. Do not call unreal_feature_intent_resolve, invent a path, infer a missing "
    + "feature from filenames alone, or manufacture a robustness-only change. Once the evidence threshold is satisfied, "
    + "the resolver will be exposed automatically."
  );
  return upsertLeadingSystemRule(chat, FEATURE_INTENT_EVIDENCE_REFILL_MARKER, rule);
}

function injectEvidenceFirstContractRule(chat: Chat): boolean {
  const rule = (
    `${EVIDENCE_FIRST_CONTRACT_MARKER}\n`
    + "The infrastructure bootstrap and task route are already established. Before business-source discovery or Feature "
    + "Intent selection, call evidence_first_contract exactly once with mode=codegen. This is a model-facing audit contract, "
    + "not another task-planning phase. Do not inspect project files or submit Feature Intent until that call succeeds."
  );
  return upsertLeadingSystemRule(chat, EVIDENCE_FIRST_CONTRACT_MARKER, rule);
}

function injectArchitectureSubmissionRule(chat: Chat): boolean {
  const rule = (
    `${ARCHITECTURE_SUBMISSION_MARKER}\n`
    + "Enough direct-source evidence has been collected for this architecture-validation turn. Submit your own "
    + "complete proposal to unreal_architecture_reasoning now. The validator tool is forced: do not emit a final "
    + "design, call another discovery tool, or ask the user to retry. If a prior proposal failed, follow its retained "
    + "repairSubmission contract and continue until proposalValidation.ok is true."
  );
  return upsertLeadingSystemRule(chat, ARCHITECTURE_SUBMISSION_MARKER, rule);
}

function injectArchitectureCoreChangeRule(chat: Chat, jsonPaths: string[]): boolean {
  const paths = [...new Set(
    (jsonPaths || []).map((path) => String(path || "").trim()).filter(Boolean),
  )].slice(0, 24);
  if (!paths.length) return false;
  const rule = (
    "[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED]\n"
    + `The previous full proposal was rejected because these implicated paths remained structurally unchanged: ${paths.join(", ")}. `
    + "Submit a complete independently re-derived proposal now. Every listed path must materially differ from the "
    + "rejected payload while satisfying direct-source evidence. These are negative constraints only; derive all "
    + "replacement values yourself and do not emit a final answer before validator success."
  );
  return upsertLeadingSystemRule(chat, "[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED]", rule);
}

function injectArchitecturePayloadRepairRule(chat: Chat, jsonPaths: string[]): boolean {
  const paths = [...new Set(
    (jsonPaths || []).map((path) => String(path || "").trim()).filter(Boolean),
  )].slice(0, 64);
  if (!paths.length) return false;
  const rule = (
    `${ARCHITECTURE_PAYLOAD_REPAIR_MARKER}\n`
    + "The previous validator call was withheld locally because its tool payload omitted required JSON-schema "
    + `paths: ${paths.join(", ")}. Reissue exactly one complete unreal_architecture_reasoning call now. `
    + "Populate every listed field from your own source-grounded design; the paths specify serialization shape "
    + "only and do not supply design values. Do not emit final text before validator success."
  );
  return upsertLeadingSystemRule(chat, ARCHITECTURE_PAYLOAD_REPAIR_MARKER, rule);
}

function trailingMetaUserMessage(messages: ChatMessage[]): ChatMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.getRole() !== "user") continue;
    const text = String(message.getText() || "").trim();
    if (!text) continue;
    return core.isMetaUserMessage(text) ? message : null;
  }
  return null;
}

function architectureGateStatus(messages: ChatMessage[], checkpoint: any): {
  attempted: boolean;
  validated: boolean;
  directEvidenceCount: number;
  declarationEvidenceCount: number;
  implementationEvidenceCount: number;
  directSourceFileEvidenceCount: number;
  directSourceDeclarationCount: number;
  directSourceImplementationCount: number;
  directSourceFileEvidencePaths: string[];
  directSourceDeclarationPaths: string[];
  directSourceImplementationPaths: string[];
  evidenceCallsSinceLastAttempt: number;
  discoveryCallsSinceLastAttempt: number;
  uniqueEvidenceSinceLastAttempt: number;
  lastValidationFailed: boolean;
  lastRepairStrategy: string;
  lastRepairMode: string;
  requiresFullProposal: boolean;
  lastErrorCode: string;
  unchangedCorePaths: string[];
  stagedContractRequired: boolean;
  networkedContractRequired: boolean;
} {
  const snapshots = core.snapshotMessages(messages);
  const resultsById = new Map<string, any>();
  for (const snapshot of snapshots) {
    for (const result of snapshot.toolResults || []) {
      const id = String(result?.toolCallId || "").trim();
      if (id) resultsById.set(id, result);
    }
  }
  let attempted = Boolean(checkpoint?.architectureProposal);
  let validated = checkpoint?.architectureProposal?.validationOk === true;
  let lastValidationFailed = checkpoint?.architectureProposal?.validationOk === false;
  let lastRepairStrategy = String(checkpoint?.architectureProposal?.repairStrategy || "").trim();
  let lastRepairMode = String(checkpoint?.architectureProposal?.repairMode || "").trim();
  let requiresFullProposal = Boolean(
    checkpoint?.architectureProposal?.requiresFullReplan
    || lastRepairStrategy === "full_replan"
    || lastRepairMode === "fullProposal",
  );
  let lastErrorCode = String(checkpoint?.architectureProposal?.lastErrorCode || "").trim();
  let unchangedCorePaths = Array.isArray(checkpoint?.architectureProposal?.unchangedCorePaths)
    ? checkpoint.architectureProposal.unchangedCorePaths.map((path: any) => String(path || "").trim()).filter(Boolean)
    : [];
  let stagedContractRequired = checkpoint?.architectureProposal?.stagedContractRequired === true;
  let networkedContractRequired = Boolean(
    checkpoint?.architectureProposal?.networkedContractRequired === true
    || unchangedCorePaths.some((path: string) => path === "networking" || path.startsWith("networking.")),
  );
  const evidence = new Set<string>();
  const declarationEvidence = new Set<string>();
  const implementationEvidence = new Set<string>();
  const directSourceFileEvidence = new Set<string>();
  const directSourceDeclarationEvidence = new Set<string>();
  const directSourceImplementationEvidence = new Set<string>();
  const evidenceSinceLastAttempt = new Set<string>();
  let evidenceCallsSinceLastAttempt = 0;
  let discoveryCallsSinceLastAttempt = 0;
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      const name = String(call?.name || "").trim();
      const result = resultsById.get(String(call?.id || "").trim());
      if (toolNamesMatch(ARCHITECTURE_TOOL_NAME, name)) {
        attempted = true;
        evidenceCallsSinceLastAttempt = 0;
        discoveryCallsSinceLastAttempt = 0;
        evidenceSinceLastAttempt.clear();
        if (result) {
          for (const payload of core.parseJsonObjects(result.content)) {
            const payloadErrorCode = String(payload?.errorCode || "").trim();
            if (payloadErrorCode) lastErrorCode = payloadErrorCode;
            unchangedCorePaths = (
              payloadErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
              && Array.isArray(payload?.requiredChangedPaths)
            )
              ? payload.requiredChangedPaths.map((path: any) => String(path || "").trim()).filter(Boolean).slice(0, 24)
              : [];
            const validation = payload?.proposalValidation;
            const designContract = validation?.designContract;
            if (typeof designContract?.stagedImplementation === "boolean") {
              stagedContractRequired = designContract.stagedImplementation;
            }
            if (typeof designContract?.networkedProposal === "boolean") {
              networkedContractRequired = designContract.networkedProposal;
            }
            const implementationGate = validation?.implementationGate;
            const validatorPassed = Boolean(
              validation?.ok === true
              && implementationGate?.writesAllowed !== false,
            );
            if (validatorPassed) {
              validated = true;
              lastValidationFailed = false;
              requiresFullProposal = false;
            } else if (validation?.ok === false || implementationGate?.writesAllowed === false) {
              validated = false;
              lastValidationFailed = true;
            }
            const repairStrategy = String(validation?.repairStrategy || "").trim();
            const repairMode = String(payload?.repairSubmission?.mode || "").trim();
            if (repairStrategy) lastRepairStrategy = repairStrategy;
            if (repairMode) lastRepairMode = repairMode;
            const currentRequiresFullProposal = Boolean(
              (validation?.ok === true && implementationGate?.writesAllowed === false)
              ||
              validation?.designContract?.requiresFullReplan === true
              || repairStrategy === "full_replan"
              || repairMode === "fullProposal",
            );
            if (validatorPassed) {
              requiresFullProposal = false;
            } else if (
              validation?.ok === false
              || implementationGate?.writesAllowed === false
              || repairStrategy
              || repairMode
            ) {
              // The newest validator decision replaces the persisted mode. Keeping
              // a prior full-replan flag sticky after the validator has narrowed the
              // issue to proposalRepairs makes the model regenerate the entire
              // proposal and can reintroduce already-fixed ownership mistakes.
              requiresFullProposal = currentRequiresFullProposal;
            }
          }
        }
        continue;
      }
      if (!result || !core.toolResultSucceeded(result)) continue;
      if (ARCHITECTURE_DISCOVERY_TOOLS.some((tool) => toolNamesMatch(tool, name))) {
        discoveryCallsSinceLastAttempt += 1;
      }
      if (!ARCHITECTURE_EVIDENCE_TOOLS.some((tool) => toolNamesMatch(tool, name))) continue;
      const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
      const rawPathIdentity = String(args.path || args.filePath || "").trim();
      const sourceIdentity = String(
        rawPathIdentity || args.symbol || args.symbolName || args.query || call.id || "",
      ).trim();
      const evidenceIdentity = rawPathIdentity
        ? normalizeProjectSourcePath(rawPathIdentity)
        : sourceIdentity;
      const rangeIdentity = [args.startLine, args.endLine, args.lineStart, args.lineEnd]
        .filter((value) => value !== undefined && value !== null && String(value).trim())
        .join(":");
      const identity = evidenceIdentity
        ? `${name.toLowerCase()}:${evidenceIdentity}${rangeIdentity ? `:${rangeIdentity}` : ""}`
        : "";
      if (!identity) continue;
      evidence.add(identity);
      evidenceSinceLastAttempt.add(identity);
      evidenceCallsSinceLastAttempt += 1;
      const normalizedSource = rawPathIdentity
        ? normalizeProjectSourcePath(rawPathIdentity)
        : sourceIdentity.replace(/\\/g, "/");
      if (/\.(?:h|hh|hpp|hxx|inl)$/i.test(normalizedSource)) declarationEvidence.add(identity);
      if (
        /\.(?:c|cc|cpp|cxx|m|mm|cs)$/i.test(normalizedSource)
        || ["read_symbol", "unreal_symbol_lookup"].some((tool) => toolNamesMatch(tool, name))
      ) {
        implementationEvidence.add(identity);
      }
      if (DIRECT_SOURCE_FILE_TOOLS.some((tool) => toolNamesMatch(tool, name))) {
        // Completion-audit readiness is intentionally stricter than the
        // architecture evidence pool: count unique successful source files,
        // not line ranges, symbol queries, or empty lookup results. Otherwise
        // one zero-match symbol lookup can unlock Feature Intent after only
        // five real reads while the UI says six direct-source reads are needed.
        const fileIdentity = normalizeProjectSourcePath(rawPathIdentity || normalizedSource);
        directSourceFileEvidence.add(fileIdentity);
        if (/\.(?:h|hh|hpp|hxx|inl)$/i.test(fileIdentity)) {
          directSourceDeclarationEvidence.add(fileIdentity);
        }
        if (/\.(?:c|cc|cpp|cxx|m|mm|cs)$/i.test(fileIdentity)) {
          directSourceImplementationEvidence.add(fileIdentity);
        }
      }
    }
  }
  return {
    attempted,
    validated,
    directEvidenceCount: evidence.size,
    declarationEvidenceCount: declarationEvidence.size,
    implementationEvidenceCount: implementationEvidence.size,
    directSourceFileEvidenceCount: directSourceFileEvidence.size,
    directSourceDeclarationCount: directSourceDeclarationEvidence.size,
    directSourceImplementationCount: directSourceImplementationEvidence.size,
    directSourceFileEvidencePaths: [...directSourceFileEvidence].sort(),
    directSourceDeclarationPaths: [...directSourceDeclarationEvidence].sort(),
    directSourceImplementationPaths: [...directSourceImplementationEvidence].sort(),
    evidenceCallsSinceLastAttempt,
    discoveryCallsSinceLastAttempt,
    uniqueEvidenceSinceLastAttempt: evidenceSinceLastAttempt.size,
    lastValidationFailed,
    lastRepairStrategy,
    lastRepairMode,
    requiresFullProposal,
    lastErrorCode,
    unchangedCorePaths,
    stagedContractRequired,
    networkedContractRequired,
  };
}

function latestFeatureFrontierState(messages: ChatMessage[]): {
  recovery: any | null;
  terminalRepeated: boolean;
} {
  const snapshots = core.snapshotMessages(messages);
  const calls = new Map<string, any>();
  let recovery: any | null = null;
  let terminalRepeated = false;
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      const id = String(call?.id || "").trim();
      if (id) calls.set(id, call);
    }
    for (const result of snapshot.toolResults || []) {
      const call = calls.get(String(result?.toolCallId || "").trim());
      const name = String(result?.name || call?.name || "").trim();
      if (!toolNamesMatch(FEATURE_INTENT_TOOL_NAME, name)) continue;
      for (const payload of core.parseJsonObjects(result?.content)) {
        const errorCode = String(payload?.errorCode || "").trim();
        const validationErrorCode = String(
          payload?.validationErrorCode
          || payload?.gateCompletion?.validationErrorCode
          || "",
        ).trim();
        const repeatedFrontierBlocker = Boolean(
          errorCode === "REPEATED_GATE_BLOCKER"
          && validationErrorCode === "FEATURE_FRONTIER_UNPROVEN"
          && (
            payload?.retryable === false
            || payload?.gateCompletion?.retryable === false
          )
        );
        if (repeatedFrontierBlocker) {
          // This is the bounded retry boundary, not another payload repair.
          // Leave normal planner reads/discovery available so evidence can
          // materially change, but never force the rejected gate again.
          recovery = null;
          terminalRepeated = true;
        } else if (
          errorCode === "FEATURE_FRONTIER_UNPROVEN"
          || validationErrorCode === "FEATURE_FRONTIER_UNPROVEN"
        ) {
          terminalRepeated = false;
          recovery = payload?.featureFrontierRecovery
            || payload?.gateCompletion?.featureFrontierRecovery
            || {
              kind: "repair_completion_frontier",
              requiredFields: [
                "completionFrontier.milestone",
                "completionFrontier.candidateFeature",
                "completionFrontier.declarationEvidence",
                "completionFrontier.implementationEvidence",
                "completionFrontier.implementedBehavior",
                "completionFrontier.unmetBehavior",
                "completionFrontier.priorCandidatesComplete",
              ],
            };
        } else if (payload?.ok === true || payload?.gatePassed === true) {
          recovery = null;
          terminalRepeated = false;
        }
      }
    }
  }
  return { recovery, terminalRepeated };
}

function appendRequired(schema: any, fields: string[]): void {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return;
  schema.required = [...new Set([
    ...(Array.isArray(schema.required) ? schema.required : []),
    ...fields,
  ])];
}

function architectureSubmissionTool(
  tool: any,
  requireCompleteProposal: boolean,
  options: { stagedContract?: boolean; networkedContract?: boolean } = {},
): any {
  if (!requireCompleteProposal) return tool;
  let cloned: any;
  try {
    cloned = JSON.parse(JSON.stringify(tool));
  } catch {
    return tool;
  }
  const callable = cloned?.function && typeof cloned.function === "object"
    ? cloned.function
    : cloned;
  const parameters = callable?.parameters;
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return cloned;
  const properties = parameters.properties;
  if (!properties || typeof properties !== "object" || !("proposal" in properties)) return cloned;
  appendRequired(parameters, ["proposal"]);
  const proposal = properties.proposal;
  if (!proposal || typeof proposal !== "object" || Array.isArray(proposal)) return cloned;
  const proposalProperties = proposal.properties;
  if (!proposalProperties || typeof proposalProperties !== "object") return cloned;
  if (options.stagedContract) {
    appendRequired(proposal, [
      "decision",
      "scope",
      "invariants",
      "impactedSurfaces",
      "validationPlan",
      "implementationFiles",
      "ownership",
      "stateInventory",
      "lifecycleTransitions",
      "validationMatrix",
      "implementationSlices",
    ]);
    for (const field of [
      "implementationFiles",
      "stateInventory",
      "lifecycleTransitions",
      "validationMatrix",
      "implementationSlices",
    ]) {
      const fieldSchema = proposalProperties[field];
      if (fieldSchema && typeof fieldSchema === "object" && !Array.isArray(fieldSchema)) {
        fieldSchema.minItems = 1;
      }
    }
    appendRequired(proposalProperties.ownership, [
      "stateOwner",
      "dataOwner",
      "lifecycleOwner",
      "failurePolicy",
      "recoveryPolicy",
    ]);
    if (proposalProperties.invariants && typeof proposalProperties.invariants === "object") {
      proposalProperties.invariants.items = {
        type: "object",
        properties: {
          id: { type: "string", minLength: 1 },
          statement: { type: "string", minLength: 1 },
        },
        required: ["id", "statement"],
        additionalProperties: false,
      };
      proposalProperties.invariants.description = (
        "Declare 2-5 stable invariant objects. Reference their ids from slices and validationMatrix."
      );
    }
    const matrixItem = proposalProperties.validationMatrix?.items;
    if (matrixItem && typeof matrixItem === "object") {
      matrixItem.required = ["invariantId", "checks"];
      delete matrixItem.anyOf;
    }
    const matrixInvariant = proposalProperties.validationMatrix?.items?.properties?.invariant;
    if (matrixInvariant && typeof matrixInvariant === "object") {
      matrixInvariant.description = (
        "Use either one complete string from proposal.invariants or its unique leading label such as I1 when "
        + "the declaration begins 'I1: ...'."
      );
    }
    const sliceInvariants = proposalProperties.implementationSlices?.items?.properties?.invariants;
    if (sliceInvariants && typeof sliceInvariants === "object") {
      sliceInvariants.description = (
        "Every entry must reference proposal.invariants using either the complete string or a unique leading label "
        + "such as I1; do not invent slice-only invariants."
      );
    }
    const sliceItem = proposalProperties.implementationSlices?.items;
    if (sliceItem && typeof sliceItem === "object") {
      sliceItem.required = ["sliceId", "files", "invariantIds", "validation"];
      delete sliceItem.anyOf;
    }
  }
  if (options.networkedContract && proposalProperties.networking) {
    appendRequired(proposal, [
      "networking",
      "alternatives",
      "selectedAlternative",
      "selectionRationale",
      "migrationPlan",
    ]);
    const networkedScope = proposalProperties.scope?.properties?.networked;
    if (networkedScope && typeof networkedScope === "object") networkedScope.enum = [true];
    appendRequired(proposalProperties.networking, [
      "authorityOwner",
      "clientInitiated",
      "requestPath",
      "rpcOwner",
      "owningConnection",
      "serverValidation",
      "replicatedState",
    ]);
    const requestPath = proposalProperties.networking?.properties?.requestPath;
    if (requestPath && typeof requestPath === "object") requestPath.minItems = 3;
  }
  return cloned;
}

function featureIntentSubmissionTool(
  tool: any,
  options: { completionAuditRequired?: boolean; sliceInputRequired?: boolean } = {},
): any {
  if (!tool || (!options.completionAuditRequired && !options.sliceInputRequired)) return tool;
  let cloned: any;
  try {
    cloned = JSON.parse(JSON.stringify(tool));
  } catch {
    return tool;
  }
  const callable = cloned?.function && typeof cloned.function === "object"
    ? cloned.function
    : cloned;
  const parameters = callable?.parameters;
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return cloned;
  const properties = parameters.properties;
  if (!properties || typeof properties !== "object") return cloned;
  if (options.completionAuditRequired && properties.completionFrontier) {
    appendRequired(parameters, ["completionFrontier"]);
  }
  if (options.sliceInputRequired && properties.slices) {
    appendRequired(parameters, ["slices"]);
  }
  return cloned;
}

function requiredSchemaLeafPaths(schema: any, basePath: string, depth = 0): string[] {
  if (!schema || typeof schema !== "object" || depth > 12) return basePath ? [basePath] : [];
  const required = Array.isArray(schema.required)
    ? schema.required.map((field: any) => String(field || "").trim()).filter(Boolean)
    : [];
  const properties = schema.properties && typeof schema.properties === "object"
    ? schema.properties
    : {};
  if (!required.length) return basePath ? [basePath] : [];
  const paths: string[] = [];
  for (const field of required) {
    const childPath = basePath ? `${basePath}.${field}` : field;
    const childSchema = properties[field];
    const descendants = requiredSchemaLeafPaths(childSchema, childPath, depth + 1);
    paths.push(...(descendants.length ? descendants : [childPath]));
  }
  return paths;
}

function schemaContractViolationPaths(
  schema: any,
  value: any,
  basePath = "",
  depth = 0,
): string[] {
  if (!schema || typeof schema !== "object" || depth > 12) return [];
  const schemaTypes = Array.isArray(schema.type) ? schema.type : [schema.type].filter(Boolean);
  const expectsObject = schemaTypes.includes("object") || Boolean(schema.properties || schema.required);
  const expectsArray = schemaTypes.includes("array");
  if (expectsObject) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      const requiredLeaves = requiredSchemaLeafPaths(schema, basePath, depth);
      return requiredLeaves.length ? requiredLeaves : [basePath || "$arguments"];
    }
    const required = Array.isArray(schema.required)
      ? schema.required.map((field: any) => String(field || "").trim()).filter(Boolean)
      : [];
    const properties = schema.properties && typeof schema.properties === "object"
      ? schema.properties
      : {};
    const violations: string[] = [];
    for (const field of required) {
      const childPath = basePath ? `${basePath}.${field}` : field;
      if (!Object.prototype.hasOwnProperty.call(value, field) || value[field] == null) {
        const requiredLeaves = requiredSchemaLeafPaths(properties[field], childPath, depth + 1);
        violations.push(...(requiredLeaves.length ? requiredLeaves : [childPath]));
        continue;
      }
      violations.push(...schemaContractViolationPaths(
        properties[field], value[field], childPath, depth + 1,
      ));
    }
    for (const [field, childSchema] of Object.entries(properties)) {
      if (required.includes(field) || !Object.prototype.hasOwnProperty.call(value, field)) continue;
      const childPath = basePath ? `${basePath}.${field}` : field;
      violations.push(...schemaContractViolationPaths(
        childSchema, value[field], childPath, depth + 1,
      ));
    }
    return violations;
  }
  if (expectsArray) {
    if (!Array.isArray(value)) return [basePath || "$arguments"];
    const minItems = Number(schema.minItems || 0);
    if (Number.isFinite(minItems) && value.length < minItems) return [basePath || "$arguments"];
    if (schema.items && typeof schema.items === "object") {
      return value.flatMap((item: any, index: number) => schemaContractViolationPaths(
        schema.items, item, `${basePath}[${index}]`, depth + 1,
      ));
    }
  }
  return [];
}

function architecturePayloadViolationPaths(request: any, tool: any): string[] {
  if (!toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(request))) return [];
  const callable = tool?.function && typeof tool.function === "object" ? tool.function : tool;
  const parameters = callable?.parameters;
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  return [...new Set(schemaContractViolationPaths(parameters, args))].slice(0, 64);
}

function featureIntentPayloadViolationPaths(request: any, tool: any): string[] {
  if (!toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requestedToolName(request))) return [];
  const callable = tool?.function && typeof tool.function === "object" ? tool.function : tool;
  const parameters = callable?.parameters;
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  return [...new Set(schemaContractViolationPaths(parameters, args))].slice(0, 64);
}

function normalizeProjectSourcePath(
  value: any,
  hostPlatform: string = process.platform,
): string {
  const identity = core.normalizeProjectEvidencePath(value, hostPlatform as any);
  if (!identity) return "";
  const windows = core.isWindowsHostPlatform(hostPlatform as any);
  const sourcePrefix = windows ? "source/" : "Source/";
  const pluginsPrefix = windows ? "plugins/" : "Plugins/";
  if (identity.startsWith(sourcePrefix) || identity.startsWith(pluginsPrefix)) return identity;
  const sourceMarker = identity.indexOf(`/${sourcePrefix}`);
  if (sourceMarker < 0) return identity;
  const pluginMarker = identity.lastIndexOf(`/${pluginsPrefix}`, sourceMarker);
  return identity.slice((pluginMarker >= 0 ? pluginMarker : sourceMarker) + 1);
}

function directSourcePairStem(value: any, hostPlatform: string = process.platform): string {
  const normalized = normalizeProjectSourcePath(value, hostPlatform);
  const moduleVisibility = core.isWindowsHostPlatform(hostPlatform as any)
    ? /(^|\/)(source\/[^/]+)\/(?:public|private)\//
    : /(^|\/)(Source\/[^/]+)\/(?:Public|Private)\//;
  return normalized
    .replace(moduleVisibility, "$1$2/")
    .replace(/\.(?:h|hh|hpp|hxx|inl|c|cc|cpp|cxx|m|mm|cs)$/i, "");
}

function hasTargetBoundDirectSourcePair(
  declarationPaths: string[],
  implementationPaths: string[],
  hostPlatform: string = process.platform,
): boolean {
  const declarationStems = new Set(
    declarationPaths.map((value) => directSourcePairStem(value, hostPlatform)).filter(Boolean),
  );
  return implementationPaths.some((value) => (
    declarationStems.has(directSourcePairStem(value, hostPlatform))
  ));
}

function featureIntentRequestedTargetFiles(request: any): string[] {
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  const normalize = (value: any) => String(value || "").replace(/\\/g, "/").replace(/^project:\/\//i, "").replace(/^\.\//, "").trim().replace(/^\/+|\/+$/g, "");
  const direct = Array.isArray(args.targetFiles) ? args.targetFiles : [];
  const slices = Array.isArray(args.slices) ? args.slices : [];
  const activeSliceId = String(args.activeSliceId || "").trim();
  const selected = activeSliceId
    ? slices.find((item: any) => String(item?.sliceId || item?.slice_id || "").trim() === activeSliceId)
    : slices[0];
  const fromSlice = Array.isArray(selected?.files) ? selected.files : [];
  return [...new Set([...direct, ...fromSlice].map(normalize).filter(Boolean))].slice(0, 4);
}

function knownAbsentFeatureIntentTargetFiles(
  messages: ChatMessage[],
  targetFiles: string[],
): string[] {
  const targets = new Map(
    targetFiles
      .map((path) => [normalizeProjectSourcePath(path), path] as const)
      .filter(([normalized]) => Boolean(normalized)),
  );
  if (targets.size === 0) return [];
  const snapshots = core.snapshotMessages(messages);
  const calls = new Map<string, any>();
  const absent = new Set<string>();
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      if (call?.id) calls.set(String(call.id), call);
    }
    for (const result of snapshot.toolResults || []) {
      const call = calls.get(String(result?.toolCallId || ""));
      const name = String(result?.name || call?.name || "");
      if (!toolNamesMatch("search_files", name) || !core.toolResultSucceeded(result)) continue;
      const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
      if (args.matchFileNames !== true) continue;
      const query = normalizeProjectSourcePath(
        String(args.query || "").replace(/\\/g, "/").split("/").pop()?.trim() || "",
      );
      if (!query) continue;
      const searchRoot = normalizeProjectSourcePath(args.path || "");
      for (const payload of core.parseJsonObjects(result?.content)) {
        const complete = payload?.searchComplete === true
          && (!Array.isArray(payload?.incompleteReasons) || payload.incompleteReasons.length === 0);
        const zeroMatches = (!Array.isArray(payload?.results) || payload.results.length === 0)
          && (!Array.isArray(payload?.fileNameResults) || payload.fileNameResults.length === 0);
        if (!complete || !zeroMatches) continue;
        for (const normalized of targets.keys()) {
          const basename = normalized.split("/").pop() || "";
          const rootCoversTarget = !searchRoot
            || normalized === searchRoot
            || normalized.startsWith(`${searchRoot}/`);
          if (basename === query && rootCoversTarget) absent.add(normalized);
        }
      }
    }
  }
  return [...absent];
}

function unreadFeatureIntentTargetFiles(
  request: any,
  successfulPaths: string[],
  knownAbsentPaths: string[] = [],
): string[] {
  const readable = new Set(successfulPaths.map((path) => normalizeProjectSourcePath(path)).filter(Boolean));
  const absent = new Set(knownAbsentPaths.map((path) => normalizeProjectSourcePath(path)).filter(Boolean));
  return featureIntentRequestedTargetFiles(request).filter((path) => (
    !readable.has(normalizeProjectSourcePath(path))
    && !absent.has(normalizeProjectSourcePath(path))
  ));
}

function stagedArchitectureContractRequired(goal: string): boolean {
  if (/\b(?:implement|create|build|add|extend)\b.{0,48}\b(?:system|subsystem|component|service|feature)\b|구현\s*슬라이스|마이그레이션|생명\s*주기|소유권|(?:시스템|서브시스템|컴포넌트|서비스|기능).{0,32}(?:구현|추가|생성|신설|확장)|(?:구현|추가|생성|신설|확장).{0,32}(?:시스템|서브시스템|컴포넌트|서비스|기능)/i.test(String(goal || ""))) {
    return true;
  }
  return /implementation\s+slice|migration\s+(?:order|plan)|lifecycle|alternative|ownership|구현\s*슬라이스|마이그레이션|생명주기|대안|소유권/i.test(
    String(goal || ""),
  );
}

function networkedArchitectureContractRequired(goal: string): boolean {
  const source = String(goal || "");
  const term = /authoritative|authority|multiplayer|replication|network|\brpc\b|\bserver\b|\bclient\b|멀티플레이|네트워크|복제|서버|클라이언트|권한/gi;
  for (const match of source.matchAll(term)) {
    const start = Number(match.index || 0);
    const before = source.slice(Math.max(0, start - 48), start);
    const after = source.slice(start + String(match[0] || "").length, start + 120);
    const negated = (
      /(?:do\s+not|don't|dont|without|exclude|excluding|out\s+of\s+scope)[^.!?\n]{0,48}$/i.test(before)
      || /^(?:[^.!?\n]{0,72})(?:do\s+not|don't|dont|without|exclude|excluding|out\s+of\s+scope)/i.test(after)
      || /(?:건드리지|제외|아님|없이|사용하지|변경하지|로컬\s*전용)[^.!?\n]{0,32}$/i.test(before)
      || /^(?:[^.!?\n]{0,72})(?:건드리지|제외|아님|없이|사용하지|변경하지|로컬\s*전용)/i.test(after)
    );
    if (!negated) return true;
  }
  return false;
}
function architectureDiscoveryToolAllowed(name: string): boolean {
  return toolNamesMatch(ARCHITECTURE_TOOL_NAME, name)
    || ARCHITECTURE_DISCOVERY_TOOLS.some((tool) => toolNamesMatch(tool, name));
}

const SESSION_SCOPED_ANALYSIS_TOOLS = [
  "unreal_rag_search",
  "unreal_agent_session",
  "unreal_agent_plan",
  "unreal_architecture_reasoning",
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
];

function enrichToolRequestSession(request: any, sessionId: string, toolDefinitions: any[]): any {
  const name = requestedToolName(request);
  if (!SESSION_SCOPED_ANALYSIS_TOOLS.some((tool) => toolNamesMatch(tool, name))) return request;
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  if (String(args.sessionId || args.session_id || "").trim()) return request;
  const sessionArgument = toolAcceptsArgument(toolDefinitions, name, "sessionId")
    ? "sessionId"
    : (toolAcceptsArgument(toolDefinitions, name, "session_id") ? "session_id" : "");
  if (!sessionArgument) return request;
  return {
    ...request,
    arguments: { ...args, [sessionArgument]: sessionId },
  };
}

function toolAcceptsArgument(toolDefinitions: any[], toolName: string, argument: string): boolean {
  const definition = (toolDefinitions || []).find((tool: any) => toolNamesMatch(
    toolName,
    tool?.function?.name || tool?.name || "",
  ));
  const schema = definition?.function?.parameters
    || definition?.function?.inputSchema
    || definition?.parameters
    || definition?.inputSchema
    || {};
  return Boolean(
    schema?.properties
    && Object.prototype.hasOwnProperty.call(schema.properties, argument),
  );
}

function mergeServerOwnedArguments(modelValue: any, serverValue: any): any {
  if (!serverValue || typeof serverValue !== "object" || Array.isArray(serverValue)) {
    return serverValue;
  }
  const modelObject = modelValue && typeof modelValue === "object" && !Array.isArray(modelValue)
    ? modelValue
    : {};
  const merged = { ...modelObject };
  for (const [key, value] of Object.entries(serverValue)) {
    merged[key] = mergeServerOwnedArguments(modelObject[key], value);
  }
  return merged;
}

function deterministicControlToolDefinition(
  definition: any,
  injectedArguments: Record<string, any>,
): any | null {
  if (!definition) return null;
  const schema = definition?.function?.parameters
    || definition?.function?.inputSchema
    || definition?.parameters
    || definition?.inputSchema
    || {};
  const properties = schema?.properties && typeof schema.properties === "object"
    ? schema.properties
    : {};
  const injected = mergeServerOwnedArguments({}, injectedArguments || {});
  const originalRequired = Array.isArray(schema.required)
    ? schema.required.map((key: any) => String(key || "")).filter(Boolean)
    : [];
  return {
    ...definition,
    function: {
      ...definition.function,
      parameters: {
        ...schema,
        properties,
        required: originalRequired.filter((key: string) => (
          !Object.prototype.hasOwnProperty.call(injected, key)
        )),
      },
    },
    __serverOwnedInjectedArgs: injected,
    __serverOwnedDirectCallSafe: originalRequired.every((key: string) => (
      Object.prototype.hasOwnProperty.call(injected, key)
    )),
  };
}

function phaseControlToolDefinition(
  definition: any,
  sessionId: string,
  authoritativeGoal: string,
  runtimeModelId = "",
): any | null {
  if (!definition) return null;
  const name = String(definition?.function?.name || definition?.name || "");
  const injected: Record<string, any> = {};
  for (const key of ["sessionId", "conversationSessionId"]) {
    if (toolAcceptsArgument([definition], name, key)) injected[key] = sessionId;
  }
  if (toolNamesMatch(TASK_PLANNER_TOOL_NAME, name)) {
    for (const key of ["request", "latestUserMessage"]) {
      if (toolAcceptsArgument([definition], name, key)) injected[key] = authoritativeGoal;
    }
    if (runtimeModelId && toolAcceptsArgument([definition], name, "runtimeModelId")) {
      injected.runtimeModelId = runtimeModelId;
    }
  }
  return deterministicControlToolDefinition(definition, injected);
}

function taskOwnedRequiredToolDefinition(
  checkpoint: any,
  toolDefinitions: any[],
  sessionId: string,
): any | null {
  const requiredName = String(checkpoint?.requiredNextTool?.name || "").trim();
  if (!requiredName || core.isNonToolNextAction(requiredName)) return null;
  const ownership = core.compactTaskRouteOwnership(checkpoint?.taskRouteOwnership);
  const definition = (toolDefinitions || []).find((tool: any) => toolNamesMatch(
    requiredName,
    tool?.function?.name || tool?.name || "",
  ));
  if (!definition) return null;
  const schema = definition?.function?.parameters
    || definition?.function?.inputSchema
    || definition?.parameters
    || definition?.inputSchema
    || {};
  const properties = schema?.properties && typeof schema.properties === "object"
    ? schema.properties
    : {};
  const requiredArgs = checkpoint?.requiredNextTool?.args;
  const injected = mergeServerOwnedArguments(
    {},
    requiredArgs && typeof requiredArgs === "object" && !Array.isArray(requiredArgs)
      ? requiredArgs
      : {},
  );
  if (ownership && Object.prototype.hasOwnProperty.call(properties, "taskAuthorization")) {
    injected.taskAuthorization = ownership;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "sessionId")) {
    injected.sessionId = sessionId;
  }
  const originalRequired = Array.isArray(schema.required)
    ? schema.required.map((key: any) => String(key || "")).filter(Boolean)
    : [];
  const directCallSafe = originalRequired.every((key: string) => (
    Object.prototype.hasOwnProperty.call(injected, key)
  ));
  return {
    ...definition,
    function: {
      ...definition.function,
      parameters: {
        ...schema,
        // Keep properties visible for SDK compatibility, but remove injected
        // server-owned fields from `required`. The model may omit them and the
        // control plane overwrites any stale copy before LM Studio dispatches.
        properties,
        required: originalRequired
          .filter((key: string) => !Object.prototype.hasOwnProperty.call(injected, key)),
      },
    },
    __serverOwnedInjectedArgs: injected,
    // Only an exact server-owned argument set may bypass model serialization.
    // Optional model-authored fields are never fabricated by this path.
    __serverOwnedDirectCallSafe: directCallSafe,
  };
}

function enrichToolRequestControl(
  request: any,
  sessionId: string,
  checkpoint: any,
  latestUserGoal: string,
  toolDefinitions: any[],
): any {
  const sessionBound = enrichToolRequestSession(request, sessionId, toolDefinitions);
  const name = requestedToolName(sessionBound);
  const sourceArgs = sessionBound?.arguments && typeof sessionBound.arguments === "object"
    ? sessionBound.arguments
    : {};
  let args = sourceArgs;
  if (
    toolNamesMatch(TASK_PLANNER_TOOL_NAME, name)
    && String(checkpoint?.runtimeModelId || "").trim()
    && toolAcceptsArgument(toolDefinitions, name, "runtimeModelId")
  ) {
    args = { ...args, runtimeModelId: String(checkpoint.runtimeModelId) };
  }

  if (toolNamesMatch(TASK_PLANNER_TOOL_NAME, name) && latestUserGoal) {
    // The planner request is an authority-bearing copy of the user's goal,
    // not a field the model may summarize as "continue" or rewrite into a
    // different edit. Keep both public planner fields aligned when present.
    if (toolAcceptsArgument(toolDefinitions, name, "request")) {
      args = { ...args, request: latestUserGoal };
    }
    if (toolAcceptsArgument(toolDefinitions, name, "latestUserMessage")) {
      args = { ...args, latestUserMessage: latestUserGoal };
    }
  }

  const requiredName = String(checkpoint?.requiredNextTool?.name || "").trim();
  const requiredArgs = checkpoint?.requiredNextTool?.args;
  if (
    requiredName
    && toolNamesMatch(requiredName, name)
    && requiredArgs
    && typeof requiredArgs === "object"
    && !Array.isArray(requiredArgs)
  ) {
    // requiredNextToolArgs are an explicit server equality contract, not a
    // model-facing hint. Merge them into the emitted call so a compact model
    // cannot strand a healthy task by omitting or copying stale control data.
    args = mergeServerOwnedArguments(args, requiredArgs);
  }

  const ownership = core.compactTaskRouteOwnership(checkpoint?.taskRouteOwnership);
  const detachedSideQuery = checkpoint?.sideQuery?.active === true
    && detachedSideQueryToolAllowed(name);
  if (detachedSideQuery && ownership) {
    // Detached tools can come from unrelated LM Studio plugins. Inject task
    // context only when the exact advertised schema declares it; adding
    // unknown properties makes LM Studio reject the call before dispatch and
    // causes compact models to retry the same read indefinitely.
    if (toolAcceptsArgument(toolDefinitions, name, "taskAuthorization")) {
      args = { ...args, taskAuthorization: ownership };
    }
    if (toolAcceptsArgument(toolDefinitions, name, "taskObservation")) {
      args = {
        ...args,
        taskObservation: {
        mode: "detached_read_only",
        requestHash: core.sha256(String(checkpoint.sideQuery.request || "")),
        },
      };
    }
  } else if (ownership && toolAcceptsArgument(toolDefinitions, name, "taskAuthorization")) {
    args = { ...args, taskAuthorization: ownership };
  }

  if (args === sourceArgs && sessionBound === request) return request;
  return { ...sessionBound, arguments: args };
}

function replaceBufferedArgumentFragments(events: any[], callId: number, args: any): void {
  const matches = events.filter((event) => event.kind === "args" && event.callId === callId);
  if (matches.length === 0) return;
  matches[0].content = JSON.stringify(args || {});
  for (const event of matches.slice(1)) event.content = "";
}

const RECOVERY_CONTROL_TOOLS = [
  "unreal_task_checkpoint",
  "unreal_task_status",
  "unreal_task_recover_active",
  "unreal_task_cancel",
  "unreal_task_cancel_active",
];

// tools/list can be refreshed independently by the unreal-rag and
// unreal-agent MCP servers. LM Studio may therefore hand the generator a
// short-lived union containing a tool from the previous task phase. Keep the
// server call-time authorization as the final authority, but defensively
// intersect Unreal work tools with the latest server-owned route before the
// model sees them. Non-Unreal MCP tools are deliberately left untouched.
const ALWAYS_DISCOVERABLE_UNREAL_TOOLS = [
  "unreal_get_active_project",
  "unreal_set_active_project",
  "unreal_rag_health",
  "unreal_agent_plan",
  "unreal_task_status",
  "unreal_task_list_active",
  "unreal_task_recover_active",
  "unreal_task_cancel_active",
  "unreal_task_quarantine_corrupt",
  "unreal_task_retry_job_cancel",
  "unreal_task_define_slices",
  "unreal_task_resume",
  "unreal_task_cancel",
  "get_workspace_info",
  "get_active_project",
  "list_active_tasks",
  "cancel_active_task",
  "quarantine_corrupt_task",
];

// These tools establish or replace task ownership. Once a server-owned route
// exists, advertising them as generic discovery controls lets a compact model
// accidentally bootstrap again and replan the live task. That destroys
// plan-scoped read evidence during evidence refill. The exact server-required
// tool remains an override for intentional recovery and task replacement.
const ACTIVE_ROUTE_BOOTSTRAP_TOOLS = [
  "unreal_get_active_project",
  "unreal_set_active_project",
  TASK_PLANNER_TOOL_NAME,
];

function activeRouteBootstrapToolAllowed(tool: any, checkpoint: any): boolean {
  const name = String(tool?.function?.name || tool?.name || "").trim();
  const required = String(checkpoint?.requiredNextTool?.name || "").trim();
  if (required && !core.isNonToolNextAction(required) && toolNamesMatch(required, name)) {
    return true;
  }
  return !ACTIVE_ROUTE_BOOTSTRAP_TOOLS.some((bootstrap) => toolNamesMatch(bootstrap, name));
}

const UNPREFIXED_UNREAL_AGENT_TOOLS = [
  ...ALWAYS_DISCOVERABLE_UNREAL_TOOLS.filter((name) => !name.startsWith("unreal_")),
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
  "write_file",
  "replace_in_file",
  "apply_edit_bundle",
  "static_validate_project",
  "build_unreal_project",
  "run_unreal_automation_tests",
  "read_unreal_logs",
  "write_session_handoff",
  "record_bootstrap_step",
];

const TASK_CHECKPOINT_TOOL_NAME = "unreal_task_checkpoint";
const TOOL_CATALOG_REFRESH_TOOLS = [
  "get_active_project",
  "get_workspace_info",
  "list_active_tasks",
];

const BOUNDED_CONTROL_POLL_TOOLS = [
  "list_active_tasks",
  "unreal_task_list_active",
];

function stableControlObservation(value: any): any {
  if (Array.isArray(value)) return value.map(stableControlObservation);
  if (!value || typeof value !== "object") return value;
  const result: Record<string, any> = {};
  const volatileKeys = new Set([
    "authToken", "ownerCapability", "createdAt", "updatedAt", "recordedAt",
    "lastSeenAt", "heartbeatAt", "leaseExpiresAt", "expiresAt",
  ]);
  for (const key of Object.keys(value).sort()) {
    if (volatileKeys.has(key)) continue;
    result[key] = stableControlObservation(value[key]);
  }
  return result;
}

function repeatedUnchangedControlTools(messages: ChatMessage[]): string[] {
  const snapshots = core.snapshotMessages(messages);
  let start = 0;
  for (let index = snapshots.length - 1; index >= 0; index -= 1) {
    if (snapshots[index]?.role === "user") {
      start = index + 1;
      break;
    }
  }
  const calls = new Map<string, any>();
  const observations = new Map<string, string[]>();
  for (const snapshot of snapshots.slice(start)) {
    for (const call of snapshot.toolCalls || []) {
      const name = String(call.name || "");
      if (!BOUNDED_CONTROL_POLL_TOOLS.some((known) => toolNamesMatch(known, name))) continue;
      calls.set(String(call.id || ""), call);
    }
    for (const result of snapshot.toolResults || []) {
      if (!core.toolResultSucceeded(result)) continue;
      const call = calls.get(String(result.toolCallId || ""));
      const name = String(result.name || call?.name || "");
      const known = BOUNDED_CONTROL_POLL_TOOLS.find((candidate) => toolNamesMatch(candidate, name));
      if (!known) continue;
      const parsed = core.parseJsonObjects(result.content || "");
      const semanticResult = parsed.length > 0
        ? stableControlObservation(parsed[parsed.length - 1])
        : String(result.content || "").replace(/\s+/g, " ").trim();
      const fingerprint = core.stableStringify({
        arguments: stableControlObservation(call?.arguments || {}),
        result: semanticResult,
      });
      const prior = observations.get(known) || [];
      prior.push(fingerprint);
      observations.set(known, prior.slice(-2));
    }
  }
  return [...observations.entries()]
    .filter(([, fingerprints]) => (
      fingerprints.length >= 2 && fingerprints[0] === fingerprints[1]
    ))
    .map(([name]) => name);
}

function successfulToolCalledSinceLatestUser(messages: ChatMessage[], toolName: string): boolean {
  const snapshots = core.snapshotMessages(messages);
  let start = 0;
  for (let index = snapshots.length - 1; index >= 0; index -= 1) {
    if (snapshots[index]?.role === "user") {
      start = index + 1;
      break;
    }
  }
  const calls = new Map<string, any>();
  for (const snapshot of snapshots.slice(start)) {
    for (const call of snapshot.toolCalls || []) {
      if (!toolNamesMatch(toolName, String(call?.name || ""))) continue;
      const callId = String(call?.id || "").trim();
      if (callId) calls.set(callId, call);
    }
    for (const result of snapshot.toolResults || []) {
      const callId = String(result?.toolCallId || "").trim();
      const resultName = String(result?.name || calls.get(callId)?.name || "");
      if (
        (calls.has(callId) || toolNamesMatch(toolName, resultName))
        && core.toolResultSucceeded(result)
      ) {
        return true;
      }
    }
  }
  return false;
}

function injectRepeatedControlBoundaryRule(history: Chat, tools: string[]): void {
  if (tools.length === 0) return;
  const marker = "[UNREAL_UNCHANGED_CONTROL_BOUNDARY]";
  const rule = marker + "\n"
    + `Two unchanged successful control observations already completed (${tools.join(", ")}). `
    + "Do not poll them again in this user turn. Continue with the routed work tool or answer from retained state.";
  try {
    for (const message of history.getMessagesArray()) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(marker)) return;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return;
      }
    }
    history.append("system", rule);
  } catch {
    // Tool catalog filtering still enforces the boundary if rule injection fails.
  }
}

function isRecoveryControlTool(name: string): boolean {
  return RECOVERY_CONTROL_TOOLS.some((control) => toolNamesMatch(control, name));
}

function isUnrealStackTool(name: string): boolean {
  const normalized = String(name || "").trim().toLowerCase();
  return normalized.includes("unreal_")
    || UNPREFIXED_UNREAL_AGENT_TOOLS.some((known) => toolNamesMatch(known, normalized));
}

function routeAllowsTool(tool: any, checkpoint: any): boolean {
  const name = String(tool?.function?.name || tool?.name || "").trim();
  if (!isUnrealStackTool(name)) return true;

  const required = String(checkpoint?.requiredNextTool?.name || "").trim();
  if (required && !core.isNonToolNextAction(required) && toolNamesMatch(required, name)) {
    return true;
  }
  // A checkpoint is not a normal always-visible status control. It is exposed
  // only for an exact server-requested continuity handoff, otherwise compact
  // models tend to poll it instead of performing routed work.
  if (toolNamesMatch(TASK_CHECKPOINT_TOOL_NAME, name)) return false;
  if (ALWAYS_DISCOVERABLE_UNREAL_TOOLS.some((control) => toolNamesMatch(control, name))) {
    return true;
  }
  const activeTools = Array.isArray(checkpoint?.toolRoute?.activeTools)
    ? checkpoint.toolRoute.activeTools.map((item: any) => String(item || "").trim()).filter(Boolean)
    : [];
  return activeTools.some((active: string) => toolNamesMatch(active, name));
}

function validateToolRequest(request: any, checkpoint: any): { ok: boolean; reason?: string } {
  const required = checkpoint?.requiredNextTool?.name;
  const actual = requestedToolName(request);
  if (
    required
    && !core.isNonToolNextAction(required)
    && !toolNamesMatch(required, actual)
    && !isRecoveryControlTool(actual)
  ) {
    return { ok: false, reason: `requiredNextTool=${required}; received=${actual}` };
  }
  if (
    required
    && toolNamesMatch(required, actual)
    && !core.toolArgumentsSatisfy(checkpoint?.requiredNextTool?.args, request?.arguments)
    && !isRecoveryControlTool(actual)
  ) {
    return { ok: false, reason: `requiredNextTool=${required} arguments do not satisfy the server-owned requiredNextToolArgs` };
  }
  const completed = new Set(checkpoint?.completedToolCallIds || []);
  if (request?.id && completed.has(request.id)) {
    return { ok: false, reason: `tool call id already completed: ${request.id}` };
  }
  return { ok: true };
}

function reconcilePendingToolCalls(pendingCalls: any[], currentSnapshots: any[]): {
  remainingPending: any[];
  matchedIds: string[];
  matchedCalls: Array<{ pending: any; result: any }>;
  abandonedIds: string[];
} {
  const completed = currentSnapshots.flatMap((message: any) => message.toolResults || []);
  const activeCallIds = new Set(
    currentSnapshots
      .flatMap((message: any) => message.toolCalls || [])
      .map((call: any) => String(call?.id || "").trim())
      .filter(Boolean),
  );
  const anonymousCompletedCount = completed.filter((result: any) => !result.toolCallId).length;
  const matchedIds: string[] = [];
  const matchedCalls: Array<{ pending: any; result: any }> = [];
  const abandonedIds: string[] = [];
  const remainingPending = pendingCalls.filter((pending: any) => {
    const pendingId = String(pending?.id || "").trim();
    const observedResultCount = Number(pending?.observedToolResultCount || 0);
    const hasAnonymousBaseline = Number.isFinite(Number(pending?.observedAnonymousToolResultCount));
    const matchedResult = pendingId
      ? completed.find((result: any) => String(result?.toolCallId || "").trim() === pendingId)
      : (hasAnonymousBaseline
        ? (anonymousCompletedCount > Number(pending.observedAnonymousToolResultCount)
          ? completed.filter((result: any) => !result.toolCallId).at(-1)
          : null)
        : (completed.length > observedResultCount ? completed.at(-1) : null));
    if (matchedResult) {
      if (pendingId) matchedIds.push(pendingId);
      matchedCalls.push({ pending, result: matchedResult });
      return false;
    }
    const architectureCallRemovedFromActiveHistory = Boolean(
      pendingId
      && !activeCallIds.has(pendingId)
      && toolNamesMatch(ARCHITECTURE_TOOL_NAME, String(pending?.name || "")),
    );
    if (architectureCallRemovedFromActiveHistory) {
      // LM Studio can retain a durable pending checkpoint after the user stops
      // generation and deletes or replaces that assistant version. A read-only
      // architecture validator call that no longer exists in active history can
      // never receive a result, so retaining it deadlocks every later recovery
      // turn. Limit abandonment to this validator; unresolved mutation tools stay
      // fail-closed until an explicit result is recorded.
      abandonedIds.push(pendingId);
      return false;
    }
    return true;
  });
  return { remainingPending, matchedIds, matchedCalls, abandonedIds };
}

function serverControlReplayGuardMarker(control: any): string {
  const compact = core.compactServerControl(control);
  const requiredName = String(compact?.requiredTool?.name || "").trim();
  const retryBoundary = String(compact?.retryPolicy?.sameSemanticInput || "allowed");
  if (!compact || !requiredName || !["once", "forbidden"].includes(retryBoundary)) return "";
  return `serverRequiredToolSucceededWithoutAdvance:${core.sha256(core.stableStringify({
    taskSessionId: String(compact.taskSessionId || ""),
    epoch: Number(compact.epoch),
    controlFingerprint: String(compact.controlFingerprint || ""),
    requiredTool: requiredName.toLowerCase(),
    requiredArgs: compact.requiredTool?.args || {},
  }))}`;
}

function successfulServerOwnedRequiredToolDidNotAdvance(
  priorControl: any,
  nextControl: any,
  pending: any,
  result: any,
): boolean {
  if (!core.toolResultSucceeded(result)) return false;
  const hostToolCallId = String(pending?.hostToolCallId || "");
  const pendingId = String(pending?.id || "");
  if (
    !hostToolCallId.includes(":server_owned_required_tool:")
    && !pendingId.startsWith("server-owned-")
  ) return false;
  const prior = core.compactServerControl(priorControl);
  const next = core.compactServerControl(nextControl);
  if (!prior || !next || !prior.requiredTool || !next.requiredTool) return false;
  if (!["once", "forbidden"].includes(
    String(prior.retryPolicy?.sameSemanticInput || "allowed"),
  )) return false;
  if (
    String(prior.taskSessionId || "") !== String(next.taskSessionId || "")
    || Number(prior.epoch) !== Number(next.epoch)
    || String(prior.controlFingerprint || "") !== String(next.controlFingerprint || "")
  ) return false;
  const pendingName = String(pending?.name || "").trim();
  return Boolean(
    toolNamesMatch(prior.requiredTool.name, pendingName)
    && toolNamesMatch(next.requiredTool.name, pendingName)
    && core.toolArgumentsSatisfy(prior.requiredTool.args, pending?.arguments)
    && core.toolArgumentsSatisfy(next.requiredTool.args, pending?.arguments)
  );
}

function synthesisCommitAcknowledged(result: any, pending: any, prepared: any = null): boolean {
  if (!core.toolResultSucceeded(result)) return false;
  const args = pending?.arguments && typeof pending.arguments === "object"
    ? pending.arguments
    : {};
  const expectedTask = String(args?.taskAuthorization?.taskSessionId || "").trim();
  const expectedDigest = String(args.outputDigest || "").trim().toLowerCase();
  const expectedEpoch = Number(args.controlEpoch);
  const expectedTransactionId = String(args.synthesisTransactionId || "").trim().toLowerCase();
  const expectedControlFingerprint = String(args.controlFingerprint || "").trim().toLowerCase();
  const expectedMutationGeneration = Number(args.mutationGeneration);
  const expectedBundleHash = String(args.synthesisEvidenceBundleHash || "").trim().toLowerCase();
  const preparedOutput = String(
    pending?.preparedSynthesisOutput
    || prepared?.output
    || "",
  );
  if (!preparedOutput || core.sha256(preparedOutput) !== expectedDigest) return false;
  for (const payload of core.parseJsonObjects(result?.content || "")) {
    const lifecycle = payload?.synthesisLifecycle && typeof payload.synthesisLifecycle === "object"
      ? payload.synthesisLifecycle
      : payload?.state?.synthesisLifecycle;
    if (
      payload?.ok === true
      && lifecycle
      && ["commit_acked", "committed"].includes(String(lifecycle.status || "").toLowerCase())
      && String(payload.taskSessionId || lifecycle.taskSessionId || "").trim() === expectedTask
      && String(lifecycle.outputDigest || "").trim().toLowerCase() === expectedDigest
      && Number(lifecycle.controlEpoch) === expectedEpoch
      && String(lifecycle.synthesisTransactionId || "").trim().toLowerCase() === expectedTransactionId
      && String(lifecycle.controlFingerprint || "").trim().toLowerCase() === expectedControlFingerprint
      && Number(lifecycle.mutationGeneration) === expectedMutationGeneration
      && String(lifecycle.synthesisEvidenceBundleHash || "").trim().toLowerCase() === expectedBundleHash
    ) return true;
  }
  return false;
}

function applyAuthoritativeControlFromSynthesisResult(
  messages: ChatMessage[],
  checkpoint: any,
  result: any,
): any | null {
  const payloads = core.parseJsonObjects(result?.content || "");
  const directControls = payloads
    .map((payload: any) => core.compactServerControl(payload?.control))
    .filter(Boolean)
    .sort((left: any, right: any) => Number(right.epoch || 0) - Number(left.epoch || 0));
  if (directControls.length === 0) return null;
  let projection: any;
  try {
    projection = core.buildCheckpoint(messages, checkpoint || {}, { maxCheckpointFacts: 32 });
  } catch {
    return null;
  }
  const projectedControl = core.compactServerControl(projection?.serverControl);
  const directControl: any = directControls[0];
  // The matched internal handshake result is itself a trusted paired
  // transport record. Prefer its newest exact envelope when an older control
  // elsewhere in retained history wins projection tie-breaking.
  const control = !projectedControl
    || (
      directControl.taskSessionId === projectedControl.taskSessionId
      && Number(directControl.epoch || 0) >= Number(projectedControl.epoch || 0)
    )
    ? directControl
    : projectedControl;
  if (!control) return null;
  checkpoint.serverControl = control;
  checkpoint.protocolControl = projection.protocolControl || null;
  checkpoint.requiredNextTool = projection.requiredNextTool || null;
  checkpoint.requiredNextToolRef = projection.requiredNextTool?.reference || null;
  checkpoint.requiredNextToolArgs = projection.requiredNextTool?.args || null;
  checkpoint.toolRoute = projection.toolRoute || null;
  checkpoint.taskRouteOwnership = projection.taskRouteOwnership || checkpoint.taskRouteOwnership || null;
  checkpoint.synthesisReadiness = projection.synthesisReadiness || control.synthesisReadiness || null;
  checkpoint.mutationGeneration = Math.max(
    Number(checkpoint.mutationGeneration || 0),
    Number(projection.mutationGeneration || 0),
    Number(control.mutationGeneration || 0),
  );
  checkpoint.diagnostics = Array.isArray(projection.diagnostics)
    ? projection.diagnostics.slice(-64)
    : checkpoint.diagnostics;
  return control;
}

async function generateUnlocked(ctl: GeneratorController, history: Chat): Promise<void> {
  guardGeneratorAbort(ctl);
  const enabled = Boolean(configValue(ctl, "enabled", true));
  const observeOnly = Boolean(configValue(ctl, "observeOnly", false));
  const requireCheckpointPersistence = Boolean(configValue(ctl, "requireCheckpointPersistence", true));
  const configuredTargetModel = String(configValue(ctl, "targetModel", "") || "").trim();

  const messages = plainMessages(history);
  const auxiliaryMetaRequest = trailingMetaUserMessage(messages);
  let workingDirectory = "";
  try {
    workingDirectory = String(ctl.getWorkingDirectory() || "");
  } catch {
    workingDirectory = "";
  }

  // Session identity belongs to the LM Studio conversation/task, not whichever
  // model happens to be loaded for this prediction. Resolve and load the
  // checkpoint before touching model readiness so unload/reload/model switches
  // cannot strand an active task under a different checkpoint directory.
  const salt = workingDirectory;
  const lineage = core.messageLineageFingerprints(messages);
  const baseKey = core.baseSessionKey(messages, salt);
  const envSessionId = String(process.env.LMS_CONTEXT_COMPACTOR_SESSION_ID || "").trim();
  const marker = core.extractSessionMarker(messages) || envSessionId;
  const conversationSessionId = core.lmStudioConversationSessionFingerprint(
    workingDirectory,
    "",
  );
  let sessionResolution: any;
  if (conversationSessionId) {
    sessionResolution = {
      sessionId: conversationSessionId,
      reason: "lmstudio_conversation_directory",
      minted: false,
      baseKey,
    };
  } else if (marker) {
    sessionResolution = {
      sessionId: core.sessionFingerprint(messages, salt, { sessionMarker: marker }),
      reason: envSessionId ? "env" : "marker",
      minted: false,
      baseKey,
    };
  } else {
    sessionResolution = await (store as any).resolveSessionFork({
      baseKey,
      lineage,
      envSessionId,
    });
  }
  const primarySessionId = String(sessionResolution.sessionId);
  // LM Studio title/summary requests observe the conversation but must never
  // advance or dispatch the primary task's durable control state.
  const sessionId = auxiliaryMetaRequest
    ? `${primarySessionId}-aux-${core.sha256(String(auxiliaryMetaRequest.getText() || "")).slice(0, 16)}`
    : primarySessionId;
  if (conversationSessionId) {
    tryInjectSessionMarker(history, conversationSessionId);
  } else if (!marker && sessionResolution.minted) {
    tryInjectSessionMarker(history, sessionId);
  } else if (marker) {
    tryInjectSessionMarker(history, marker);
  }
  await (store as any).touchSessionFork(baseKey, sessionId, lineage).catch(() => undefined);
  debugAgentLog("H-SESSION", "generator.ts:generate", "session resolved", {
    reason: sessionResolution.reason,
    minted: Boolean(sessionResolution.minted),
    baseKey: String(baseKey).slice(0, 12),
    sessionId: String(sessionId).slice(0, 12),
    lineageLen: lineage.length,
    hasMarker: Boolean(marker),
  });
  let checkpoint = await loadCheckpointBestEffort(sessionId);
  const outerGenerateRequestId = `generate-${core.sha256(core.stableStringify({
    sessionId,
    checkpointGeneration: Number(checkpoint?.checkpointGeneration || 0),
    startedAt: Date.now(),
  })).slice(0, 32)}`;
  // Server-owned direct calls still cross LM Studio's host callback boundary.
  // Their request ids must therefore be unique per generator invocation; a
  // session/epoch-only id is reused by retries and makes the host treat the
  // second lifecycle as a duplicate tool call.
  const directToolCallRequestId = (prefix: string, discriminator: unknown = "0"): string => (
    `${prefix}-${outerGenerateRequestId}-${String(discriminator || "0")}`.slice(0, 240)
  );
  const hostFacingLifecycleCounts: HostFacingLifecycleCounts = {
    start: 0,
    name: 0,
    args: 0,
    end: 0,
    failure: 0,
  };
  const observeHostFacingLifecycleEvent = (event: any): void => {
    const kind = String(event?.kind || "");
    if (kind === "start") hostFacingLifecycleCounts.start += 1;
    else if (kind === "name") hostFacingLifecycleCounts.name += 1;
    else if (kind === "args") hostFacingLifecycleCounts.args += 1;
    else if (kind === "end") hostFacingLifecycleCounts.end += 1;
    else if (kind === "failure") hostFacingLifecycleCounts.failure += 1;
  };
  const emitHostFacingLifecycleEvent = (event: any): void => {
    emitToolLifecycleEvent(ctl, event, observeHostFacingLifecycleEvent);
  };
  const hostToolCallLifecycleDiagnostics: HostToolCallLifecycleDiagnostic[] = [];
  const hostToolCallLifecycle = createHostToolCallTransactionManager({
    outerGenerateRequestId,
    emit: emitHostFacingLifecycleEvent,
    onDiagnostic: (diagnostic) => {
      hostToolCallLifecycleDiagnostics.push(diagnostic);
      while (hostToolCallLifecycleDiagnostics.length > 256) {
        hostToolCallLifecycleDiagnostics.shift();
      }
    },
  });
  const hostToolCallLifecycleSnapshot = (): any => ({
    ...hostToolCallLifecycle.snapshot(),
    lmStudioLifecycleEventCounts: { ...hostFacingLifecycleCounts },
    lmStudioNameEventCount: hostFacingLifecycleCounts.name,
  });

  const deliverDirectProjectStatusResponse = async (
    targetCheckpoint: any,
    suppliedState: any,
  ): Promise<void> => {
    const state = core.compactDirectProjectStatus(suppliedState);
    if (!state || !state.responseText || !state.responseDigest) {
      const error: any = new Error("DIRECT_PROJECT_STATUS_INVALID_DURABLE_RESPONSE");
      error.code = "DIRECT_PROJECT_STATUS_INVALID_DURABLE_RESPONSE";
      throw error;
    }
    const responseEmitting = core.compactDirectProjectStatus({
      ...state,
      state: "response_emitting",
      responseEmittingAt: isoNow(),
    });
    if (!responseEmitting) throw new Error("DIRECT_PROJECT_STATUS_INVALID_RESPONSE_INTENT");
    targetCheckpoint.directProjectStatus = responseEmitting;
    await persistCheckpoint(
      sessionId,
      targetCheckpoint,
      requireCheckpointPersistence,
      "direct_project_status_response_intent",
    );
    ctl.fragmentGenerated(state.responseText, { reasoningType: "none" });
    const responseDelivered = core.compactDirectProjectStatus({
      ...responseEmitting,
      state: "response_delivered",
      responseDelivered: true,
      responseDeliveredAt: isoNow(),
    });
    if (!responseDelivered) throw new Error("DIRECT_PROJECT_STATUS_INVALID_DELIVERY_RECEIPT");
    targetCheckpoint.directProjectStatus = responseDelivered;
    await persistCheckpoint(
      sessionId,
      targetCheckpoint,
      requireCheckpointPersistence,
      "direct_project_status_response_delivered",
    );
    const terminalState = core.compactDirectProjectStatus({
      ...responseDelivered,
      state: state.outcome === "success" ? "complete" : "failed",
      completedAt: state.outcome === "success" ? isoNow() : "",
      failedAt: state.outcome === "success" ? "" : isoNow(),
    });
    if (!terminalState) throw new Error("DIRECT_PROJECT_STATUS_INVALID_TERMINAL_STATE");
    targetCheckpoint.directProjectStatus = terminalState;
    targetCheckpoint.predictionState = lifecycleState(
      targetCheckpoint,
      "completed",
      { stopReason: state.outcome === "success"
        ? "direct_project_status_complete"
        : "direct_project_status_failed" },
    );
    await persistCheckpoint(
      sessionId,
      targetCheckpoint,
      requireCheckpointPersistence,
      "direct_project_status_terminal",
    );
    await appendEventBestEffort(sessionId, {
      type: "direct_project_status_terminal",
      at: isoNow(),
      identity: state.identity,
      toolCallId: state.toolCallId,
      outcome: state.outcome,
      errorCode: state.errorCode || null,
      responseDigest: state.responseDigest,
      retryCount: 0,
    });
  };

  const deliverAcknowledgedSynthesis = async (): Promise<boolean> => {
    const delivery = checkpoint?.synthesisDelivery;
    if (
      String(checkpoint?.synthesisState?.status || "") !== "commit_acked"
      || !delivery
      || typeof delivery !== "object"
      || !String(delivery.output || "")
    ) return false;
    if (["emitting", "emitted", "uncertain"].includes(String(delivery.deliveryState || ""))) {
      return false;
    }
    const ackTool: any = ctl.getToolDefinitions().find((tool: any) => toolNamesMatch(
      "unreal_task_ack_synthesis_delivery",
      tool?.function?.name || tool?.name || "",
    ));
    if (!ackTool) {
      throw new Error("SYNTHESIS_DELIVERY_ACK_TOOL_UNAVAILABLE");
    }
    const output = String(delivery.output);
    const outputDigest = String(delivery.outputDigest || "");
    if (core.sha256(output) !== outputDigest) {
      throw new Error("SYNTHESIS_DELIVERY_DIGEST_MISMATCH");
    }
    const transactionId = String(delivery.transactionId || "");
    const deliveryId = core.sha256(core.stableStringify({
      synthesisTransactionId: transactionId,
      outputDigest,
    }));
    const deliveryReceiptId = core.sha256(core.stableStringify({
      synthesisTransactionId: transactionId,
      outputDigest,
      uiDeliveryCompleted: true,
    }));
    const recoveryArtifactPath = await store.saveSynthesisRecoveryArtifact(sessionId, {
      deliveryId,
      synthesisTransactionId: transactionId,
      outputDigest,
      output,
      createdAt: isoNow(),
    });
    const ackToolCallId = directToolCallRequestId(
      "synthesis-delivery-ack",
      deliveryReceiptId.slice(0, 32),
    );
    const operatorAuthorizedReemit = String(delivery.deliveryState || "") === "reemit_authorized";
    checkpoint.synthesisDelivery = {
      ...delivery,
      deliveryState: "emitting",
      deliveryGuarantee: operatorAuthorizedReemit
        ? "operator_authorized_duplicate_risk"
        : "at_most_once_with_recovery_artifact",
      deliveryId,
      deliveryReceiptId,
      recoveryArtifactPath,
      ackToolCallId,
    };
    await persistCheckpoint(
      sessionId,
      checkpoint,
      requireCheckpointPersistence,
      "synthesis_delivery_emit_intent",
    );
    ctl.fragmentGenerated(output, { reasoningType: "none" });
    if (checkpoint?.preparedSynthesis && typeof checkpoint.preparedSynthesis === "object") {
      checkpoint.preparedSynthesis = {
        ...checkpoint.preparedSynthesis,
        status: "delivery_pending",
      };
    }
    const ackArguments = {
      taskAuthorization: delivery.taskAuthorization || {
        taskSessionId: String(checkpoint?.serverControl?.taskSessionId || ""),
        ownerCapability: String(checkpoint?.taskRouteOwnership?.ownerCapability || ""),
      },
      synthesisTransactionId: transactionId,
      outputDigest,
      deliveryReceiptId,
    };
    const ackName = String(
      ackTool?.function?.name || ackTool?.name || "unreal_task_ack_synthesis_delivery",
    );
    const ackIdentity = {
      hostToolCallId: ackToolCallId,
      predictionAttemptId: `${outerGenerateRequestId}:synthesis-delivery-ack`,
      modelLocalCallId: "delivery-ack",
      rawToolCallId: ackToolCallId,
      toolCallId: ackToolCallId,
      toolName: ackName,
      source: "synthesis_delivery_ack",
      callId: 1_000_001,
      durableIdentity: true,
    };
    hostToolCallLifecycle.record({ stage: "start", ...ackIdentity });
    hostToolCallLifecycle.record({ stage: "name", ...ackIdentity, name: ackName });
    hostToolCallLifecycle.record({
      stage: "args",
      ...ackIdentity,
      content: JSON.stringify(ackArguments),
    });
    hostToolCallLifecycle.record({
      stage: "end",
      ...ackIdentity,
      request: { id: ackToolCallId, type: "function", name: ackName, arguments: ackArguments },
    });
    const ackPending = {
      id: ackToolCallId,
      name: ackName,
      arguments: ackArguments,
      dispatchState: "emitted",
      dispatchIntentAt: isoNow(),
      dispatchedAt: isoNow(),
      dispatchAttempt: 1,
      hostToolCallId: ackToolCallId,
      synthesisDeliveryAck: true,
    };
    checkpoint.pendingToolCall = null;
    checkpoint.pendingToolCalls = [
      ...(Array.isArray(checkpoint.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
      ackPending,
    ];
    checkpoint.synthesisState = lifecycleState(checkpoint, "delivery_pending", {
      outputDigest,
      stopReason: "synthesis_delivery_after_ack",
    });
    checkpoint.synthesisDelivery = {
      ...checkpoint.synthesisDelivery,
      deliveryState: "emitted",
      emittedAt: isoNow(),
    };
    await persistCheckpoint(
      sessionId,
      checkpoint,
      requireCheckpointPersistence,
      "synthesis_delivery_pending_server_receipt",
    );
    await appendEventBestEffort(sessionId, {
      type: "synthesis_delivery_emitted_ack_pending",
      at: isoNow(),
      outputDigest,
      transactionId,
      deliveryReceiptId,
    });
    return true;
  };
  const ambiguousSynthesisDelivery = Boolean(
    String(checkpoint?.synthesisState?.status || "") === "commit_acked"
    && ["emitting", "uncertain"].includes(
      String(checkpoint?.synthesisDelivery?.deliveryState || ""),
    )
  );
  const existingDeliveryRecoveryPending = [
    ...(Array.isArray(checkpoint?.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
    ...(checkpoint?.pendingToolCall ? [checkpoint.pendingToolCall] : []),
  ].find((pending: any) => toolNamesMatch(
    "unreal_task_recover_synthesis_delivery",
    String(pending?.name || ""),
  ));
  if (ambiguousSynthesisDelivery && !existingDeliveryRecoveryPending) {
    checkpoint.synthesisDelivery = {
      ...checkpoint.synthesisDelivery,
      deliveryState: "uncertain",
      deliveryGuarantee: "at_most_once_with_explicit_operator_recovery",
      uncertaintyDetectedAt: isoNow(),
    };
    await persistCheckpoint(
      sessionId,
      checkpoint,
      requireCheckpointPersistence,
      "synthesis_delivery_uncertain_recovery_artifact",
    );
    await appendEventBestEffort(sessionId, {
      type: "synthesis_delivery_state_uncertain",
      at: isoNow(),
      outputDigest: String(checkpoint?.synthesisDelivery?.outputDigest || ""),
      transactionId: String(checkpoint?.synthesisDelivery?.transactionId || ""),
      reason: "durable_emit_intent_without_atomic_ui_receipt",
    });
    const recoveryTool: any = ctl.getToolDefinitions().find((tool: any) => toolNamesMatch(
      "unreal_task_recover_synthesis_delivery",
      tool?.function?.name || tool?.name || "",
    ));
    if (!recoveryTool) {
      const error: any = new Error(
        "SYNTHESIS_DELIVERY_RECOVERY_TOOL_UNAVAILABLE: install or refresh the unreal-rag tool catalog.",
      );
      error.code = "SYNTHESIS_DELIVERY_RECOVERY_TOOL_UNAVAILABLE";
      throw error;
    }
    const recoveryName = String(
      recoveryTool?.function?.name
      || recoveryTool?.name
      || "unreal_task_recover_synthesis_delivery",
    );
    const transactionId = String(checkpoint?.synthesisDelivery?.transactionId || "");
    const outputDigest = String(checkpoint?.synthesisDelivery?.outputDigest || "");
    const recoveryToolCallId = directToolCallRequestId(
      "synthesis-delivery-recovery",
      transactionId.slice(0, 32),
    );
    const recoveryArguments = {
      taskAuthorization: checkpoint?.synthesisDelivery?.taskAuthorization || {
        taskSessionId: String(checkpoint?.serverControl?.taskSessionId || ""),
        ownerCapability: String(checkpoint?.taskRouteOwnership?.ownerCapability || ""),
      },
      synthesisTransactionId: transactionId,
      outputDigest,
      action: "mark_uncertain",
    };
    const recoveryPending = {
      id: recoveryToolCallId,
      name: recoveryName,
      arguments: recoveryArguments,
      dispatchState: "emitting",
      dispatchIntentAt: isoNow(),
      dispatchAttempt: 1,
      dispatchedAt: "",
      hostToolCallId: recoveryToolCallId,
      synthesisDeliveryRecovery: true,
    };
    checkpoint.pendingToolCall = null;
    checkpoint.pendingToolCalls = [
      ...(Array.isArray(checkpoint.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
      recoveryPending,
    ];
    checkpoint.synthesisDelivery = {
      ...checkpoint.synthesisDelivery,
      recoveryToolCallId,
    };
    await persistCheckpoint(
      sessionId,
      checkpoint,
      requireCheckpointPersistence,
      "synthesis_delivery_recovery_dispatch_intent",
    );
    const recoveryIdentity = {
      hostToolCallId: recoveryToolCallId,
      predictionAttemptId: `${outerGenerateRequestId}:synthesis-delivery-recovery`,
      modelLocalCallId: "delivery-recovery",
      rawToolCallId: recoveryToolCallId,
      toolCallId: recoveryToolCallId,
      toolName: recoveryName,
      source: "synthesis_delivery_recovery",
      callId: 1_000_002,
      durableIdentity: true,
    };
    hostToolCallLifecycle.record({ stage: "start", ...recoveryIdentity });
    hostToolCallLifecycle.record({ stage: "name", ...recoveryIdentity, name: recoveryName });
    hostToolCallLifecycle.record({
      stage: "args",
      ...recoveryIdentity,
      content: JSON.stringify(recoveryArguments),
    });
    hostToolCallLifecycle.record({
      stage: "end",
      ...recoveryIdentity,
      request: {
        id: recoveryToolCallId,
        type: "function",
        name: recoveryName,
        arguments: recoveryArguments,
      },
    });
    recoveryPending.dispatchState = "emitted";
    recoveryPending.dispatchedAt = isoNow();
    await persistCheckpoint(
      sessionId,
      checkpoint,
      requireCheckpointPersistence,
      "synthesis_delivery_recovery_dispatched",
    );
    return;
  }
  if (await deliverAcknowledgedSynthesis()) return;

  let unresolvedPendingCalls: any[] = [
    ...(Array.isArray(checkpoint?.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
    ...(checkpoint?.pendingToolCall ? [checkpoint.pendingToolCall] : []),
  ];
  let synthesisCommitRejected = false;
  let synthesisDeliveryAcknowledged = false;
  let synthesisDeliveryRecoveryRegistered = false;
  let synthesisNackControlApplied = false;
  let resolvedDirectProjectStatus: any = null;
  const successfulServerOwnedRequiredResults: Array<{ pending: any; result: any }> = [];
  if (checkpoint && unresolvedPendingCalls.length > 0) {
    const currentSnapshots = core.snapshotMessages(messages);
    const { remainingPending, matchedIds, matchedCalls, abandonedIds } = reconcilePendingToolCalls(
      unresolvedPendingCalls,
      currentSnapshots,
    );
    for (const match of matchedCalls) {
      const pendingName = String(match.pending?.name || "").trim();
      const hostToolCallId = String(match.pending?.hostToolCallId || "");
      const pendingId = String(match.pending?.id || "");
      if (
        core.toolResultSucceeded(match.result)
        && (
          hostToolCallId.includes(":server_owned_required_tool:")
          || pendingId.startsWith("server-owned-")
        )
      ) {
        successfulServerOwnedRequiredResults.push(match);
      }
      const synthesisLifecyclePayload = core.parseJsonObjects(match.result?.content || "")
        .map((payload: any) => ({
          payload,
          lifecycle: payload?.synthesisLifecycle && typeof payload.synthesisLifecycle === "object"
            ? payload.synthesisLifecycle
            : payload?.state?.synthesisLifecycle,
        }))
        .find(({ payload, lifecycle }: any) => payload?.ok === true && lifecycle);
      const resultLifecycle = synthesisLifecyclePayload?.lifecycle;
      const resultLifecycleStatus = String(resultLifecycle?.status || "").trim().toLowerCase();
      const localTransactionId = String(checkpoint?.synthesisDelivery?.transactionId || "").trim().toLowerCase();
      const localOutputDigest = String(checkpoint?.synthesisDelivery?.outputDigest || "").trim().toLowerCase();
      const exactDeliveryIdentity = Boolean(
        resultLifecycle
        && localTransactionId
        && localOutputDigest
        && String(resultLifecycle.synthesisTransactionId || "").trim().toLowerCase() === localTransactionId
        && String(resultLifecycle.outputDigest || "").trim().toLowerCase() === localOutputDigest
      );
      if (resultLifecycleStatus === "delivered" && exactDeliveryIdentity) {
        applyAuthoritativeControlFromSynthesisResult(messages, checkpoint, match.result);
        synthesisDeliveryAcknowledged = true;
        checkpoint.synthesisState = lifecycleState(checkpoint, "delivered", {
          outputDigest: localOutputDigest,
          stopReason: "operator_confirmed_delivery_visible",
        });
        if (checkpoint?.preparedSynthesis && typeof checkpoint.preparedSynthesis === "object") {
          checkpoint.preparedSynthesis = { ...checkpoint.preparedSynthesis, status: "delivered" };
        }
        checkpoint.synthesisDelivery = null;
        continue;
      }
      if (resultLifecycleStatus === "delivery_reemit_authorized" && exactDeliveryIdentity) {
        applyAuthoritativeControlFromSynthesisResult(messages, checkpoint, match.result);
        checkpoint.synthesisState = lifecycleState(checkpoint, "commit_acked", {
          outputDigest: localOutputDigest,
          stopReason: "operator_authorized_delivery_reemit",
        });
        checkpoint.synthesisDelivery = {
          ...checkpoint.synthesisDelivery,
          deliveryState: "reemit_authorized",
          deliveryGuarantee: "operator_authorized_duplicate_risk",
          deliveryAttempt: Math.max(1, Number(resultLifecycle.deliveryAttempt || 2)),
        };
        continue;
      }
      if (toolNamesMatch("unreal_task_recover_synthesis_delivery", pendingName)) {
        if (resultLifecycleStatus !== "delivery_uncertain" || !exactDeliveryIdentity) {
          throw new Error("SYNTHESIS_DELIVERY_RECOVERY_REJECTED");
        }
        const authoritativeControl = applyAuthoritativeControlFromSynthesisResult(
          messages,
          checkpoint,
          match.result,
        );
        if (!authoritativeControl || authoritativeControl.disposition !== "await_user") {
          throw new Error("SYNTHESIS_DELIVERY_RECOVERY_CONTROL_INVALID");
        }
        checkpoint.synthesisState = lifecycleState(checkpoint, "delivery_uncertain", {
          outputDigest: localOutputDigest,
          stopReason: "explicit_operator_recovery_required",
        });
        checkpoint.synthesisDelivery = {
          ...checkpoint.synthesisDelivery,
          deliveryState: "uncertain",
          deliveryGuarantee: "at_most_once_with_explicit_operator_recovery",
        };
        synthesisDeliveryRecoveryRegistered = true;
        continue;
      }
      if (match.pending?.directProjectStatus === true) {
        const durableState = core.compactDirectProjectStatus(checkpoint?.directProjectStatus);
        const pendingIdentity = String(match.pending?.directProjectStatusIdentity || "").trim();
        if (
          !durableState
          || !pendingIdentity
          || durableState.identity !== pendingIdentity
          || durableState.toolCallId !== String(match.pending?.id || "").trim()
        ) {
          const error: any = new Error("DIRECT_PROJECT_STATUS_IDENTITY_MISMATCH");
          error.code = "DIRECT_PROJECT_STATUS_IDENTITY_MISMATCH";
          throw error;
        }
        const resolution = directProjectStatusResponse(
          match.result,
          latestUserGoalText(messages),
        );
        resolvedDirectProjectStatus = core.compactDirectProjectStatus({
          ...durableState,
          state: "result_received",
          resultDigest: resolution.resultDigest,
          responseText: resolution.responseText,
          responseDigest: core.sha256(resolution.responseText),
          outcome: resolution.outcome,
          errorCode: resolution.errorCode,
          resultReceivedAt: isoNow(),
        });
        if (!resolvedDirectProjectStatus) {
          throw new Error("DIRECT_PROJECT_STATUS_RESULT_COULD_NOT_BE_COMMITTED");
        }
        checkpoint.directProjectStatus = resolvedDirectProjectStatus;
        continue;
      }
      if (toolNamesMatch("unreal_task_ack_synthesis_delivery", pendingName)) {
        const delivered = core.parseJsonObjects(match.result?.content || "").some((payload: any) => (
          payload?.ok === true
          && String(payload?.synthesisLifecycle?.status || payload?.state?.synthesisLifecycle?.status || "").toLowerCase() === "delivered"
          && String(payload?.synthesisLifecycle?.deliveryReceiptId || payload?.state?.synthesisLifecycle?.deliveryReceiptId || "")
            === String(match.pending?.arguments?.deliveryReceiptId || "")
        ));
        if (!delivered) {
          throw new Error("SYNTHESIS_DELIVERY_ACK_REJECTED");
        }
        synthesisDeliveryAcknowledged = true;
        checkpoint.synthesisState = lifecycleState(checkpoint, "delivered", {
          outputDigest: String(match.pending?.arguments?.outputDigest || ""),
          stopReason: "authoritative_delivery_ack",
        });
        if (checkpoint?.preparedSynthesis && typeof checkpoint.preparedSynthesis === "object") {
          checkpoint.preparedSynthesis = { ...checkpoint.preparedSynthesis, status: "delivered" };
        }
        checkpoint.synthesisDelivery = null;
        continue;
      }
      if (!toolNamesMatch("unreal_task_commit_synthesis", pendingName)) continue;
      const outputDigest = String(
        match.pending?.preparedSynthesisOutputDigest
        || match.pending?.arguments?.outputDigest
        || "",
      );
      if (synthesisCommitAcknowledged(match.result, match.pending, checkpoint?.preparedSynthesis)) {
        checkpoint.synthesisState = lifecycleState(checkpoint, "commit_acked", {
          outputDigest,
          stopReason: "authoritative_commit_ack",
        });
        if (checkpoint?.preparedSynthesis && typeof checkpoint.preparedSynthesis === "object") {
          checkpoint.preparedSynthesis = {
            ...checkpoint.preparedSynthesis,
            status: "commit_acked",
            dispatchedAt: String(checkpoint.preparedSynthesis.dispatchedAt || isoNow()),
          };
        }
        checkpoint.synthesisDelivery = {
          output: String(
            match.pending?.preparedSynthesisOutput
            || checkpoint?.preparedSynthesis?.output
            || "",
          ).slice(0, 131_072),
          outputDigest,
          transactionId: String(
            match.pending?.arguments?.synthesisTransactionId
            || match.pending?.id
            || "",
          ),
          acknowledgedAt: isoNow(),
          deliveryState: "pending",
          taskAuthorization: match.pending?.arguments?.taskAuthorization || null,
        };
        await appendEventBestEffort(sessionId, {
          type: "synthesis_commit_acked",
          at: isoNow(),
          outputDigest,
          toolCallId: String(match.pending?.id || ""),
        });
      } else {
        // A semantic NACK is a durable control transition, not a transport
        // failure.  Keep the prepared bytes independently of pendingToolCalls,
        // ingest the complete authoritative v2 route, and let the next turn
        // execute that exact recovery action instead of retrying this tx.
        const rejectedTransactionControl: any = core.compactServerControl(checkpoint?.serverControl) || {};
        const resultPayloads = core.parseJsonObjects(match.result?.content || "");
        const nackPayload = [...resultPayloads].reverse().find((payload: any) => (
          String(payload?.errorCode || payload?.code || "").trim()
          || core.compactServerControl(payload?.control)
        )) || {};
        const authoritativeControl = applyAuthoritativeControlFromSynthesisResult(
          messages,
          checkpoint,
          match.result,
        );
        synthesisNackControlApplied = synthesisNackControlApplied || Boolean(authoritativeControl);
        const pendingOutput = String(match.pending?.preparedSynthesisOutput || "");
        const pendingDigest = String(
          match.pending?.preparedSynthesisOutputDigest
          || match.pending?.arguments?.outputDigest
          || (pendingOutput ? core.sha256(pendingOutput) : outputDigest)
          || "",
        ).trim().toLowerCase();
        const controlForPrepared = rejectedTransactionControl;
        const preparedCandidate = checkpoint?.preparedSynthesis && typeof checkpoint.preparedSynthesis === "object"
          ? checkpoint.preparedSynthesis
          : {
            version: 1,
            status: "prepared",
            output: pendingOutput,
            outputDigest: pendingDigest,
            synthesisTransactionId: String(
              match.pending?.arguments?.synthesisTransactionId
              || match.pending?.id
              || "",
            ).trim().toLowerCase(),
            taskSessionId: String(
              controlForPrepared?.taskSessionId
              || match.pending?.arguments?.taskAuthorization?.taskSessionId
              || "",
            ).trim(),
            objectiveHash: String(
              match.pending?.arguments?.objectiveHash
              || checkpoint?.objectiveHash
              || "",
            ).trim().toLowerCase(),
            controlEpoch: Number(
              controlForPrepared?.epoch
              ?? match.pending?.arguments?.controlEpoch
              ?? 0,
            ),
            controlFingerprint: String(
              controlForPrepared?.controlFingerprint
              || match.pending?.arguments?.controlFingerprint
              || "",
            ).trim().toLowerCase(),
            planRevision: String(
              controlForPrepared?.planRevision
              || controlForPrepared?.synthesisReadiness?.planRevision
              || "",
            ).trim(),
            acceptedEvidenceHash: String(
              controlForPrepared?.synthesisReadiness?.acceptedEvidenceHash || "",
            ).trim().toLowerCase(),
            remainingFrontierHash: String(
              controlForPrepared?.synthesisReadiness?.remainingFrontierHash || "",
            ).trim().toLowerCase(),
            synthesisEvidenceBundleHash: String(
              controlForPrepared?.synthesisReadiness?.synthesisEvidenceBundleHash || "",
            ).trim().toLowerCase(),
            mutationGeneration: Math.max(
              0,
              Number(controlForPrepared?.mutationGeneration ?? checkpoint?.mutationGeneration ?? 0),
            ),
            preparedAt: isoNow(),
          };
        const rejectionCode = String(
          nackPayload?.errorCode
          || nackPayload?.code
          || "SYNTHESIS_NOT_READY",
        ).trim().slice(0, 120);
        const rejectedPrepared = {
          ...preparedCandidate,
          status: "rejected_stale",
          outputDigest: pendingDigest || String(preparedCandidate.outputDigest || "").trim().toLowerCase(),
          rejectedAt: isoNow(),
          rejectionCode,
          rejectedByControlEpoch: Number(authoritativeControl?.epoch ?? 0),
          rejectedByControlFingerprint: String(
            authoritativeControl?.controlFingerprint || authoritativeControl?.fingerprint || "",
          ).trim().toLowerCase(),
          staleReason: String(
            nackPayload?.error
            || nackPayload?.message
            || "authoritative synthesis control rejected the prepared transaction",
          ).slice(0, 160),
        };
        if (core.compactPreparedSynthesis(rejectedPrepared)) {
          checkpoint.preparedSynthesis = rejectedPrepared;
        }
        checkpoint.synthesisState = lifecycleState(checkpoint, "rejected_stale", {
          outputDigest,
          stopReason: `synthesis_commit_${rejectionCode.toLowerCase()}`,
        });
        checkpoint.synthesisDelivery = null;
        synthesisCommitRejected = true;
        await appendEventBestEffort(sessionId, {
          type: "synthesis_commit_nack",
          at: isoNow(),
          outputDigest,
          toolCallId: String(match.pending?.id || ""),
          errorCode: rejectionCode,
          controlEpoch: Number(authoritativeControl?.epoch ?? checkpoint?.serverControl?.epoch ?? 0),
          requiredTool: authoritativeControl?.requiredTool?.name || null,
        });
      }
    }
    if (remainingPending.length !== unresolvedPendingCalls.length) {
      checkpoint.completedToolCallIds = [
        ...(checkpoint.completedToolCallIds || []),
        ...matchedIds,
      ].filter((id: string, index: number, ids: string[]) => ids.indexOf(id) === index).slice(-256);
      checkpoint.pendingToolCall = null;
      checkpoint.pendingToolCalls = remainingPending;
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "pending_tool_result_reconciliation",
      );
      if (abandonedIds.length > 0) {
        await appendEventBestEffort(sessionId, {
          type: "pending_tool_calls_abandoned",
          at: new Date().toISOString(),
          reason: "read_only_architecture_call_absent_from_active_history",
          toolCallIds: abandonedIds,
        });
      }
    }
    unresolvedPendingCalls = remainingPending;
    if (resolvedDirectProjectStatus) {
      await deliverDirectProjectStatusResponse(checkpoint, resolvedDirectProjectStatus);
      return;
    }
    if (synthesisDeliveryAcknowledged) {
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "synthesis_delivery_acknowledged",
      );
      return;
    }
    if (synthesisDeliveryRecoveryRegistered) {
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "synthesis_delivery_operator_recovery_required",
      );
      return;
    }
    if (await deliverAcknowledgedSynthesis()) return;
    if (synthesisCommitRejected) {
      if (!synthesisNackControlApplied) {
        throw new Error(
          "Synthesis commit was rejected without a complete authoritative v2 control envelope; final output was withheld.",
        );
      }
      const refreshedControl: any = core.compactServerControl(checkpoint?.serverControl);
      const prepared = core.compactPreparedSynthesis(checkpoint?.preparedSynthesis);
      const refreshedReadiness = refreshedControl?.synthesisReadiness;
      const unchangedEvidence = Boolean(
        prepared
        && authoritativeSynthesisFinality(refreshedControl, checkpoint).eligible
        && prepared.objectiveHash === String(checkpoint?.objectiveHash || "").trim().toLowerCase()
        && prepared.acceptedEvidenceHash === String(refreshedReadiness?.acceptedEvidenceHash || "")
        && prepared.remainingFrontierHash === String(refreshedReadiness?.remainingFrontierHash || "")
        && prepared.synthesisEvidenceBundleHash === String(refreshedReadiness?.synthesisEvidenceBundleHash || "")
        && Number(prepared.mutationGeneration) === Number(refreshedControl?.mutationGeneration || 0)
      );
      if (unchangedEvidence && prepared) {
        const commitTool: any = ctl.getToolDefinitions().find((tool: any) => toolNamesMatch(
          "unreal_task_commit_synthesis",
          tool?.function?.name || tool?.name || "",
        ));
        if (!commitTool) throw new Error("SYNTHESIS_COMMIT_TOOL_UNAVAILABLE_DURING_STALE_RETRY");
        const outputDigest = String(prepared.outputDigest || "");
        const retryArguments = {
          taskAuthorization: {
            taskSessionId: String(refreshedControl.taskSessionId || ""),
            ownerCapability: String(checkpoint?.taskRouteOwnership?.ownerCapability || ""),
          },
          objectiveHash: String(checkpoint?.objectiveHash || ""),
          controlEpoch: Number(refreshedControl.epoch),
          controlFingerprint: String(refreshedControl.controlFingerprint || refreshedControl.fingerprint || ""),
          mutationGeneration: Number(refreshedControl.mutationGeneration || 0),
          synthesisEvidenceBundleHash: String(refreshedReadiness.synthesisEvidenceBundleHash || ""),
          outputDigest,
        } as any;
        retryArguments.synthesisTransactionId = core.sha256(core.stableStringify({
          taskSessionId: retryArguments.taskAuthorization.taskSessionId,
          objectiveHash: retryArguments.objectiveHash,
          controlEpoch: retryArguments.controlEpoch,
          controlFingerprint: retryArguments.controlFingerprint,
          mutationGeneration: retryArguments.mutationGeneration,
          synthesisEvidenceBundleHash: retryArguments.synthesisEvidenceBundleHash,
          outputDigest,
        }));
        const retryId = `synthesis-commit-${retryArguments.synthesisTransactionId.slice(0, 32)}`;
        const retryName = String(
          commitTool?.function?.name || commitTool?.name || "unreal_task_commit_synthesis",
        );
        const retryPending: any = {
          id: retryId,
          name: retryName,
          arguments: retryArguments,
          hostToolCallId: retryId,
          callbackCallId: 1_000_002,
          dispatchState: "emitting",
          dispatchIntentAt: isoNow(),
          dispatchAttempt: 1,
          preparedSynthesisOutput: prepared.output,
          preparedSynthesisOutputDigest: outputDigest,
        };
        checkpoint.preparedSynthesis = {
          ...prepared,
          status: "commit_sent",
          synthesisTransactionId: retryArguments.synthesisTransactionId,
          controlEpoch: retryArguments.controlEpoch,
          controlFingerprint: retryArguments.controlFingerprint,
          rejectedAt: "",
          rejectionCode: "",
          staleReason: "",
          dispatchedAt: isoNow(),
        };
        checkpoint.synthesisState = lifecycleState(checkpoint, "commit_sent", {
          outputDigest,
          stopReason: "stale_control_identical_digest_retry",
        });
        checkpoint.pendingToolCall = null;
        checkpoint.pendingToolCalls = [retryPending];
        await persistCheckpoint(
          sessionId,
          checkpoint,
          requireCheckpointPersistence,
          "stale_control_identical_digest_retry_intent",
        );
        const identity = {
          hostToolCallId: retryId,
          predictionAttemptId: `${outerGenerateRequestId}:stale-synthesis-retry`,
          modelLocalCallId: "stale-synthesis-retry",
          rawToolCallId: retryId,
          toolCallId: retryId,
          toolName: retryName,
          source: "stale_synthesis_retry",
          callId: 1_000_002,
          durableIdentity: true,
        };
        hostToolCallLifecycle.record({ stage: "start", ...identity });
        hostToolCallLifecycle.record({ stage: "name", ...identity, name: retryName });
        hostToolCallLifecycle.record({ stage: "args", ...identity, content: JSON.stringify(retryArguments) });
        hostToolCallLifecycle.record({
          stage: "end",
          ...identity,
          request: { id: retryId, type: "function", name: retryName, arguments: retryArguments },
        });
        retryPending.dispatchState = "emitted";
        retryPending.dispatchedAt = isoNow();
        await persistCheckpoint(
          sessionId,
          checkpoint,
          requireCheckpointPersistence,
          "stale_control_identical_digest_retry_emitted",
        );
        await appendEventBestEffort(sessionId, {
          type: "synthesis_stale_control_identical_digest_retried",
          at: isoNow(),
          outputDigest,
          synthesisTransactionId: retryArguments.synthesisTransactionId,
          modelInvoked: false,
        });
        return;
      }
      await appendEventBestEffort(sessionId, {
        type: "synthesis_nack_recovery_ready",
        at: isoNow(),
        nextRequiredTool: checkpoint?.requiredNextTool?.name || null,
        controlEpoch: Number(checkpoint?.serverControl?.epoch || 0),
        outputDurable: Boolean(core.compactPreparedSynthesis(checkpoint?.preparedSynthesis)),
        sameTransactionRetry: false,
        identicalDigestRetryEligible: unchangedEvidence,
      });
    }
  }

  if (checkpoint && unresolvedPendingCalls.length > 0) {
    const abandonedPreparedIds: string[] = [];
    const reEmittedPreparedIds: string[] = [];
    const retainedPending: any[] = [];
    for (const pending of unresolvedPendingCalls) {
      const dispatchState = String(pending?.dispatchState || "emitted");
      const pendingName = String(pending?.name || "").trim();
      const replayableSynthesisHandshake = toolNamesMatch("unreal_task_commit_synthesis", pendingName)
        || toolNamesMatch("unreal_task_ack_synthesis_delivery", pendingName)
        || toolNamesMatch("unreal_task_recover_synthesis_delivery", pendingName);
      if (
        !["prepared", "emitting"].includes(dispatchState)
        && !(replayableSynthesisHandshake && dispatchState === "emitted")
      ) {
        retainedPending.push(pending);
        continue;
      }
      const id = String(pending?.id || "").trim();
      const name = pendingName;
      const durableHostToolCallId = String(pending?.hostToolCallId || id).trim() || id;
      if (
        !core.mutationToolName(name)
        && !toolNamesMatch("unreal_task_commit_synthesis", name)
        && !toolNamesMatch("unreal_task_ack_synthesis_delivery", name)
        && !toolNamesMatch("unreal_task_recover_synthesis_delivery", name)
      ) {
        if (id) abandonedPreparedIds.push(id);
        continue;
      }
      if (!id) {
        // A mutation without a durable call id cannot be replayed safely and
        // must remain fail-closed for explicit operator reconciliation.
        retainedPending.push(pending);
        continue;
      }
      if (
        toolNamesMatch("unreal_task_commit_synthesis", name)
        && String(pending?.preparedSynthesisOutput || "")
        && core.sha256(String(pending.preparedSynthesisOutput))
          !== String(pending?.arguments?.outputDigest || "").trim().toLowerCase()
      ) {
        throw new Error(
          "Prepared synthesis bytes do not match the durable commit digest; replay was blocked.",
        );
      }
      // Persist the stable-id dispatch intent before crossing the frontend
      // boundary.  An `emitting` record without a result is replayed with the
      // same id: the frontend/result ledger and server mutation guards use
      // that identity to converge without minting a second semantic call.
      pending.dispatchState = "emitting";
      pending.dispatchIntentAt = isoNow();
      pending.dispatchAttempt = Math.max(0, Number(pending.dispatchAttempt || 0)) + 1;
      retainedPending.push(pending);
      checkpoint.pendingToolCall = null;
      checkpoint.pendingToolCalls = retainedPending.concat(
        unresolvedPendingCalls.slice(unresolvedPendingCalls.indexOf(pending) + 1),
      );
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "prepared_tool_call_dispatch_intent",
      );
      const replayPredictionAttemptId = `${outerGenerateRequestId}:reconnect-replay`;
      const replayEventIdentity = {
        hostToolCallId: durableHostToolCallId,
        predictionAttemptId: replayPredictionAttemptId,
        modelLocalCallId: String(pending?.callbackCallId ?? "prepared"),
        rawToolCallId: id,
        toolCallId: id,
        toolName: name,
        source: "reconnect_replay",
        callId: Number(pending?.callbackCallId || 0),
        durableIdentity: true,
      };
      hostToolCallLifecycle.record({ stage: "start", ...replayEventIdentity });
      hostToolCallLifecycle.record({ stage: "name", ...replayEventIdentity, name });
      hostToolCallLifecycle.record({
        stage: "args",
        ...replayEventIdentity,
        content: JSON.stringify(pending?.arguments || {}),
      });
      hostToolCallLifecycle.record({
        stage: "end",
        ...replayEventIdentity,
        request: {
          id,
          type: "function",
          name,
          arguments: pending?.arguments || {},
        },
      });
      pending.dispatchState = "emitted";
      pending.dispatchedAt = isoNow();
      reEmittedPreparedIds.push(id);
    }
    if (
      abandonedPreparedIds.length > 0
      || reEmittedPreparedIds.length > 0
      || retainedPending.length !== unresolvedPendingCalls.length
    ) {
      checkpoint.pendingToolCall = null;
      checkpoint.pendingToolCalls = retainedPending;
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "prepared_tool_call_reconciliation",
      );
      await appendEventBestEffort(sessionId, {
        type: "prepared_tool_calls_reconciled",
        at: isoNow(),
        abandonedReadOnlyToolCallIds: abandonedPreparedIds,
        reEmittedMutationToolCallIds: reEmittedPreparedIds,
      });
      unresolvedPendingCalls = retainedPending;
    }
  }
  if (unresolvedPendingCalls.length > 0) {
    await appendEventBestEffort(sessionId, {
      type: "generation_blocked",
      at: new Date().toISOString(),
      reason: "pending_tool_result_missing",
      pendingToolCalls: unresolvedPendingCalls.map((pending: any) => ({
        id: pending?.id || null,
        name: pending?.name || "",
      })),
    });
    throw new Error(
      `Generation is paused because ${unresolvedPendingCalls.length} prior tool call(s) still lack a result. `
      + "Wait for LM Studio to record the tool result, or resolve/cancel the failed tool call before sending another message.",
    );
  }


  const checkpointBeforeModelReadiness = checkpoint;
  const preliminaryCheckpoint: any = core.buildCheckpoint(
    messages,
    checkpoint || {},
    { maxCheckpointFacts: 32 },
  );
  const preliminaryServerControl = core.compactServerControl(preliminaryCheckpoint?.serverControl);
  const priorDelivery = checkpoint?.synthesisDelivery;
  const priorDeliveryTask = String(
    priorDelivery?.taskAuthorization?.taskSessionId
    || checkpoint?.serverControl?.taskSessionId
    || "",
  ).trim();
  const priorDeliveryOutput = String(priorDelivery?.output || "");
  const priorDeliveryDigest = String(priorDelivery?.outputDigest || "").trim().toLowerCase();
  if (
    !preliminaryCheckpoint?.synthesisDelivery
    && priorDelivery
    && typeof priorDelivery === "object"
    && priorDeliveryTask
    && preliminaryServerControl?.taskSessionId === priorDeliveryTask
    && priorDeliveryOutput
    && core.sha256(priorDeliveryOutput) === priorDeliveryDigest
    && String(preliminaryCheckpoint?.objectiveHash || "") === String(checkpoint?.objectiveHash || "")
  ) {
    // A structured await_user response can force a conservative full history
    // rebuild. The paired authoritative control is recoverable from history,
    // but the exact UI-delivery bytes intentionally are not. Rebind those
    // durable local bytes only to the same task and objective identity.
    preliminaryCheckpoint.synthesisDelivery = { ...priorDelivery };
    preliminaryCheckpoint.synthesisState = checkpoint?.synthesisState
      ? { ...checkpoint.synthesisState }
      : preliminaryCheckpoint.synthesisState;
    preliminaryCheckpoint.preparedSynthesis = checkpoint?.preparedSynthesis
      ? { ...checkpoint.preparedSynthesis }
      : preliminaryCheckpoint.preparedSynthesis;
  }
  if (auxiliaryMetaRequest) {
    preliminaryCheckpoint.serverControl = null;
    preliminaryCheckpoint.protocolControl = null;
    preliminaryCheckpoint.requiredNextTool = null;
    preliminaryCheckpoint.requiredNextToolRef = null;
    preliminaryCheckpoint.requiredNextToolArgs = null;
    preliminaryCheckpoint.toolRoute = null;
    preliminaryCheckpoint.taskRouteOwnership = null;
    preliminaryCheckpoint.taskRouteTerminal = false;
    preliminaryCheckpoint.recoveryObligation = null;
    preliminaryCheckpoint.postBudgetAction = null;
  }
  const replayGuardControl = core.compactServerControl(preliminaryCheckpoint?.serverControl);
  const replayGuardMarker = serverControlReplayGuardMarker(replayGuardControl);
  const replayGuardAlreadyDurable = Boolean(
    replayGuardMarker
    && Array.isArray(preliminaryCheckpoint?.diagnostics)
    && preliminaryCheckpoint.diagnostics.includes(replayGuardMarker)
  );
  const successfulRequiredToolWithoutAdvance = successfulServerOwnedRequiredResults.some(
    (match) => successfulServerOwnedRequiredToolDidNotAdvance(
      checkpoint?.serverControl,
      replayGuardControl,
      match.pending,
      match.result,
    ),
  );
  if (!auxiliaryMetaRequest && replayGuardMarker && (
    replayGuardAlreadyDurable || successfulRequiredToolWithoutAdvance
  )) {
    preliminaryCheckpoint.diagnostics = [
      ...(Array.isArray(preliminaryCheckpoint.diagnostics)
        ? preliminaryCheckpoint.diagnostics
        : []),
      replayGuardMarker,
    ].filter((value: string, index: number, values: string[]) => (
      values.indexOf(value) === index
    )).slice(-64);
    await persistCheckpoint(
      sessionId,
      preliminaryCheckpoint,
      requireCheckpointPersistence,
      "server_required_tool_succeeded_without_control_advance",
    );
    await appendEventBestEffort(sessionId, {
      type: "server_required_tool_replay_blocked",
      at: isoNow(),
      taskSessionId: String(replayGuardControl?.taskSessionId || ""),
      controlEpoch: Number(replayGuardControl?.epoch || 0),
      requiredTool: String(replayGuardControl?.requiredTool?.name || ""),
      successfulResultObserved: successfulRequiredToolWithoutAdvance,
      durableGuardReapplied: replayGuardAlreadyDurable,
    });
    const error: any = new Error(
      "SERVER_CONTROL_STALLED_AFTER_SUCCESSFUL_REQUIRED_TOOL: the exact server-owned tool succeeded, but the authoritative control epoch and obligation did not advance. Replay was blocked.",
    );
    error.code = "SERVER_CONTROL_STALLED_AFTER_SUCCESSFUL_REQUIRED_TOOL";
    error.errorCode = "SERVER_CONTROL_STALLED_AFTER_SUCCESSFUL_REQUIRED_TOOL";
    throw error;
  }
  const directProjectStatusGoal = latestUserGoalText(messages).trim();
  const directProjectStatusQuery = Boolean(
    !auxiliaryMetaRequest
    && !preliminaryCheckpoint?.serverControl
    && !preliminaryCheckpoint?.taskRouteOwnership
    && directProjectStatusGoal
    && directProjectStatusGoal.length <= 160
    && (
      /\b(?:(?:where\s+(?:is|was)\s+(?:the\s+)?(?:current|active)\s+project)|(?:(?:current|active)\s+project(?:\s+(?:path|location|directory|folder))?\s*(?:\?|$))|(?:(?:what|which|show|report)\b[^.!?\n]{0,48}\bproject(?:\s+(?:path|location|directory|folder))?\b))/iu.test(directProjectStatusGoal)
      || /(?:현재|활성)\s*프로젝트(?:\s*(?:경로|위치))?\s*(?:뭐|어디|야|인가|인지|\?|$)|프로젝트[^.!?\n]{0,24}(?:뭐|어디|알려|확인)/u.test(directProjectStatusGoal)
    )
    && !/(?:set|switch|change|select|clear|설정|변경|전환|선택|해제)/iu.test(directProjectStatusGoal)
    && !/(?:status|state|source|code|implementation|feature|module|build|test|fix|create|write|complete|develop|상태|소스|코드|구현|기능|모듈|빌드|테스트|수정|생성|추가|삭제|완성|개발)/iu.test(directProjectStatusGoal)
  );
  const directProjectStatusTool: any = directProjectStatusQuery
    ? ctl.getToolDefinitions().find((tool: any) => toolNamesMatch(
      "unreal_get_active_project",
      tool?.function?.name || tool?.name || "",
    ))
    : null;
  if (directProjectStatusQuery) {
    const toolName = String(
      directProjectStatusTool?.function?.name
      || directProjectStatusTool?.name
      || "unreal_get_active_project"
    );
    const normalizedGoalHash = core.sha256(
      normalizedDirectProjectStatusGoal(directProjectStatusGoal),
    );
    const userMessageId = latestUserMessageIdentity(messages);
    const directProjectStatusIdentity = core.sha256(core.stableStringify({
      sessionId,
      latestUserMessageId: userMessageId,
      normalizedUserGoalHash: normalizedGoalHash,
      toolName: "unreal_get_active_project",
    }));
    const requestId = `direct-project-status-${directProjectStatusIdentity.slice(0, 40)}`;
    const priorDirectState = core.compactDirectProjectStatus(
      preliminaryCheckpoint?.directProjectStatus,
    );
    const sameDirectQuery = priorDirectState?.identity === directProjectStatusIdentity;
    if (sameDirectQuery && ["complete", "failed"].includes(priorDirectState.state)) {
      await appendEventBestEffort(sessionId, {
        type: "direct_project_status_duplicate_suppressed",
        at: isoNow(),
        identity: directProjectStatusIdentity,
        terminalState: priorDirectState.state,
      });
      return;
    }
    if (sameDirectQuery && priorDirectState.state === "response_delivered") {
      const terminalState = core.compactDirectProjectStatus({
        ...priorDirectState,
        state: priorDirectState.outcome === "success" ? "complete" : "failed",
        completedAt: priorDirectState.outcome === "success" ? isoNow() : "",
        failedAt: priorDirectState.outcome === "success" ? "" : isoNow(),
      });
      if (!terminalState) throw new Error("DIRECT_PROJECT_STATUS_INVALID_RECOVERED_TERMINAL");
      preliminaryCheckpoint.directProjectStatus = terminalState;
      await persistCheckpoint(
        sessionId,
        preliminaryCheckpoint,
        requireCheckpointPersistence,
        "direct_project_status_terminal_recovered",
      );
      return;
    }
    if (sameDirectQuery && priorDirectState.state === "result_received") {
      await deliverDirectProjectStatusResponse(preliminaryCheckpoint, priorDirectState);
      return;
    }
    if (sameDirectQuery && priorDirectState.state === "response_emitting") {
      const uncertainState = core.compactDirectProjectStatus({
        ...priorDirectState,
        state: "failed",
        outcome: "failure",
        errorCode: "DIRECT_PROJECT_STATUS_DELIVERY_UNCERTAIN",
        responseDelivered: false,
        failedAt: isoNow(),
      });
      if (!uncertainState) throw new Error("DIRECT_PROJECT_STATUS_INVALID_UNCERTAIN_STATE");
      preliminaryCheckpoint.directProjectStatus = uncertainState;
      await persistCheckpoint(
        sessionId,
        preliminaryCheckpoint,
        requireCheckpointPersistence,
        "direct_project_status_delivery_uncertain",
      );
      await appendEventBestEffort(sessionId, {
        type: "direct_project_status_delivery_uncertain",
        at: isoNow(),
        identity: directProjectStatusIdentity,
        automaticReplay: false,
      });
      return;
    }
    if (sameDirectQuery && ["dispatch_pending", "emitted"].includes(priorDirectState.state)) {
      const errorCode = "DIRECT_PROJECT_STATUS_RESULT_MISSING";
      const korean = /[가-힣]/u.test(directProjectStatusGoal);
      const responseText = korean
        ? `현재 활성 프로젝트를 확인하지 못했습니다 (${errorCode}). 이전 프로젝트 조회 호출의 결과가 기록되지 않았습니다.`
        : `Could not determine the active project (${errorCode}). The prior project-status call has no recorded result.`;
      const failedReceipt = core.compactDirectProjectStatus({
        ...priorDirectState,
        state: "result_received",
        outcome: "failure",
        errorCode,
        resultDigest: core.sha256("missing-result"),
        responseText,
        responseDigest: core.sha256(responseText),
        resultReceivedAt: isoNow(),
      });
      if (!failedReceipt) throw new Error("DIRECT_PROJECT_STATUS_INVALID_MISSING_RESULT_STATE");
      await deliverDirectProjectStatusResponse(preliminaryCheckpoint, failedReceipt);
      return;
    }
    const createdAt = isoNow();
    const initialDirectState = core.compactDirectProjectStatus({
      version: 1,
      identity: directProjectStatusIdentity,
      sessionId,
      latestUserMessageId: userMessageId,
      normalizedUserGoalHash: normalizedGoalHash,
      toolName,
      toolCallId: requestId,
      state: "dispatch_pending",
      attemptCount: 1,
      resultDigest: "",
      responseText: "",
      responseDigest: "",
      responseDelivered: false,
      outcome: "",
      errorCode: "",
      createdAt,
      emittedAt: "",
      resultReceivedAt: "",
      responseEmittingAt: "",
      responseDeliveredAt: "",
      completedAt: "",
      failedAt: "",
    });
    if (!initialDirectState) throw new Error("DIRECT_PROJECT_STATUS_INVALID_INITIAL_STATE");
    preliminaryCheckpoint.directProjectStatus = initialDirectState;
    if (!directProjectStatusTool) {
      const errorCode = "DIRECT_PROJECT_STATUS_PROVIDER_UNAVAILABLE";
      const korean = /[가-힣]/u.test(directProjectStatusGoal);
      const responseText = korean
        ? `현재 활성 프로젝트를 확인하지 못했습니다 (${errorCode}). unreal_get_active_project 도구 제공자가 준비되지 않았습니다.`
        : `Could not determine the active project (${errorCode}). The unreal_get_active_project provider is unavailable.`;
      const unavailableState = core.compactDirectProjectStatus({
        ...initialDirectState,
        state: "result_received",
        outcome: "failure",
        errorCode,
        resultDigest: core.sha256("provider-unavailable"),
        responseText,
        responseDigest: core.sha256(responseText),
        resultReceivedAt: isoNow(),
      });
      if (!unavailableState) throw new Error("DIRECT_PROJECT_STATUS_INVALID_PROVIDER_FAILURE");
      await deliverDirectProjectStatusResponse(preliminaryCheckpoint, unavailableState);
      return;
    }
    const request = { id: requestId, type: "function", name: toolName, arguments: {} };
    const pending: any = {
      id: requestId,
      name: toolName,
      arguments: {},
      dispatchState: "emitting",
      dispatchIntentAt: isoNow(),
      dispatchAttempt: 1,
      hostToolCallId: requestId,
      directProjectStatus: true,
      directProjectStatusIdentity,
    };
    preliminaryCheckpoint.pendingToolCall = null;
    preliminaryCheckpoint.pendingToolCalls = [pending];
    preliminaryCheckpoint.predictionState = lifecycleState(
      preliminaryCheckpoint,
      "completed",
      { stopReason: "server_owned_direct_project_status" },
    );
    await persistCheckpoint(
      sessionId,
      preliminaryCheckpoint,
      requireCheckpointPersistence,
      "direct_project_status_dispatch_intent",
    );
    const identity = {
      hostToolCallId: requestId,
      predictionAttemptId: `${outerGenerateRequestId}:direct-project-status`,
      modelLocalCallId: "0",
      rawToolCallId: requestId,
      toolCallId: requestId,
      toolName,
      source: "server_owned_project_status",
      callId: 0,
      durableIdentity: true,
    };
    hostToolCallLifecycle.record({ stage: "start", ...identity });
    hostToolCallLifecycle.record({ stage: "name", ...identity, name: toolName });
    hostToolCallLifecycle.record({ stage: "args", ...identity, content: "{}" });
    hostToolCallLifecycle.record({ stage: "end", ...identity, request });
    pending.dispatchState = "emitted";
    pending.dispatchedAt = isoNow();
    preliminaryCheckpoint.directProjectStatus = core.compactDirectProjectStatus({
      ...initialDirectState,
      state: "emitted",
      emittedAt: isoNow(),
    });
    if (!preliminaryCheckpoint.directProjectStatus) {
      throw new Error("DIRECT_PROJECT_STATUS_INVALID_EMITTED_STATE");
    }
    await persistCheckpoint(
      sessionId,
      preliminaryCheckpoint,
      requireCheckpointPersistence,
      "direct_project_status_emitted",
    );
    await appendEventBestEffort(sessionId, {
      type: "server_control_tool_direct_emitted",
      at: isoNow(),
      phase: "project_status",
      tool: toolName,
      modelSerializationBypassed: true,
      modelReadinessBypassed: true,
    });
    return;
  }
  preliminaryCheckpoint.predictionState = mergeLifecycleState(
    preliminaryCheckpoint.predictionState,
    lifecycleState(preliminaryCheckpoint, "pending", { stopReason: "model_readiness" }),
  );
  await persistCheckpoint(
    sessionId,
    preliminaryCheckpoint,
    requireCheckpointPersistence,
    "before_model_readiness",
  );

  let model: any;
  let resolvedTargetModel = configuredTargetModel;
  let autoSelected = false;
  try {
    const resolved = await resolveTargetModel(ctl, configuredTargetModel, {
      timeoutSeconds: finiteNumber(
        configValue(ctl, "modelReadinessTimeoutSeconds", 120), 120, 0, 900,
      ),
      pollIntervalSeconds: finiteNumber(
        configValue(ctl, "modelReadinessPollIntervalSeconds", 2), 2, 0.01, 30,
      ),
    });
    model = resolved.model;
    resolvedTargetModel = resolved.resolvedTargetModel;
    autoSelected = resolved.autoSelected;
  } catch (error: any) {
    const readinessAborted = Boolean((ctl as any)?.abortSignal?.aborted);
    const readinessErrorCode = String(
      error?.code || (readinessAborted ? "GENERATION_ABORTED" : "MODEL_NOT_READY"),
    ).slice(0, 120);
    preliminaryCheckpoint.predictionState = mergeLifecycleState(
      preliminaryCheckpoint.predictionState,
      lifecycleState(preliminaryCheckpoint, "pending", {
        stopReason: readinessAborted ? readinessErrorCode.toLowerCase() : "model_not_ready",
      }),
    );
    await persistCheckpoint(
      sessionId,
      preliminaryCheckpoint,
      requireCheckpointPersistence,
      readinessAborted ? "model_readiness_aborted" : "model_readiness_failed",
    );
    await appendEventBestEffort(sessionId, {
      type: readinessAborted ? "model_readiness_aborted" : "model_readiness_failed",
      at: isoNow(),
      targetModel: configuredTargetModel,
      errorCode: readinessErrorCode,
      error: String(error?.message || error).slice(0, 1000),
    });
    throw error;
  }
  if (autoSelected) {
    await appendEventBestEffort(sessionId, {
      type: "target_model_auto_selected",
      at: isoNow(),
      targetModel: resolvedTargetModel,
    });
  }

  const nextCheckpoint: any = preliminaryCheckpoint;
  nextCheckpoint.runtimeModelId = resolvedTargetModel;
  const persistPredictionSetupFailure = async (error: any, phase: string): Promise<void> => {
    const signalAborted = Boolean((ctl as any)?.abortSignal?.aborted);
    const errorCode = String(
      error?.code || (signalAborted ? "GENERATION_ABORTED" : "PREDICTION_SETUP_FAILED"),
    ).slice(0, 120);
    const stopReason = errorCode.toLowerCase();
    nextCheckpoint.predictionState = mergeLifecycleState(
      nextCheckpoint.predictionState,
      lifecycleState(nextCheckpoint, "pending", { stopReason }),
    );
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "prediction_setup_rejected",
    );
    await appendEventBestEffort(sessionId, {
      type: "prediction_setup_rejected",
      at: isoNow(),
      phase,
      errorCode,
      signalAborted,
      taskSessionId: String(nextCheckpoint?.taskRouteOwnership?.taskSessionId || ""),
    });
  };
  const modelFenceTimeoutMs = finiteNumber(
    configValue(ctl, "predictionWallClockSeconds", 180), 180, 5, 1800,
  ) * 1000;
  const transactionSetupStartedAt = performance.now();
  let contextLength: number;
  let transactionModelFence: any;
  try {
    contextLength = Number(await boundedPredictionSetupValue(
      ctl,
      modelFenceTimeoutMs,
      transactionSetupStartedAt,
      () => model.getContextLength(),
    ));
    transactionModelFence = await captureModelFence(
      model,
      resolvedTargetModel,
      contextLength,
      {
        signal: (ctl as any)?.abortSignal,
        timeoutMs: Math.max(
          1,
          modelFenceTimeoutMs - (performance.now() - transactionSetupStartedAt),
        ),
      },
    );
  } catch (error) {
    await persistPredictionSetupFailure(error, "transaction_model_setup");
    throw error;
  }
  if (isQwen38_27b(resolvedTargetModel) && !transactionModelFence.instanceReference) {
    const error: any = new Error(
      "Qwen 3.8 27B reasoning policy requires an observable LM Studio instance reference; "
      + "prediction was blocked because the runtime instance could not be pinned.",
    );
    error.code = "REASONING_MODEL_INSTANCE_UNPINNED";
    throw error;
  }
  let toolDefinitions = ctl.getToolDefinitions();
  nextCheckpoint.modelFence = transactionModelFence;
  const persistedFeatureResume = checkpointBeforeModelReadiness?.featureIntentResume;
  nextCheckpoint.featureIntentResume = null;
  let featureIntentRediscoveryActive = false;
  let featureIntentRediscoveryRemaining = 0;
  nextCheckpoint.compactionGeneration = Number(checkpointBeforeModelReadiness?.compactionGeneration || 0);
  const serverControlV2 = core.compactServerControl(nextCheckpoint?.serverControl);
  const serverControlV2Active = Boolean(serverControlV2);
  const latestRawMessage: any = messages.length > 0 ? messages[messages.length - 1] : null;
  const latestRawRole = String(
    typeof latestRawMessage?.getRole === "function"
      ? latestRawMessage.getRole()
      : latestRawMessage?.role || latestRawMessage?.type || "",
  ).trim().toLowerCase();
  const userInputResumeOwnership = core.compactTaskRouteOwnership(nextCheckpoint?.taskRouteOwnership);
  const userInputResumeActive = Boolean(
    serverControlV2Active
    && String(serverControlV2?.disposition || "") === "await_user"
    && serverControlV2?.requiredUserInput
    && latestRawRole === "user"
    && userInputResumeOwnership
  );
  const trailingMetaUser = auxiliaryMetaRequest;
  const architectureGoal = latestUserGoalText(messages);
  const latestTurnRequestIntentContext = matchingRequestIntentContext(
    architectureGoal,
    nextCheckpoint,
  );
  const latestTurnIntent = core.classifyUserTurnIntent(architectureGoal, {
    hasActiveTask: Boolean(nextCheckpoint?.taskRouteOwnership && nextCheckpoint?.toolRoute?.routeHash),
    activeObjective: String(nextCheckpoint?.objective || ""),
    ...latestTurnRequestIntentContext,
  });
  const detachedSideQueryActive = Boolean(
    !serverControlV2Active
    && nextCheckpoint?.sideQuery?.active
    && (
      !latestTurnRequestIntentContext.requestIntent
      || latestTurnIntent === "SIDE_QUERY"
    )
  );
  if (detachedSideQueryActive) {
    injectDetachedSideQueryRule(
      history,
      String(nextCheckpoint.sideQuery.request || architectureGoal),
    );
  }
  // buildCheckpoint retains the active objective across context-dependent
  // utterances such as "continue".  Use that authoritative goal for routing
  // and planner binding while keeping the raw latest text only for detecting
  // an explicit recovery continuation below.
  const checkpointObjectiveHash = String(nextCheckpoint?.objectiveHash || "").trim().toLowerCase();
  const latestRawGoalMatchesCheckpoint = Boolean(
    checkpointObjectiveHash
    && core.objectiveHashOf(architectureGoal) === checkpointObjectiveHash
  );
  const durableFullObjective = String(nextCheckpoint?.objectiveFull || "").trim();
  const durableFullObjectiveMatchesCheckpoint = Boolean(
    durableFullObjective
    && core.objectiveHashOf(durableFullObjective) === checkpointObjectiveHash
  );
  const authoritativeGoal = String(
    detachedSideQueryActive
      ? nextCheckpoint?.sideQuery?.request
      : latestRawGoalMatchesCheckpoint
        ? architectureGoal
        : durableFullObjectiveMatchesCheckpoint
          ? durableFullObjective
          : (nextCheckpoint?.objective || architectureGoal),
  ).trim();
  // A checkpoint intent is authoritative only for the exact trimmed UTF-8
  // objective hash it was issued for. First turns, detached side queries, and
  // changed goals intentionally receive an empty context and keep the legacy
  // classifier fallback.
  // The checkpoint objective is a bounded model-facing projection. Prefer the
  // full latest raw objective for classification when it carries the matching
  // server hash, so long UTF-8 requests do not fall back merely because the
  // checkpoint text was truncated.
  const latestRawRequestIntentContext = latestTurnRequestIntentContext;
  const authoritativeClassificationGoal = latestRawRequestIntentContext.requestIntent
    ? architectureGoal
    : authoritativeGoal;
  const authoritativeRequestIntentContext = latestRawRequestIntentContext.requestIntent
    ? latestRawRequestIntentContext
    : matchingRequestIntentContext(authoritativeClassificationGoal, nextCheckpoint);
  const workflowStopActive = Boolean(
    !detachedSideQueryActive
    && (
      ["workflow_stop", "complete", ...(userInputResumeActive ? [] : ["await_user"])].includes(
        String(serverControlV2?.disposition || ""),
      )
      || (
        !serverControlV2Active
        && nextCheckpoint?.semanticBlocker?.active === true
        && nextCheckpoint.semanticBlocker.stopCurrentWorkflow === true
        && !String(nextCheckpoint.semanticBlocker.clearOnTool || "").trim()
      )
    )
  );
  if (workflowStopActive) {
    injectWorkflowStopRule(history, nextCheckpoint.semanticBlocker);
  }
  const sourceWorkIntent = core.classifySourceWorkIntent(
    authoritativeClassificationGoal,
    authoritativeRequestIntentContext,
  );
  const sourceAdmissionInfrastructureAvailable = Boolean(
    toolDefinitions.some((tool: any) => toolNamesMatch(
      TASK_PLANNER_TOOL_NAME,
      tool?.function?.name || tool?.name || "",
    ))
    && toolDefinitions.some((tool: any) => toolNamesMatch(
      "unreal_get_active_project",
      tool?.function?.name || tool?.name || "",
    ))
  );
  const targetedSourceAdmissionCategory = new Set<string>([
    core.SOURCE_WORK_CATEGORIES?.TARGETED_SOURCE_ANALYSIS,
    core.SOURCE_WORK_CATEGORIES?.TARGETED_SOURCE_EDIT,
  ]).has(String(sourceWorkIntent.category));
  const sourceAnalysisAdmissionRequired = Boolean(
    !detachedSideQueryActive
    && !trailingMetaUser
    && sourceWorkIntent.requiresDurableTask === true
    && (sourceAdmissionInfrastructureAvailable || targetedSourceAdmissionCategory)
    && !serverControlV2Active
    && !nextCheckpoint?.taskRouteOwnership
  );
  const persistedArchitectureRecovery = Boolean(
    nextCheckpoint?.architectureProposal?.validationOk === false
    && architectureRecoveryContinuationRequested(architectureGoal),
  );
  // Once the MCP supplies an architecture control envelope, the server FSM is
  // authoritative. Legacy heuristic orchestration remains only for histories
  // that predate the envelope/checkpoint migration.
  const serverOwnedArchitectureControl = Boolean(nextCheckpoint?.architectureControl);
  const architectureValidationRequired = !detachedSideQueryActive
    && !workflowStopActive
    && !serverControlV2Active
    && !serverOwnedArchitectureControl && !trailingMetaUser && (
    !sourceAnalysisAdmissionRequired
    && (
      requiresArchitectureValidation(authoritativeGoal, toolDefinitions)
    || persistedArchitectureRecovery
    )
  );
  if (architectureValidationRequired) {
    injectArchitectureValidationRule(history);
  }
  const plannerTool = toolDefinitions.find((tool: any) => toolNamesMatch(
    TASK_PLANNER_TOOL_NAME,
    tool?.function?.name || tool?.name || "",
  ));
  const plannerAvailable = Boolean(plannerTool);
  const routeOwnershipAvailable = Boolean(nextCheckpoint?.taskRouteOwnership);
  const exactToolRouteAvailable = Boolean(
    nextCheckpoint?.toolRoute?.routeHash
    && Array.isArray(nextCheckpoint?.toolRoute?.activeTools)
    && nextCheckpoint.toolRoute.activeTools.length > 0
  );
  const projectAgentDiscoveryAvailable = [
    "get_workspace_info",
    "get_active_project",
    "list_active_tasks",
  ].some((expected) => toolDefinitions.some((tool: any) => toolNamesMatch(
    expected,
    tool?.function?.name || tool?.name || "",
  )));
  const activeProjectBootstrapTool: any = toolDefinitions.find((tool: any) => toolNamesMatch(
    "unreal_get_active_project",
    tool?.function?.name || tool?.name || "",
  ));
  const unroutedMutationDefinitionsPresent = Boolean(
    !routeOwnershipAvailable
    && toolDefinitions.some((tool: any) => core.mutationToolName(
      tool?.function?.name || tool?.name || "",
    )),
  );
  if (!detachedSideQueryActive && !routeOwnershipAvailable && (projectAgentDiscoveryAvailable || unroutedMutationDefinitionsPresent)) {
    injectTaskRouteOwnershipRule(history, plannerAvailable);
  }
  const routeHash = String(nextCheckpoint?.toolRoute?.routeHash || "").trim();
  const routedMutationTools = Array.isArray(nextCheckpoint?.toolRoute?.activeTools)
    ? nextCheckpoint.toolRoute.activeTools
      .map((name: any) => String(name || "").trim())
      .filter((name: string) => core.mutationToolName(name))
    : [];
  const rawMutationDefinitionsPresent = toolDefinitions.some((tool: any) => core.mutationToolName(
    tool?.function?.name || tool?.name || "",
  ));
  const catalogRefreshTool: any = TOOL_CATALOG_REFRESH_TOOLS
    .map((expected) => toolDefinitions.find((tool: any) => toolNamesMatch(
      expected,
      tool?.function?.name || tool?.name || "",
    )))
    .find(Boolean);
  const priorCatalogRefresh = checkpoint?.catalogRefresh
    && checkpoint.catalogRefresh.routeHash === routeHash
    ? checkpoint.catalogRefresh
    : null;
  const catalogRefreshNeeded = Boolean(
    !detachedSideQueryActive
    && !serverControlV2Active
    && routeOwnershipAvailable
    && exactToolRouteAvailable
    && routedMutationTools.length > 0
    && !rawMutationDefinitionsPresent
  );
  let catalogRefreshForced = false;
  let catalogRefreshBlocked = false;
  const architectureEvidenceReadThreshold = Math.floor(finiteNumber(
    configValue(ctl, "architectureEvidenceReadThreshold", 4), 4, 1, 64,
  ));
  const architectureEvidenceHardLimit = Math.floor(finiteNumber(
    configValue(ctl, "architectureEvidenceHardLimit", 8), 8, architectureEvidenceReadThreshold, 128,
  ));
  const featureIntentEvidenceReadThreshold = Math.floor(finiteNumber(
    configValue(ctl, "featureIntentEvidenceReadThreshold", 3), 3, 1, 16,
  ));
  const architectureReplanEvidenceReadBudget = Math.floor(finiteNumber(
    configValue(ctl, "architectureReplanEvidenceReadBudget", 4), 4, 0, 32,
  ));
  const preRouteDiscoveryLimit = Math.floor(finiteNumber(
    configValue(ctl, "preRouteDiscoveryLimit", 6), 6, 1, 32,
  ));
  const durableInspectionDiscoveryLimit = Math.floor(finiteNumber(
    configValue(ctl, "durableInspectionDiscoveryLimit", 2), 2, 1, 8,
  ));
  const architectureStatus = architectureGateStatus(messages, nextCheckpoint);
  const durableInspectionPlanningRequired = requiresDurableInspectionPlanning(
    authoritativeClassificationGoal,
    authoritativeRequestIntentContext,
  );
  const taskRoutePlanningRequired = Boolean(
    requiresTaskRoutePlanning(authoritativeClassificationGoal, authoritativeRequestIntentContext)
    || durableInspectionPlanningRequired
  );
  const activeProjectResolved = Boolean(
    String(nextCheckpoint?.activeProject || nextCheckpoint?.activeProjectName || "").trim(),
  );
  const nonDurablePlannerSuppressionRequired = Boolean(
    !serverControlV2Active
    && !routeOwnershipAvailable
    && !taskRoutePlanningRequired
    && !durableInspectionPlanningRequired
    && (
      String(sourceWorkIntent.category) === String(core.SOURCE_WORK_CATEGORIES?.PROJECT_STATUS_ONLY)
      || (
        String(sourceWorkIntent.category) === String(core.SOURCE_WORK_CATEGORIES?.GENERIC_CONCEPT_ANSWER)
        && sourceWorkIntent.conceptQuestion === true
      )
    )
  );
  if (sourceAnalysisAdmissionRequired) {
    injectSourceAnalysisAdmissionRule(history, plannerAvailable);
  }
  const featureCompletionAuditRequired = requiresFeatureCompletionAudit(authoritativeGoal);
  const effectiveFeatureIntentEvidenceReadThreshold = featureCompletionAuditRequired
    ? Math.max(featureIntentEvidenceReadThreshold, 6)
    : featureIntentEvidenceReadThreshold;
  if (
    !serverControlV2Active
    &&
    persistedFeatureResume
    && typeof persistedFeatureResume === "object"
    && !Array.isArray(persistedFeatureResume)
  ) {
    const currentFeatureResultCount = observedToolResultCount(messages, FEATURE_INTENT_TOOL_NAME);
    const featureResultPending = currentFeatureResultCount
      <= Number(persistedFeatureResume.observedResultCount || 0);
    const currentDiscoveryResultCount = observedToolResultCountForNames(
      messages,
      ARCHITECTURE_DISCOVERY_TOOLS,
    );
    const resumeMode = String(persistedFeatureResume.mode || "legacy_exact");
    const restoreExactResume = () => {
      nextCheckpoint.featureIntentResume = persistedFeatureResume;
      const newerRequiredTool = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
      // Preserve a newer checkpoint/control-plane handoff. Once it succeeds,
      // the deferred target-read flow becomes eligible again.
      if (!newerRequiredTool || toolNamesMatch(FEATURE_INTENT_TOOL_NAME, newerRequiredTool)) {
        nextCheckpoint.requiredNextTool = {
          name: FEATURE_INTENT_TOOL_NAME,
          reference: "feature_intent_target_evidence_resume",
          args: mergeServerOwnedArguments({}, persistedFeatureResume.args || {}),
        };
      }
    };
    if (featureResultPending && resumeMode === "awaiting_target_read") {
      const targetReadCompleted = currentDiscoveryResultCount
        > Number(persistedFeatureResume.observedDiscoveryResultCount || 0);
      const resumedTargetFiles = featureIntentRequestedTargetFiles({
        arguments: persistedFeatureResume.args || {},
      });
      const knownAbsentTargets = knownAbsentFeatureIntentTargetFiles(
        messages,
        resumedTargetFiles,
      );
      const unreadTargets = targetReadCompleted
        ? unreadFeatureIntentTargetFiles(
          { arguments: persistedFeatureResume.args || {} },
          architectureStatus.directSourceFileEvidencePaths,
          knownAbsentTargets,
        )
        : [];
      if (!targetReadCompleted || unreadTargets.length > 0) {
        // Multiple target files may still need the same bounded read handoff.
        // The request cannot reach MCP while unread targets remain: the
        // post-prediction recovery below replaces it with the next exact read.
        restoreExactResume();
      } else {
        // The newly read source may disprove the model's pre-read hypothesis.
        // Drop semantic args completely instead of freezing them across hard
        // compaction, and grant two ordinary bounded discovery observations.
        nextCheckpoint.featureIntentResume = {
          mode: "rediscover_after_target_read",
          observedResultCount: currentFeatureResultCount,
          observedDiscoveryResultCount: currentDiscoveryResultCount,
          maxDiscoveryCalls: 2,
        };
        featureIntentRediscoveryActive = true;
        featureIntentRediscoveryRemaining = 2;
        await appendEventBestEffort(sessionId, {
          type: "feature_intent_post_read_reevaluation_started",
          at: new Date().toISOString(),
          staleSemanticArgumentsDiscarded: true,
          maxDiscoveryCalls: 2,
        });
      }
    } else if (featureResultPending && resumeMode === "rediscover_after_target_read") {
      const used = Math.max(
        0,
        currentDiscoveryResultCount
          - Number(persistedFeatureResume.observedDiscoveryResultCount || 0),
      );
      const maxCalls = Math.max(1, Math.min(2, Number(persistedFeatureResume.maxDiscoveryCalls || 2)));
      if (used < maxCalls) {
        nextCheckpoint.featureIntentResume = persistedFeatureResume;
        featureIntentRediscoveryActive = true;
        featureIntentRediscoveryRemaining = maxCalls - used;
      } else {
        await appendEventBestEffort(sessionId, {
          type: "feature_intent_post_read_reevaluation_completed",
          at: new Date().toISOString(),
          reason: "bounded_discovery_consumed",
          discoveryCallsUsed: used,
        });
      }
    } else if (featureResultPending) {
      // Backward-compatible handling for durable checkpoints written before
      // post-read semantic re-evaluation was introduced.
      restoreExactResume();
    }
  }
  if (
    !serverControlV2Active
    && featureIntentRediscoveryActive
    && toolNamesMatch(
      FEATURE_INTENT_TOOL_NAME,
      String(nextCheckpoint?.requiredNextTool?.name || ""),
    )
  ) {
    nextCheckpoint.requiredNextTool = null;
  }
  if (featureIntentRediscoveryActive) {
    injectFeatureIntentPostReadRule(history, featureIntentRediscoveryRemaining);
  }
  const advertisedRequiredToolName = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
  let advertisedRequiredToolExists = Boolean(advertisedRequiredToolName && toolDefinitions.some((tool: any) => (
    toolNamesMatch(advertisedRequiredToolName, tool?.function?.name || tool?.name || "")
  )));
  const advertisedRequiredToolIsRouted = Boolean(advertisedRequiredToolName && (
    nextCheckpoint?.toolRoute?.activeTools || []
  ).some((name: any) => toolNamesMatch(advertisedRequiredToolName, String(name || ""))));
  let serverControlReadSchemaRecovered = false;
  if (
    serverControlV2Active
    && serverControlV2?.requiredTool
    && !advertisedRequiredToolExists
  ) {
    // LM Studio can retain a stale per-chat tool snapshot across a provider's
    // tools/list update. If the exact server-owned obligation is a read-only
    // direct-source call whose arguments are already fixed, a narrow local
    // serialization schema is sufficient: the MCP provider remains the
    // call-time authority and no model-selected path or mutation is enabled.
    const recoveryDefinition = serverControlledDirectReadDefinition(nextCheckpoint);
    if (recoveryDefinition) {
      toolDefinitions = [...toolDefinitions, recoveryDefinition];
      advertisedRequiredToolExists = true;
      serverControlReadSchemaRecovered = true;
      await appendEventBestEffort(sessionId, {
        type: "server_control_read_schema_recovered",
        at: new Date().toISOString(),
        epoch: Number(serverControlV2.epoch),
        requiredTool: String(serverControlV2.requiredTool.name || ""),
        source: "server_owned_direct_read",
      });
    }
  }
  const invalidRequiredToolContract = Boolean(
    !detachedSideQueryActive
    && !serverControlV2Active
    && advertisedRequiredToolName
    && !advertisedRequiredToolExists
    && !advertisedRequiredToolIsRouted
  );
  const requiredToolSchemaMissing = Boolean(
    !detachedSideQueryActive
    && advertisedRequiredToolName
    && !advertisedRequiredToolExists
    && advertisedRequiredToolIsRouted
  );
  if (
    serverControlV2Active
    && serverControlV2?.requiredTool
    && !advertisedRequiredToolExists
  ) {
    await appendEventBestEffort(sessionId, {
      type: "server_control_schema_missing",
      at: new Date().toISOString(),
      epoch: Number(serverControlV2.epoch),
      requiredTool: String(serverControlV2.requiredTool.name || ""),
    });
  }
  if (invalidRequiredToolContract) {
    // A server-owned exact-tool contract must resolve either to an advertised
    // definition or to the authoritative active route. Older RAG envelopes
    // inferred tool-ness from snake_case prose and could otherwise create an
    // impossible gate such as `read_project_source_or_answer` forever.
    nextCheckpoint.requiredNextTool = null;
    if (
      nextCheckpoint?.protocolControl
      && toolNamesMatch(nextCheckpoint.protocolControl.nextAction || "", advertisedRequiredToolName)
    ) {
      nextCheckpoint.protocolControl.nextActionIsTool = false;
    }
    await appendEventBestEffort(sessionId, {
      type: "invalid_required_tool_contract_cleared",
      at: new Date().toISOString(),
      requiredTool: advertisedRequiredToolName,
      reason: "not_advertised_and_not_in_active_route",
    });
  }
  const featureIntentTool = toolDefinitions.find((tool: any) => toolNamesMatch(
    FEATURE_INTENT_TOOL_NAME,
    tool?.function?.name || tool?.name || "",
  ));
  const featureIntentRouted = Boolean((nextCheckpoint?.toolRoute?.activeTools || []).some(
    (name: any) => toolNamesMatch(FEATURE_INTENT_TOOL_NAME, String(name || "")),
  ));
  const selectedSliceFiles = Array.isArray(nextCheckpoint?.toolRoute?.selectedSlice?.files)
    ? nextCheckpoint.toolRoute.selectedSlice.files.filter((path: any) => String(path || "").trim())
    : [];
  const plannerPhaseActive = String(nextCheckpoint?.toolRoute?.phase || "").toLowerCase() === "planner";
  const targetBoundEvidencePairReady = hasTargetBoundDirectSourcePair(
    architectureStatus.directSourceDeclarationPaths,
    architectureStatus.directSourceImplementationPaths,
  );
  const featureIntentEvidenceReady = Boolean(
    architectureStatus.directSourceFileEvidenceCount >= effectiveFeatureIntentEvidenceReadThreshold
    && architectureStatus.directSourceImplementationCount >= (featureCompletionAuditRequired ? 2 : 1)
    && architectureStatus.directSourceDeclarationCount >= (featureCompletionAuditRequired ? 2 : 1)
    && (!featureCompletionAuditRequired || targetBoundEvidencePairReady)
  );
  const featureFrontierState = latestFeatureFrontierState(messages);
  const featureFrontierRecovery = featureFrontierState.recovery;
  const featureFrontierTerminalRepeated = featureFrontierState.terminalRepeated;
  if (
    !serverControlV2Active
    && featureFrontierTerminalRepeated
    && toolNamesMatch(
      FEATURE_INTENT_TOOL_NAME,
      String(nextCheckpoint?.requiredNextTool?.name || ""),
    )
  ) {
    // A bounded semantic repeat ends the old exact-serialization obligation.
    // Keep the task and planner route alive so a different source candidate can
    // be inspected, but do not immediately force the rejected gate a third time.
    nextCheckpoint.requiredNextTool = null;
    nextCheckpoint.featureIntentResume = null;
  }
  const featureFrontierRecoveryActive = Boolean(
    !serverControlV2Active
    && !detachedSideQueryActive
    && !featureIntentRediscoveryActive
    && plannerPhaseActive
    && featureCompletionAuditRequired
    && featureFrontierRecovery,
  );
  let featureFrontierRepairToolForced = false;
  let featureFrontierSemanticRediscoveryActive = false;
  let featureFrontierSemanticRediscoveryRemaining = 0;
  if (featureFrontierRecoveryActive) {
    const requiredReads = Array.isArray(featureFrontierRecovery?.requiredReads)
      ? featureFrontierRecovery.requiredReads.filter((path: any) => String(path || "").trim())
      : [];
    const semanticDiscoveryRequired = featureFrontierRecovery?.semanticDiscoveryRequired === true;
    const semanticDiscoveryLimit = Math.max(
      1,
      Math.min(2, Number(featureFrontierRecovery?.maxDiscoveryCalls || 2)),
    );
    const semanticDiscoveryUsed = semanticDiscoveryRequired
      ? successfulToolResultCountSinceLatest(
        messages,
        FEATURE_INTENT_TOOL_NAME,
        ARCHITECTURE_DISCOVERY_TOOLS,
      )
      : 0;
    featureFrontierSemanticRediscoveryActive = Boolean(
      semanticDiscoveryRequired
      && requiredReads.length === 0
      && semanticDiscoveryUsed < semanticDiscoveryLimit
    );
    featureFrontierSemanticRediscoveryRemaining = featureFrontierSemanticRediscoveryActive
      ? semanticDiscoveryLimit - semanticDiscoveryUsed
      : 0;
    if (
      featureFrontierSemanticRediscoveryActive
      && toolNamesMatch(
        FEATURE_INTENT_TOOL_NAME,
        String(nextCheckpoint?.requiredNextTool?.name || ""),
      )
    ) {
      // The semantic validator result is newer than an earlier checkpoint's
      // Feature handoff.  Clear only that stale gate requirement while the
      // bounded source re-evaluation is active.
      nextCheckpoint.requiredNextTool = null;
    }
    injectFeatureIntentRecoveryRule(
      history,
      featureFrontierRecovery,
      featureFrontierSemanticRediscoveryRemaining,
    );
    const requiredBeforeFrontierRepair = String(
      nextCheckpoint?.requiredNextTool?.name || "",
    ).trim();
    const frontierRepairMayUseRequired = Boolean(
      !requiredBeforeFrontierRepair
      || toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requiredBeforeFrontierRepair)
    );
    // FEATURE_FRONTIER_UNPROVEN has two distinct recovery shapes.  A non-empty
    // requiredReads list authorizes bounded source discovery.  An empty list is
    // a model-only payload repair and must return directly to Feature Intent;
    // leaving all planner tools exposed lets compaction-triggered re-reads cycle
    // forever without changing the frontier.  Preserve checkpoint/control-plane
    // requirements by taking this ownership only when no different exact tool
    // is already pending.
    if (
      requiredReads.length === 0
      && !featureFrontierSemanticRediscoveryActive
      && featureIntentTool
      && featureIntentRouted
      && frontierRepairMayUseRequired
    ) {
      nextCheckpoint.requiredNextTool = {
        name: FEATURE_INTENT_TOOL_NAME,
        reference: {
          sourceField: "compactor.featureFrontierRepair",
          value: FEATURE_INTENT_TOOL_NAME,
        },
        args: null,
      };
      featureFrontierRepairToolForced = true;
    }
  }
  const featureIntentContractTool = featureIntentSubmissionTool(featureIntentTool, {
    completionAuditRequired: featureCompletionAuditRequired,
    sliceInputRequired: selectedSliceFiles.length === 0,
  });
  const contractAwareToolDefinitions = toolDefinitions.map((tool: any) => (
    toolNamesMatch(FEATURE_INTENT_TOOL_NAME, tool?.function?.name || tool?.name || "")
      ? featureIntentContractTool
      : tool
  ));
  const checkpointRecoveryRequired = toolNamesMatch(
    TASK_CHECKPOINT_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  );
  const evidenceFirstContractTool: any = toolDefinitions.find((tool: any) => toolNamesMatch(
    EVIDENCE_FIRST_CONTRACT_TOOL_NAME,
    tool?.function?.name || tool?.name || "",
  ));
  const evidenceFirstContractSatisfied = successfulToolCalledSinceLatestUser(
    messages,
    EVIDENCE_FIRST_CONTRACT_TOOL_NAME,
  );
  if (
    !serverControlV2Active
    &&
    evidenceFirstContractSatisfied
    && toolNamesMatch(
      EVIDENCE_FIRST_CONTRACT_TOOL_NAME,
      String(nextCheckpoint?.requiredNextTool?.name || ""),
    )
  ) {
    nextCheckpoint.requiredNextTool = null;
  }
  const requiredBeforeEvidenceFirst = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
  const evidenceFirstMayPreemptRequired = Boolean(
    !requiredBeforeEvidenceFirst
    || toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requiredBeforeEvidenceFirst)
  );
  const evidenceFirstContractForced = Boolean(
    !detachedSideQueryActive
    && !serverControlV2Active
    && !workflowStopActive
    && routeOwnershipAvailable
    && plannerPhaseActive
    && featureIntentTool
    && featureIntentRouted
    && selectedSliceFiles.length === 0
    && !architectureValidationRequired
    && !checkpointRecoveryRequired
    && evidenceFirstMayPreemptRequired
    && evidenceFirstContractTool
    && !evidenceFirstContractSatisfied
  );
  if (evidenceFirstContractForced) {
    nextCheckpoint.requiredNextTool = {
      name: EVIDENCE_FIRST_CONTRACT_TOOL_NAME,
      reference: { sourceField: "compactor.evidenceFirstContract", value: EVIDENCE_FIRST_CONTRACT_TOOL_NAME },
      args: { mode: "codegen" },
    };
    injectEvidenceFirstContractRule(history);
  }
  const evidenceFirstContractReady = Boolean(
    !evidenceFirstContractTool || evidenceFirstContractSatisfied,
  );
  const requiredBeforeEvidenceRefill = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
  const evidenceRefillMaySuspendRequired = Boolean(
    !requiredBeforeEvidenceRefill
    || toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requiredBeforeEvidenceRefill)
  );
  const featureIntentEvidenceRefillActive = Boolean(
    !detachedSideQueryActive
    && !serverControlV2Active
    && !workflowStopActive
    && !evidenceFirstContractForced
    && evidenceFirstContractReady
    && routeOwnershipAvailable
    && featureIntentTool
    && featureIntentRouted
    && plannerPhaseActive
    && selectedSliceFiles.length === 0
    && !architectureValidationRequired
    && !checkpointRecoveryRequired
    && evidenceRefillMaySuspendRequired
    && !featureFrontierRecoveryActive
    && !featureIntentEvidenceReady
  );
  if (featureIntentEvidenceRefillActive) {
    nextCheckpoint.requiredNextTool = null;
    injectFeatureIntentEvidenceRefillRule(
      history,
      effectiveFeatureIntentEvidenceReadThreshold,
      architectureStatus.directSourceDeclarationCount,
      architectureStatus.directSourceImplementationCount,
    );
  }
  const requiredBeforeFeatureHandoff = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
  const featureHandoffMayUseRequired = Boolean(
    !requiredBeforeFeatureHandoff
    || toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requiredBeforeFeatureHandoff)
  );
  // An allowed schema is not a required semantic transition. Only Python's
  // authoritative v2 requiredTool may select/force a task-bound gate.
  const serverV2FeatureIntentHandoffAllowed = false;
  const featureIntentDiscoveryHandoffForced = Boolean(
    !detachedSideQueryActive
    && (!serverControlV2Active || serverV2FeatureIntentHandoffAllowed)
    && !workflowStopActive
    && !evidenceFirstContractForced
    && !featureIntentEvidenceRefillActive
    && (evidenceFirstContractReady || serverV2FeatureIntentHandoffAllowed)
    && routeOwnershipAvailable
    && featureIntentTool
    && featureIntentRouted
    && plannerPhaseActive
    && selectedSliceFiles.length === 0
    && !architectureValidationRequired
    && !checkpointRecoveryRequired
    && featureHandoffMayUseRequired
    && !featureFrontierRecoveryActive
    && !featureIntentRediscoveryActive
    && !featureFrontierTerminalRepeated
    && featureIntentEvidenceReady
  );
  if (featureIntentDiscoveryHandoffForced) {
    const priorFeatureArgs = toolNamesMatch(
      FEATURE_INTENT_TOOL_NAME,
      String(nextCheckpoint?.requiredNextTool?.name || ""),
    ) && nextCheckpoint?.requiredNextTool?.args
      && typeof nextCheckpoint.requiredNextTool.args === "object"
      && !Array.isArray(nextCheckpoint.requiredNextTool.args)
      ? nextCheckpoint.requiredNextTool.args
      : null;
    nextCheckpoint.requiredNextTool = {
      name: FEATURE_INTENT_TOOL_NAME,
      reference: { sourceField: "compactor.boundedEvidenceHandoff", value: FEATURE_INTENT_TOOL_NAME },
      args: priorFeatureArgs,
    };
    injectFeatureIntentAtomicRule(history, architectureStatus.directSourceFileEvidencePaths);
  }
  const architectureContractGoal = architectureContractGoalText(
    messages,
    authoritativeGoal,
    architectureStatus.attempted && architectureStatus.lastValidationFailed,
  );
  const stagedContractRequired = Boolean(
    architectureStatus.stagedContractRequired
    || stagedArchitectureContractRequired(architectureContractGoal),
  );
  const networkedContractRequired = Boolean(
    architectureStatus.networkedContractRequired
    || networkedArchitectureContractRequired(architectureContractGoal),
  );
  if (nextCheckpoint?.architectureProposal) {
    nextCheckpoint.architectureProposal.stagedContractRequired = stagedContractRequired;
    nextCheckpoint.architectureProposal.networkedContractRequired = networkedContractRequired;
  }
  const architectureTool = toolDefinitions.find((tool: any) => {
    const name = tool?.function?.name || tool?.name || "";
    return toolNamesMatch(ARCHITECTURE_TOOL_NAME, name);
  });
  const requiredArchitectureTool = toolNamesMatch(
    ARCHITECTURE_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  );
  const initialArchitectureEvidenceReady = Boolean(
    architectureStatus.directEvidenceCount >= architectureEvidenceReadThreshold
    && (
      architectureStatus.implementationEvidenceCount > 0
      || architectureStatus.directEvidenceCount >= architectureEvidenceHardLimit
    )
  );
  const architectureEvidenceRefillActive = Boolean(
    !serverControlV2Active
    && architectureValidationRequired
    && architectureTool
    && architectureStatus.attempted
    && architectureStatus.lastValidationFailed
    && (
      architectureStatus.requiresFullProposal
      || architectureStatus.lastRepairStrategy === "evidence_refill"
    )
    && architectureStatus.lastErrorCode !== "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
    && architectureReplanEvidenceReadBudget > 0
    && architectureStatus.discoveryCallsSinceLastAttempt < architectureReplanEvidenceReadBudget
    && !requiredArchitectureTool
  );
  const architectureToolForced = Boolean(
    !serverControlV2Active
    && architectureValidationRequired
    && !architectureStatus.validated
    && architectureTool
    && (
      (!architectureStatus.attempted && initialArchitectureEvidenceReady)
      || (architectureStatus.attempted && !architectureEvidenceRefillActive)
      || requiredArchitectureTool
    )
  );
  if (architectureToolForced) {
    injectArchitectureSubmissionRule(history);
    if (architectureStatus.lastErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED") {
      injectArchitectureCoreChangeRule(history, architectureStatus.unchangedCorePaths);
    }
  }
  const initialActiveProjectBootstrapForced = Boolean(
    !serverControlV2Active
    && !workflowStopActive
    && !routeOwnershipAvailable
    && activeProjectBootstrapTool
    && plannerTool
    && !trailingMetaUser
    && taskRoutePlanningRequired
    && !activeProjectResolved
    && (
      sourceAnalysisAdmissionRequired
      || (
        architectureStatus.discoveryCallsSinceLastAttempt === 0
        && !architectureStatus.attempted
      )
    )
  );
  if (initialActiveProjectBootstrapForced) {
    injectInitialActiveProjectBootstrapRule(
      history,
      String(activeProjectBootstrapTool?.function?.name || activeProjectBootstrapTool?.name || "unreal_get_active_project"),
    );
  }
  const preRoutePlannerForced = Boolean(
    !serverControlV2Active
    && !workflowStopActive
    && !routeOwnershipAvailable
    && plannerTool
    && !trailingMetaUser
    && taskRoutePlanningRequired
    && (
      architectureStatus.validated
      || (sourceAnalysisAdmissionRequired && activeProjectResolved)
      || (
        !architectureValidationRequired
        && (
          durableInspectionPlanningRequired
            ? activeProjectResolved
            : architectureStatus.discoveryCallsSinceLastAttempt >= preRouteDiscoveryLimit
        )
      )
    )
    && !initialActiveProjectBootstrapForced
  );
  if (preRoutePlannerForced) injectPreRoutePlannerHandoffRule(history);
  const sourceAnalysisAdmissionBlocked = Boolean(
    sourceAnalysisAdmissionRequired
    && !initialActiveProjectBootstrapForced
    && !preRoutePlannerForced
  );
  if (sourceAnalysisAdmissionBlocked) {
    await appendEventBestEffort(sessionId, {
      type: "source_analysis_admission_blocked",
      at: isoNow(),
      category: sourceWorkIntent.category,
      plannerAvailable,
      activeProjectResolved,
      requiredTransition: activeProjectResolved
        ? TASK_PLANNER_TOOL_NAME
        : "unreal_get_active_project",
    });
    throw new Error(
      activeProjectResolved
        ? "Durable source analysis is blocked because unreal_agent_plan is unavailable. Source tools remain hidden."
        : "Durable source analysis is blocked because the server-owned active-project bootstrap and planner route are unavailable. Source tools remain hidden.",
    );
  }
  const catalogRefreshPhaseEligible = Boolean(
    !workflowStopActive
    && !architectureToolForced
    && !architectureEvidenceRefillActive
    && !initialActiveProjectBootstrapForced
    && !preRoutePlannerForced
  );
  catalogRefreshForced = Boolean(
    catalogRefreshNeeded
    && catalogRefreshPhaseEligible
    && catalogRefreshTool
    && Number(priorCatalogRefresh?.attempts || 0) < 1
  );
  catalogRefreshBlocked = Boolean(
    catalogRefreshNeeded
    && catalogRefreshPhaseEligible
    && !catalogRefreshForced
  );
  if (catalogRefreshForced) {
    nextCheckpoint.catalogRefresh = {
      routeHash,
      attempts: 1,
      status: "requested",
      tool: String(catalogRefreshTool?.function?.name || catalogRefreshTool?.name || ""),
      requestedAt: new Date().toISOString(),
    };
    injectToolCatalogRefreshRule(history, nextCheckpoint.catalogRefresh.tool);
  } else if (catalogRefreshBlocked) {
    nextCheckpoint.catalogRefresh = {
      routeHash,
      attempts: Number(priorCatalogRefresh?.attempts || 0),
      status: "failed",
      tool: String(priorCatalogRefresh?.tool || ""),
      requestedAt: String(priorCatalogRefresh?.requestedAt || ""),
    };
  } else if (routeOwnershipAvailable && exactToolRouteAvailable && rawMutationDefinitionsPresent) {
    nextCheckpoint.catalogRefresh = {
      routeHash,
      attempts: Number(priorCatalogRefresh?.attempts || 0),
      status: "synchronized",
      tool: String(priorCatalogRefresh?.tool || ""),
      requestedAt: String(priorCatalogRefresh?.requestedAt || ""),
    };
  } else if (!catalogRefreshNeeded) {
    nextCheckpoint.catalogRefresh = null;
  }
  const exactRequiredToolName = String(nextCheckpoint?.requiredNextTool?.name || "").trim();
  const unchangedControlTools = repeatedUnchangedControlTools(messages).filter(
    (name) => !toolNamesMatch(name, exactRequiredToolName),
  );
  injectRepeatedControlBoundaryRule(history, unchangedControlTools);
  const localConversationMaySuppressRequiredTool = Boolean(
    !serverControlV2Active
    && (detachedSideQueryActive || trailingMetaUser)
  );
  const exactRequiredToolDefinition: any = localConversationMaySuppressRequiredTool
    ? null
    : taskOwnedRequiredToolDefinition(nextCheckpoint, contractAwareToolDefinitions, sessionId);
  const initialActiveProjectControlDefinition: any = initialActiveProjectBootstrapForced
    ? phaseControlToolDefinition(activeProjectBootstrapTool, sessionId, authoritativeGoal, resolvedTargetModel)
    : null;
  const preRoutePlannerControlDefinition: any = preRoutePlannerForced
    ? phaseControlToolDefinition(plannerTool, sessionId, authoritativeGoal, resolvedTargetModel)
    : null;
  const catalogRefreshControlDefinition: any = catalogRefreshForced
    ? phaseControlToolDefinition(catalogRefreshTool, sessionId, authoritativeGoal, resolvedTargetModel)
    : null;
  const userInputResumeTool: any = userInputResumeActive
    ? toolDefinitions.find((tool: any) => toolNamesMatch(
      "unreal_task_resume",
      tool?.function?.name || tool?.name || "",
    ))
    : null;
  const userInputResumeDefinition: any = userInputResumeActive && userInputResumeTool
    ? deterministicControlToolDefinition(userInputResumeTool, {
      taskSessionId: String(serverControlV2?.taskSessionId || ""),
      taskAuthorization: userInputResumeOwnership,
      userResponse: architectureGoal,
      resumeToken: String(serverControlV2?.requiredUserInput?.resumeToken || ""),
    })
    : null;
  const exactRequiredToolForced = Boolean(
    exactRequiredToolDefinition
    && (
      (serverControlV2Active && Boolean(serverControlV2?.requiredTool))
      || (
        !workflowStopActive
        && !architectureToolForced
        && !architectureEvidenceRefillActive
        && !featureIntentEvidenceRefillActive
        && !initialActiveProjectBootstrapForced
        && !preRoutePlannerForced
        && !catalogRefreshForced
      )
    )
  );
  if (exactRequiredToolForced) {
    injectServerRequiredToolRule(
      history,
      exactRequiredToolName,
      nextCheckpoint?.requiredNextTool?.args,
    );
  }
  const serverV2FeatureIntentToolForced = Boolean(
    serverV2FeatureIntentHandoffAllowed
    && featureIntentDiscoveryHandoffForced
    && exactRequiredToolForced
    && toolNamesMatch(FEATURE_INTENT_TOOL_NAME, exactRequiredToolName)
  );
  const requireCompleteArchitectureProposal = Boolean(
    (architectureToolForced || architectureEvidenceRefillActive)
    && (!nextCheckpoint?.architectureProposal || architectureStatus.requiresFullProposal),
  );
  const architectureContractTool = architectureSubmissionTool(
    architectureTool,
    requireCompleteArchitectureProposal,
    {
      stagedContract: stagedContractRequired,
      networkedContract: networkedContractRequired,
    },
  );
  // A legacy semantic recovery result can arrive after a v2 route was
  // checkpointed.  V2 narrows authority, but it must never re-expose a tool
  // explicitly forbidden by that newer recovery result.  Core normally turns
  // a contradictory route into a terminal checkpoint; retain this intersection
  // here as a second, generator-side barrier for mixed-version histories.
  const semanticForbiddenTools = Array.isArray(nextCheckpoint?.semanticBlocker?.forbiddenTools)
    ? nextCheckpoint.semanticBlocker.forbiddenTools.map((name: any) => String(name || "").trim()).filter(Boolean)
    : [];
  const semanticForbiddenCallFingerprints = Array.isArray(
    nextCheckpoint?.semanticBlocker?.forbiddenCallFingerprints,
  )
    ? nextCheckpoint.semanticBlocker.forbiddenCallFingerprints
      .map((fingerprint: any) => String(fingerprint || "").trim().toLowerCase())
      .filter((fingerprint: string) => /^[a-f0-9]{64}$/.test(fingerprint))
    : [];
  const exactSemanticCallBlockingActive = semanticForbiddenCallFingerprints.length > 0;
  const toolAllowedBySemanticBlocker = (tool: any): boolean => {
    const name = String(tool?.function?.name || tool?.name || "").trim();
    // Versioned blockers carry exact call fingerprints. Keeping the schema
    // visible allows a corrected argument set while rejecting only the failed
    // semantic call below; a whole tool-family deny would recreate the recovery
    // deadlock the fingerprint was designed to avoid.
    if (exactSemanticCallBlockingActive) return true;
    return !semanticForbiddenTools.some((forbidden: string) => toolNamesMatch(forbidden, name));
  };
  const serverAllowedTools = Array.isArray(serverControlV2?.allowedTools)
    ? serverControlV2.allowedTools.map((name: any) => String(name || "").trim()).filter(Boolean)
    : [];
  const serverProjectedToolDefinitions = toolDefinitions.filter((tool: any) => (
    serverAllowedTools.some((allowed: string) => toolNamesMatch(
      allowed,
      tool?.function?.name || tool?.name || "",
    ))
  )).filter(toolAllowedBySemanticBlocker);
  const phaseToolDefinitions = serverControlV2Active
    ? (userInputResumeDefinition
      ? [userInputResumeDefinition].filter(toolAllowedBySemanticBlocker)
      : serverV2FeatureIntentToolForced
      ? [exactRequiredToolDefinition].filter(Boolean).filter(toolAllowedBySemanticBlocker)
      : serverProjectedToolDefinitions)
    : workflowStopActive
    ? []
    : detachedSideQueryActive
    ? toolDefinitions.filter((tool: any) => detachedSideQueryToolAllowed(
      tool?.function?.name || tool?.name || "",
    ))
    : (architectureToolForced
    ? [architectureContractTool].filter(toolAllowedBySemanticBlocker)
    : (initialActiveProjectBootstrapForced
      ? [initialActiveProjectControlDefinition].filter(Boolean).filter(toolAllowedBySemanticBlocker)
    : (preRoutePlannerForced
      ? [preRoutePlannerControlDefinition].filter(Boolean).filter(toolAllowedBySemanticBlocker)
      : (catalogRefreshForced
        ? [catalogRefreshControlDefinition].filter(Boolean).filter(toolAllowedBySemanticBlocker)
      : (exactRequiredToolForced
        ? [exactRequiredToolDefinition].filter(toolAllowedBySemanticBlocker)
      : (featureIntentEvidenceRefillActive
      ? toolDefinitions
        .filter((tool: any) => ARCHITECTURE_DISCOVERY_TOOLS.some((name) => toolNamesMatch(
          name,
          tool?.function?.name || tool?.name || "",
        )))
        .filter(toolAllowedBySemanticBlocker)
      : (architectureEvidenceRefillActive
      ? toolDefinitions
        .filter((tool: any) => architectureDiscoveryToolAllowed(
          tool?.function?.name || tool?.name || "",
        ))
        .map((tool: any) => (
          toolNamesMatch(ARCHITECTURE_TOOL_NAME, tool?.function?.name || tool?.name || "")
            ? architectureContractTool
            : tool
        ))
        .filter(toolAllowedBySemanticBlocker)
      : toolDefinitions.filter(toolAllowedBySemanticBlocker))))))));
  // Exact catalog/state enforcement: a generated write is intent, not proof of
  // ownership. Remove mutation schemas until a server result has supplied the
  // compact taskSessionId + ownerCapability pair. This also protects older
  // agent MCP revisions that advertised write tools on a clean startup.
  const checkpointExplicitlyRequired = toolNamesMatch(
    TASK_CHECKPOINT_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  );
  // Exact phase filtering also closes the tools/list refresh race between the
  // two Unreal MCP providers. Architecture recovery already constructs its
  // own narrower catalog, so it remains authoritative while active.
  const routeSafePhaseToolDefinitions = serverControlV2Active
    ? phaseToolDefinitions
    : routeOwnershipAvailable
    ? phaseToolDefinitions.filter((tool: any) => activeRouteBootstrapToolAllowed(tool, nextCheckpoint))
    : phaseToolDefinitions;
  const nonDurableSafePhaseToolDefinitions = nonDurablePlannerSuppressionRequired
    ? routeSafePhaseToolDefinitions.filter((tool: any) => !toolNamesMatch(
      TASK_PLANNER_TOOL_NAME,
      tool?.function?.name || tool?.name || "",
    ))
    : routeSafePhaseToolDefinitions;
  const contractAwareRouteSafePhaseToolDefinitions = nonDurableSafePhaseToolDefinitions.map((tool: any) => (
    !tool?.__serverOwnedInjectedArgs
    && toolNamesMatch(FEATURE_INTENT_TOOL_NAME, tool?.function?.name || tool?.name || "")
      ? featureIntentContractTool
      : tool
  ));
  const routedToolDefinitions = serverControlV2Active
    ? contractAwareRouteSafePhaseToolDefinitions
    : detachedSideQueryActive
    ? contractAwareRouteSafePhaseToolDefinitions
    : routeOwnershipAvailable
    && exactToolRouteAvailable
    && !architectureToolForced
    && !architectureEvidenceRefillActive
    && !featureIntentEvidenceRefillActive
    ? contractAwareRouteSafePhaseToolDefinitions.filter((tool: any) => routeAllowsTool(tool, nextCheckpoint))
    : (checkpointExplicitlyRequired
      ? contractAwareRouteSafePhaseToolDefinitions
      : contractAwareRouteSafePhaseToolDefinitions.filter((tool: any) => !toolNamesMatch(
        TASK_CHECKPOINT_TOOL_NAME,
        tool?.function?.name || tool?.name || "",
      )));
  const boundedToolDefinitions = serverControlV2Active
    ? routedToolDefinitions
    : routedToolDefinitions.filter((tool: any) => {
    const name = String(tool?.function?.name || tool?.name || "");
    return !unchangedControlTools.some((blocked) => toolNamesMatch(blocked, name));
  });
  const effectiveToolDefinitions = detachedSideQueryActive
    ? boundedToolDefinitions
    : routeOwnershipAvailable
    ? boundedToolDefinitions
    : boundedToolDefinitions.filter((tool: any) => !core.mutationToolName(
      tool?.function?.name || tool?.name || "",
    ));
  let modelFacingToolDefinitions = effectiveToolDefinitions.filter((tool: any) => {
    const name = String(tool?.function?.name || tool?.name || "");
    return ![
      "unreal_task_commit_synthesis",
      "unreal_task_ack_synthesis_delivery",
      "unreal_task_recover_synthesis_delivery",
    ].some((internalName) => toolNamesMatch(internalName, name));
  }).map((tool: any) => {
    if (!tool?.__serverOwnedInjectedArgs) return tool;
    const {
      __serverOwnedInjectedArgs: _injected,
      __serverOwnedDirectCallSafe: _directCallSafe,
      ...publicDefinition
    } = tool;
    return publicDefinition;
  });
  const featureIntentModelFacingTool: any = modelFacingToolDefinitions.find((tool: any) => (
    toolNamesMatch(FEATURE_INTENT_TOOL_NAME, tool?.function?.name || tool?.name || "")
  )) || featureIntentContractTool;
  const exactServerOwnedDirectCall = Boolean(
    exactRequiredToolForced
    && serverControlV2Active
    && serverControlV2?.requiredTool
    && exactRequiredToolDefinition?.__serverOwnedDirectCallSafe === true,
  );
  const phaseControlDefinition: any = userInputResumeDefinition
    ? userInputResumeDefinition
    : initialActiveProjectBootstrapForced
    ? initialActiveProjectControlDefinition
    : preRoutePlannerForced
      ? preRoutePlannerControlDefinition
      : catalogRefreshForced
        ? catalogRefreshControlDefinition
        : null;
  const phaseControlKind = userInputResumeDefinition
    ? "user_input_resume"
    : initialActiveProjectBootstrapForced
    ? "initial_active_project"
    : preRoutePlannerForced
      ? "pre_route_planner"
      : catalogRefreshForced
        ? "catalog_refresh"
        : "";
  const phaseServerOwnedDirectCall = Boolean(
    phaseControlDefinition?.__serverOwnedDirectCallSafe === true,
  );
  // These requests are already fully determined and authorized by server state.
  // Prompt projection cannot change them, so do not let an oversized prompt or
  // tool catalog block their direct, control-plane-validated dispatch.
  const serverOwnedDirectControlPending = Boolean(
    exactServerOwnedDirectCall || phaseServerOwnedDirectCall,
  );
  const initialSetupStartedAt = performance.now();
  const initialSetupWallClockMs = finiteNumber(
    configValue(ctl, "predictionWallClockSeconds", 180), 180, 5, 1800,
  ) * 1000;
  const initialSetupDeadlineAt = initialSetupStartedAt + initialSetupWallClockMs;
  const remainingInitialSetupMs = () => {
    const now = performance.now();
    const remainingMs = initialSetupDeadlineAt - now;
    if (remainingMs <= 0) {
      throw predictionSupervisorError(
        "PREDICTION_WALL_CLOCK_EXCEEDED",
        now - initialSetupStartedAt,
      );
    }
    return remainingMs;
  };
  let currentFormatted: any;
  let inputTokens: number;
  let toolSchemaTokens: number;
  if (serverOwnedDirectControlPending) {
    currentFormatted = null;
    inputTokens = 0;
    toolSchemaTokens = 0;
  } else try {
    currentFormatted = await boundedPredictionSetupValue(
      ctl,
      remainingInitialSetupMs(),
      initialSetupStartedAt,
      () => model.applyPromptTemplate(history),
    );
    inputTokens = Math.max(0, Number(await boundedPredictionSetupValue(
      ctl,
      remainingInitialSetupMs(),
      initialSetupStartedAt,
      () => model.countTokens(currentFormatted),
    )) || 0);
    toolSchemaTokens = Math.max(0, Number(await boundedPredictionSetupValue(
      ctl,
      remainingInitialSetupMs(),
      initialSetupStartedAt,
      () => model.countTokens(JSON.stringify(modelFacingToolDefinitions)),
    )) || 0);
  } catch (error) {
    await persistPredictionSetupFailure(error, "initial_prompt_measurement");
    throw error;
  }
  const persistedNextToolName = detachedSideQueryActive
    ? ""
    : (nextCheckpoint?.requiredNextTool?.name || "");
  const nextToolName = core.isNonToolNextAction(persistedNextToolName) ? "" : persistedNextToolName;
  const hardRemainingTokens = finiteNumber(configValue(ctl, "hardRemainingTokens", 8000), 8000);
  const configuredOutputReserve = finiteNumber(
    configValue(ctl, "maxOutputReserve", 4096), 4096, 1,
  );
  const architectureOutputReserve = finiteNumber(
    configValue(ctl, "architectureMaxOutputReserve", 6144), 6144,
    Math.max(6144, configuredOutputReserve),
  );
  const synthesisOutputReserve = finiteNumber(
    configValue(ctl, "synthesisMaxOutputReserve", 3072), 3072,
    512,
  );
  const toolCallOutputReserve = finiteNumber(
    configValue(ctl, "toolCallMaxOutputReserve", 6144), 6144,
    Math.max(6144, configuredOutputReserve),
  );
  const synthesisFinal = String(nextCheckpoint?.serverControl?.phase || "").trim().toLowerCase() === "synthesis";
  const dynamicOutputReserve = architectureValidationRequired
    ? Math.max(architectureOutputReserve, toolCallOutputReserve)
    : synthesisFinal
      ? synthesisOutputReserve
      : toolCallOutputReserve;
  const currentTurnCapConfig = resolveCurrentTurnCapConfig(ctl);
  const config = {
    enabled,
    observeOnly,
    strictToolControlPlane: Boolean(configValue(ctl, "strictToolControlPlane", false)),
    bufferUntilPredictionComplete: Boolean(configValue(ctl, "bufferUntilPredictionComplete", true)),
    streamReasoningProgress: Boolean(configValue(ctl, "streamReasoningProgress", true)),
    predictionHeartbeatSeconds: finiteNumber(
      configValue(ctl, "predictionHeartbeatSeconds", 4), 4, 1, 30,
    ),
    predictionWallClockSeconds: finiteNumber(
      configValue(ctl, "predictionWallClockSeconds", 180), 180, 5, 1800,
    ),
    predictionNoProgressSeconds: finiteNumber(
      configValue(ctl, "predictionNoProgressSeconds", 45), 45, 0.01, 300,
    ),
    rejectTruncatedPredictions: Boolean(configValue(ctl, "rejectTruncatedPredictions", true)),
    requireCheckpointPersistence,
    softRemainingTokens: finiteNumber(configValue(ctl, "softRemainingTokens", 14000), 14000, hardRemainingTokens),
    hardRemainingTokens,
    maxOutputReserve: dynamicOutputReserve,
    safetyMarginTokens: finiteNumber(configValue(ctl, "safetyMarginTokens", 1024), 1024),
    temperature: finiteNumber(configValue(ctl, "temperature", 0.1), 0.1, 0, 1),
    topPSampling: finiteNumber(configValue(ctl, "topPSampling", 0.85), 0.85, 0, 1),
    topKSampling: Math.floor(finiteNumber(configValue(ctl, "topKSampling", 20), 20, 1, 1000)),
    minPSampling: finiteNumber(configValue(ctl, "minPSampling", 0), 0, 0, 1),
    reasoningEffort: normalizeReasoningEffort(configValue(ctl, "reasoningEffort", "low")),
    normalToolResultReserve: finiteNumber(configValue(ctl, "normalToolResultReserve", 3000), 3000),
    buildToolResultReserve: finiteNumber(configValue(ctl, "buildToolResultReserve", 8000), 8000),
    recentCompleteTurns: Math.floor(finiteNumber(configValue(ctl, "recentCompleteTurns", 1), 1, 0, 100)),
    maxCurrentTurnMessages: currentTurnCapConfig.effectiveValue,
    minimumTurnsBetweenCompactions: Math.floor(finiteNumber(configValue(ctl, "minimumTurnsBetweenCompactions", 0), 0, 0, 100)),
    targetRemainingTokensAfterCompaction: finiteNumber(
      configValue(ctl, "targetRemainingTokensAfterCompaction", 24000), 24000, hardRemainingTokens,
    ),
    architectureEvidenceReadThreshold,
    architectureEvidenceHardLimit,
    architectureReplanEvidenceReadBudget,
    preRouteDiscoveryLimit,
    durableInspectionDiscoveryLimit,
  };
  const predictionPhase = resolvedPredictionPhase(
    nextCheckpoint,
    architectureValidationRequired,
  );
  const thinkingEnabled = new Set(["plan", "critique", "compile_fix_analyze"])
    .has(predictionPhase);
  if (predictionPhase === "synthesis_final") {
    config.maxOutputReserve = Math.min(config.maxOutputReserve, 4096);
  }
  const synthesisBudgetCompatibility = predictionPhase === "synthesis_final"
    ? {
      configuredTokens: synthesisOutputReserve,
      effectiveTokens: null,
      calculationStage: "attempt_prompt",
      compatible: null,
    }
    : null;
  const effectiveReasoningEffort = thinkingEnabled ? config.reasoningEffort : null;
  const reasoningRawConfig = qwen38ReasoningRawConfig(
    resolvedTargetModel,
    thinkingEnabled,
    effectiveReasoningEffort,
  );
  const predictionPolicy = {
    version: 2,
    policyVersion: "qwen-reasoning-runtime-v2",
    modelIdentifier: transactionModelFence.identifier || resolvedTargetModel,
    modelInstanceReference: transactionModelFence.instanceReference,
    modelLoadConfigHash: null,
    modelLoadConfigObservable: false,
    samplingProfile: isQwen38_27b(resolvedTargetModel) ? "qwen3_8_27b" : "generic",
    phase: predictionPhase,
    sampling: {
      temperature: config.temperature,
      topPSampling: config.topPSampling,
      topKSampling: config.topKSampling,
      minPSampling: config.minPSampling,
    },
    budgets: {
      maxOutputTokens: config.maxOutputReserve,
      reasoningTokens: null,
      finalAnswerReserveTokens: modelFacingToolDefinitions.length === 0
        ? config.maxOutputReserve
        : configuredOutputReserve,
      toolCallReserveTokens: toolCallOutputReserve,
      separateReasoningBudgetSupported: false,
      wallClockSeconds: config.predictionWallClockSeconds,
      noProgressSeconds: config.predictionNoProgressSeconds,
      synthesisCompatibility: synthesisBudgetCompatibility,
    },
    currentTurnCap: currentTurnCapConfig,
    reasoningControl: {
      transport: reasoningRawConfig ? "generator_raw_kv_config" : "unsupported_model",
      sdkVersion: "1.5.0",
      effort: reasoningRawConfig ? effectiveReasoningEffort : null,
      enableThinking: Boolean(reasoningRawConfig && thinkingEnabled),
      policyRequested: Boolean(reasoningRawConfig),
      transportConfigured: Boolean(reasoningRawConfig),
      backendApplied: "unknown",
      backendObserved: false,
      failClosedIfUnpinned: Boolean(reasoningRawConfig),
      runtimePinned: Boolean(reasoningRawConfig && transactionModelFence.instanceReference),
      externalLoadConfigStatus: "not_observable_by_public_sdk",
      perPredictionEffortObservable: false,
    },
  };
  const predictionPolicyHash = core.sha256(core.stableStringify(predictionPolicy));
  nextCheckpoint.predictionPolicy = { ...predictionPolicy, fingerprint: predictionPolicyHash };
  const initialToolProjection = {
    count: modelFacingToolDefinitions.length,
    schemaHash: core.sha256(core.stableStringify(modelFacingToolDefinitions)),
    schemaTokens: toolSchemaTokens,
  };
  let decision = core.budgetDecision({ contextLength, inputTokens, nextToolName, config, toolSchemaTokens });
  if (!serverOwnedDirectControlPending
    && decision.action === "hard_compact" && modelFacingToolDefinitions.length > 1) {
    const hardProjectionPriority = [
      String(exactRequiredToolName || ""),
      "search_files",
      "read_file_range",
      "read_file",
      "list_directory",
    ].filter(Boolean);
    const selected: any[] = [];
    for (const desiredName of hardProjectionPriority) {
      const match = modelFacingToolDefinitions.find((tool: any) => toolNamesMatch(
        desiredName,
        tool?.function?.name || tool?.name || "",
      ));
      if (match && !selected.includes(match)) selected.push(match);
      if (exactRequiredToolName || selected.length >= 3) break;
    }
    if (selected.length === 0 && modelFacingToolDefinitions.length > 0) {
      selected.push(modelFacingToolDefinitions[0]);
    }
    selected.sort((left, right) => (
      modelFacingToolDefinitions.indexOf(left) - modelFacingToolDefinitions.indexOf(right)
    ));
    modelFacingToolDefinitions = selected;
    toolSchemaTokens = Math.max(0, Number(await boundedPredictionSetupValue(
      ctl,
      remainingInitialSetupMs(),
      initialSetupStartedAt,
      () => model.countTokens(JSON.stringify(modelFacingToolDefinitions)),
    )) || 0);
    decision = core.budgetDecision({ contextLength, inputTokens, nextToolName, config, toolSchemaTokens });
  }
  const finalToolProjection = {
    count: modelFacingToolDefinitions.length,
    schemaHash: core.sha256(core.stableStringify(modelFacingToolDefinitions)),
    schemaTokens: toolSchemaTokens,
  };
  const currentTurnMessageCount = measureCurrentTurnLength(history);
  const currentTurnCapForced = Boolean(
    !serverOwnedDirectControlPending
    &&
    config.maxCurrentTurnMessages > 0
    && currentTurnMessageCount > config.maxCurrentTurnMessages
  );

  console.info(
    `[unreal-context-compactor] Proxy active: target=${resolvedTargetModel} `
    + `historyPromptTokens=${inputTokens} toolSchemaTokens=${toolSchemaTokens} `
    + `totalEstimatedPromptTokens=${inputTokens + toolSchemaTokens} `
    + `actualBackendPromptTokens=unknown context=${contextLength} action=${decision.action}`,
  );

  const checkpointProjectionTokens = Number(
    core.buildModelFacingCheckpointProjection(nextCheckpoint, predictionPhase)
      ?.metrics?.tokenEstimate || 0,
  );
  await appendEventBestEffort(sessionId, {
    type: "context_measurement",
    at: new Date().toISOString(),
    proxyActive: true,
    targetModel: resolvedTargetModel,
    modelFence: transactionModelFence,
    predictionPolicy: nextCheckpoint.predictionPolicy,
    inputTokens,
    historyPromptTokens: inputTokens,
    toolSchemaTokens,
    checkpointProjectionTokens,
    systemContractTokens: null,
    totalEstimatedPromptTokens: inputTokens + toolSchemaTokens,
    actualBackendPromptTokens: null,
    contextLength,
    decision,
    promptMeasurementBypassedForExactServerControl: serverOwnedDirectControlPending,
    toolProjection: {
      phase: predictionPhase,
      action: decision.action,
      before: initialToolProjection,
      after: finalToolProjection,
      reduced: finalToolProjection.count < initialToolProjection.count,
    },
    workingDirectory,
    architectureValidationRequired,
    serverOwnedArchitectureControl,
    architectureToolForced,
    architectureEvidenceRefillActive,
    architectureAttempted: architectureStatus.attempted,
    architectureValidated: architectureStatus.validated,
    architectureDirectEvidenceCount: architectureStatus.directEvidenceCount,
    architectureDeclarationEvidenceCount: architectureStatus.declarationEvidenceCount,
    architectureImplementationEvidenceCount: architectureStatus.implementationEvidenceCount,
    directSourceFileEvidenceCount: architectureStatus.directSourceFileEvidenceCount,
    directSourceDeclarationCount: architectureStatus.directSourceDeclarationCount,
    directSourceImplementationCount: architectureStatus.directSourceImplementationCount,
    architectureEvidenceCallsSinceLastAttempt: architectureStatus.evidenceCallsSinceLastAttempt,
    architectureDiscoveryCallsSinceLastAttempt: architectureStatus.discoveryCallsSinceLastAttempt,
    architectureUniqueEvidenceSinceLastAttempt: architectureStatus.uniqueEvidenceSinceLastAttempt,
    architectureLastRepairStrategy: architectureStatus.lastRepairStrategy,
    architectureLastRepairMode: architectureStatus.lastRepairMode,
    architectureLastErrorCode: architectureStatus.lastErrorCode,
    architectureUnchangedCorePaths: architectureStatus.unchangedCorePaths,
    architectureStagedContractRequired: stagedContractRequired,
    architectureNetworkedContractRequired: networkedContractRequired,
    requireCompleteArchitectureProposal,
    preRoutePlannerForced,
    durableInspectionPlanningRequired,
    sourceWorkCategory: sourceWorkIntent.category,
    sourceWorkDomain: sourceWorkIntent.domain,
    sourceWorkOperation: sourceWorkIntent.operation,
    sourceWorkMutability: sourceWorkIntent.mutability,
    sourceWorkRequiresProjectEvidence: sourceWorkIntent.requiresProjectEvidence,
    sourceWorkRequiresDurableTask: sourceWorkIntent.requiresDurableTask,
    sourceAdmissionInfrastructureAvailable,
    sourceAnalysisAdmissionRequired,
    activeProjectResolved,
    durableInspectionDiscoveryLimit,
    currentTurnCapConfig,
    initialActiveProjectBootstrapForced,
    plannerAvailable,
    routeOwnershipAvailable,
    projectAgentDiscoveryAvailable,
    unroutedMutationDefinitionsPresent,
    routedMutationTools,
    rawMutationDefinitionsPresent,
    catalogRefreshForced,
    catalogRefreshBlocked,
    exactRequiredToolForced,
    exactRequiredToolName,
    workflowStopActive,
    invalidRequiredToolContract,
    invalidRequiredToolName: invalidRequiredToolContract ? advertisedRequiredToolName : "",
    requiredToolSchemaMissing,
    missingRequiredToolName: requiredToolSchemaMissing ? advertisedRequiredToolName : "",
    serverControlReadSchemaRecovered,
    evidenceFirstContractForced,
    evidenceFirstContractSatisfied,
    serverV2FeatureIntentHandoffAllowed,
    serverV2FeatureIntentToolForced,
    featureIntentDiscoveryHandoffForced,
    featureIntentEvidenceReady,
    featureIntentTargetBoundEvidenceReady: targetBoundEvidencePairReady,
    featureIntentEvidenceRefillActive,
    featureFrontierRecoveryActive,
    featureFrontierRepairToolForced,
    featureFrontierSemanticRediscoveryActive,
    featureFrontierSemanticRediscoveryRemaining,
    featureCompletionAuditRequired,
    effectiveFeatureIntentEvidenceReadThreshold,
    detachedSideQueryActive,
    detachedSideQueryRequest: detachedSideQueryActive
      ? String(nextCheckpoint?.sideQuery?.request || "").slice(0, 240)
      : "",
  });

  let modelChat = history;
  const lastCompactionCount = Number(checkpoint?.lastCompactionSourceMessageCount || 0);
  const messagesSinceLastCompaction = Math.max(0, messages.length - lastCompactionCount);
  let effectiveAction = decision.action;
  if (
    decision.action === "soft_compact"
    && lastCompactionCount > 0
    && messagesSinceLastCompaction < Number(config.minimumTurnsBetweenCompactions)
  ) {
    effectiveAction = "deferred";
  }

  // Mid-chat major goal switches may compact when budget is already soft/hard.
  // Ordinary follow-ups must not wipe retained turns solely because the objective string changed.
  const priorObjective = String(checkpoint?.objective || "").trim();
  const latestObjective = String(nextCheckpoint.objective || "").trim();
  const goalChanged = core.isMajorGoalChange(priorObjective, latestObjective);
  const userGoalCount = messages.filter((message) => {
    const text = String(message.getText() || "").trim();
    return message.getRole() === "user" && text && !core.isMetaUserMessage(text);
  }).length;
  const hasPriorAssistant = messages.some((message) => message.getRole() === "assistant");
  const latestClassificationGoal = latestRawRequestIntentContext.requestIntent
    ? architectureGoal
    : latestObjective;
  const latestRequestIntentContext = latestRawRequestIntentContext.requestIntent
    ? latestRawRequestIntentContext
    : matchingRequestIntentContext(latestClassificationGoal, nextCheckpoint);
  const latestIsReadOnly = core.isReadOnlyUserGoal(
    latestClassificationGoal,
    latestRequestIntentContext,
  );
  const budgetPressed = decision.action === "soft_compact" || decision.action === "hard_compact";
  // Major mode flips may soft-compact even when the budget is healthy, but ordinary
  // objective-string churn must not. A real major objective change starts with
  // no verbatim prior turns; the bounded checkpoint retains only verified facts.
  const goalChangeCompact = Boolean(
    enabled
    && !observeOnly
    && !trailingMetaUser
    && goalChanged,
  );
  const zeroRetainedTurns = Boolean(goalChangeCompact && priorObjective && latestObjective);
  if (currentTurnCapForced) {
    // This is an integrity bound, not a budget optimization: do not defer a
    // growing active turn merely because the token estimate is still healthy.
    effectiveAction = "soft_compact";
  }
  if (goalChangeCompact && effectiveAction === "normal") {
    effectiveAction = "soft_compact";
  }

  debugAgentLog("H9", "generator.ts:generate", "goal-change and meta gate", {
    priorObjectiveLen: priorObjective.length,
    latestObjectiveLen: latestObjective.length,
    authoritativeClassificationGoalLen: authoritativeClassificationGoal.length,
    authoritativeRequestIntentMutability:
      authoritativeRequestIntentContext.requestIntent?.mutability || "",
    checkpointRequestIntentMutability: nextCheckpoint?.requestIntent?.mutability || "",
    checkpointObjectiveHash: String(nextCheckpoint?.objectiveHash || ""),
    goalChanged,
    latestIsReadOnly,
    answeringMeta: Boolean(trailingMetaUser),
    userGoalCount,
    hasPriorAssistant,
    decisionAction: decision.action,
    effectiveAction,
    goalChangeCompact,
    zeroRetainedTurns,
    budgetPressed,
  });

  const shouldCompact = !serverOwnedDirectControlPending
    && (effectiveAction === "soft_compact" || effectiveAction === "hard_compact");
  let compactedMetrics: any = null;
  if (shouldCompact) {
    nextCheckpoint.compactionGeneration += 1;
    const applied = Boolean(!observeOnly && enabled);
    if (applied) {
      const compactConfig = zeroRetainedTurns
        ? { ...config, recentCompleteTurns: 0 }
        : config;
      try {
        compactedMetrics = await compactToTarget(
          model,
          history,
          nextCheckpoint,
          compactConfig,
          contextLength,
          decision.reservedTokens,
          {
            trailingMetaUser,
            maxCurrentTurnMessages: currentTurnCapForced
              ? config.maxCurrentTurnMessages
              : null,
            measure: (operation) => boundedPredictionSetupValue(
              ctl,
              remainingInitialSetupMs(),
              initialSetupStartedAt,
              operation,
            ),
            exhaustionContext: {
              taskSessionId: String(
                nextCheckpoint?.serverControl?.taskSessionId
                || nextCheckpoint?.taskRouteOwnership?.taskSessionId
                || "",
              ),
              controlEpoch: nextCheckpoint?.serverControl?.epoch ?? null,
              storeRevision: Number(nextCheckpoint?.storeRevision || 0),
              compactionGeneration: Number(nextCheckpoint?.compactionGeneration || 0),
              inputTokensBefore: inputTokens,
              systemPromptTokens: null,
              checkpointProjectionTokens,
              toolSchemaTokens,
              outputReserveTokens: config.maxOutputReserve,
              toolResultReserveTokens: core.expectedToolReserve(nextToolName, config),
              safetyMarginTokens: config.safetyMarginTokens,
              retainedEvidenceCount: Array.isArray(nextCheckpoint?.evidenceFacts)
                ? nextCheckpoint.evidenceFacts.length : 0,
              activeRequiredTool: exactRequiredToolName,
              recoveryStrategy: "bounded_partial_report_then_resume_frontier",
            },
          },
        );
      } catch (error) {
        await persistPredictionSetupFailure(error, "context_compaction_measurement");
        if (String((error as any)?.code || "") === "CONTEXT_COMPACTION_EXHAUSTED") {
          const details = (error as any)?.context && typeof (error as any).context === "object"
            ? { ...(error as any).context }
            : {};
          const remainingFrontier = Array.isArray(nextCheckpoint?.remainingFrontier)
            ? nextCheckpoint.remainingFrontier.slice(0, 32)
            : Array.isArray(nextCheckpoint?.inspectionProgress?.remainingFrontier)
              ? nextCheckpoint.inspectionProgress.remainingFrontier.slice(0, 32)
              : [];
          const partialReport = [
            "컨텍스트 안전 여백을 확보하지 못해 추가 모델 호출을 중단했습니다.",
            `현재까지 확인한 목표: ${String(nextCheckpoint?.objective || "(목표 미확인)").slice(0, 800)}`,
            "완료 상태가 아니며, 내구성 체크포인트의 기존 증거와 작업 식별자는 그대로 보존했습니다.",
            `남은 안전 여백: ${Number(details.remainingTokens || 0)} tokens (필수 ${Number(details.hardRemainingTokens || 0)}).`,
            remainingFrontier.length
              ? `남은 조사 범위: ${remainingFrontier.map((entry: any) => String(entry)).join(", ")}`
              : "남은 조사 범위는 체크포인트의 evidence/frontier에서 이어서 확인해야 합니다.",
            "다음 실행에서는 새 세션에서 같은 task/objective를 이어받아 남은 범위부터 진행하십시오.",
          ].join("\n");
          const outputDigest = predictionOutputDigest([
            { kind: "fragment", content: partialReport, opts: { reasoningType: "none" } },
          ], []);
          nextCheckpoint.coverageIncomplete = true;
          nextCheckpoint.remainingFrontier = remainingFrontier;
          nextCheckpoint.compactionRecovery = {
            version: 1,
            status: "partial_report_emitted",
            errorCode: "CONTEXT_COMPACTION_EXHAUSTED",
            details,
            objectiveHash: String(nextCheckpoint?.objectiveHash || ""),
            taskSessionId: String(
              nextCheckpoint?.serverControl?.taskSessionId
              || nextCheckpoint?.taskRouteOwnership?.taskSessionId
              || "",
            ),
            controlEpoch: nextCheckpoint?.serverControl?.epoch ?? null,
            checkpointGeneration: Number(nextCheckpoint?.checkpointGeneration || 0),
            mutationGeneration: Number(nextCheckpoint?.mutationGeneration || 0),
            recoveryStrategy: "resume_remaining_frontier",
            updatedAt: isoNow(),
          };
          nextCheckpoint.predictionState = lifecycleState(nextCheckpoint, "pending", {
            stopReason: "context_compaction_exhausted",
          });
          await persistCheckpoint(
            sessionId,
            nextCheckpoint,
            requireCheckpointPersistence,
            "context_compaction_exhausted_pending",
          );
          await appendEventBestEffort(sessionId, {
            type: "context_compaction_exhausted",
            at: isoNow(),
            ...details,
            coverageIncomplete: true,
            remainingFrontier,
          });
          ctl.fragmentGenerated(partialReport, { reasoningType: "none" });
          nextCheckpoint.predictionState = lifecycleState(nextCheckpoint, "committed", {
            outputDigest,
            stopReason: "context_compaction_exhausted_partial_report",
          });
          await persistCheckpoint(
            sessionId,
            nextCheckpoint,
            requireCheckpointPersistence,
            "context_compaction_exhausted_partial_report_committed",
          );
          return;
        }
        throw error;
      }
      modelChat = compactedMetrics.chat;
      nextCheckpoint.lastCompactionSourceMessageCount = messages.length;
    }
    if (applied && compactedMetrics) modelChat = compactedMetrics.chat;
    const progressSignature = compactionWorkflowProgressSignature(nextCheckpoint);
    const priorChurn = checkpoint?.compactionChurn && typeof checkpoint.compactionChurn === "object"
      ? checkpoint.compactionChurn
      : {};
    const consecutiveWithoutProgress = applied
      ? (String(priorChurn.progressSignature || "") === progressSignature
        ? Math.max(0, Number(priorChurn.consecutiveWithoutProgress || 0)) + 1
        : 1)
      : Math.max(0, Number(priorChurn.consecutiveWithoutProgress || 0));
    nextCheckpoint.compactionChurn = {
      version: 1,
      progressSignature,
      consecutiveWithoutProgress,
      lastAction: effectiveAction,
      updatedAt: isoNow(),
    };
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      goalChangeCompact,
      zeroRetainedTurns,
      currentTurnCapForced,
      currentTurnMessageCount,
      answeringMeta: Boolean(trailingMetaUser),
      applied,
      checkpointGeneration: nextCheckpoint.checkpointGeneration,
      postInputTokens: compactedMetrics?.inputTokens,
      postRemainingTokens: compactedMetrics?.remainingTokens,
      retainedTurns: compactedMetrics?.retainedTurns,
      currentTurnCap: compactedMetrics?.currentTurnCap,
      exactTemplateMeasurements: compactedMetrics?.exactTemplateMeasurements,
      measurementDurationMs: compactedMetrics?.measurementDurationMs,
      targetReached: compactedMetrics?.targetReached,
      removedCurrentTurnMessages: compactedMetrics?.removedCurrentTurnMessages,
      objectivePreview: String(nextCheckpoint.objective || "").slice(0, 160),
    });
    const deterministicTransitionPending = Boolean(
      exactRequiredToolForced
      || initialActiveProjectBootstrapForced
      || catalogRefreshForced
    );
    if (applied && consecutiveWithoutProgress >= 3 && !deterministicTransitionPending) {
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "compaction_churn_blocked",
      );
      await appendEventBestEffort(sessionId, {
        type: "compaction_churn_blocked",
        at: isoNow(),
        progressSignature,
        consecutiveWithoutProgress,
      });
      throw new Error(
        "Context compaction repeated three times without authoritative workflow progress. Generation stopped before another identical discovery cycle; resume from a narrowed server-owned route or a changed evidence frontier.",
      );
    }
  } else {
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      goalChangeCompact,
      zeroRetainedTurns,
      currentTurnCapForced,
      currentTurnMessageCount,
      answeringMeta: Boolean(trailingMetaUser),
      applied: false,
      messagesSinceLastCompaction,
      objectivePreview: String(nextCheckpoint.objective || "").slice(0, 160),
    });
  }
  nextCheckpoint.predictionState = mergeLifecycleState(
    nextCheckpoint.predictionState,
    lifecycleState(nextCheckpoint, "pending", { stopReason: "prediction" }),
  );
  if (
    modelFacingToolDefinitions.length === 0
    && !workflowStopActive
    && !requiredToolSchemaMissing
    && !catalogRefreshBlocked
  ) {
    nextCheckpoint.synthesisState = mergeLifecycleState(
      nextCheckpoint.synthesisState,
      lifecycleState(nextCheckpoint, "pending", { stopReason: "prediction" }),
    );
  }
  await persistCheckpoint(
    sessionId,
    nextCheckpoint,
    requireCheckpointPersistence,
    "before_prediction",
  );
  const emitDurableDeterministicFinal = async (content: string, reason: string): Promise<void> => {
    const outputDigest = predictionOutputDigest([
      { kind: "fragment", content, opts: { reasoningType: "none" } },
    ], []);
    // `completed` is an internal generation fact, not an output-delivery fact.
    // Persist a pre-emission state which cannot be mistaken for visible output;
    // only advance to committed after fragmentGenerated returns.
    nextCheckpoint.predictionState = lifecycleState(
      nextCheckpoint,
      "prepared",
      { outputDigest, stopReason: reason },
    );
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      `${reason}_prepared`,
    );
    ctl.fragmentGenerated(content, { reasoningType: "none" });
    nextCheckpoint.predictionState = mergeLifecycleState(
      nextCheckpoint.predictionState,
      lifecycleState(nextCheckpoint, "committed", { outputDigest, stopReason: reason }),
    );
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      `${reason}_committed`,
    );
  };
  if (catalogRefreshBlocked) {
    await appendEventBestEffort(sessionId, {
      type: "tool_catalog_refresh_failed",
      at: new Date().toISOString(),
      routeHash,
      routedMutationTools,
      attempts: Number(nextCheckpoint?.catalogRefresh?.attempts || 0),
      refreshToolAvailable: Boolean(catalogRefreshTool),
    });
    throw new Error(
      (catalogRefreshTool
        ? "The Unreal Agent tool catalog did not expose the active route's mutation schemas after one bounded refresh. "
        : "The stale Unreal Agent tool catalog has no read-only control available to request a refresh. ")
      + "Generation stopped before another health/read loop. Restart or re-enable mcp/unreal-agent, then continue the same task."
    );
  }
  if (requiredToolSchemaMissing) {
    const content = (
      `Work stopped safely: the server requires ${advertisedRequiredToolName}, but LM Studio's current chat `
      + "catalog does not expose that tool schema. No generated tool call was executed. Restart or re-enable "
      + "the affected Unreal MCP provider, open a new chat, and retry the same request."
    );
    await emitDurableDeterministicFinal(content, "required_tool_schema_missing");
    await appendEventBestEffort(sessionId, {
      type: "required_tool_schema_missing_final_emitted",
      at: new Date().toISOString(),
      requiredTool: advertisedRequiredToolName,
      routeHash,
      targetModelInvoked: false,
      toolRequestCount: 0,
    });
    return;
  }
  if (decision.action === "hard_compact" && (!enabled || observeOnly)) {
    await appendEventBestEffort(sessionId, {
      type: "generation_blocked",
      at: new Date().toISOString(),
      reason: enabled ? "observe_only_at_hard_limit" : "compactor_disabled_at_hard_limit",
      decision,
    });
    throw new Error(
      "Context is below the hard safety threshold, but compaction is not active. "
      + "Enable the context compactor and disable observe-only mode before continuing.",
    );
  }

  // A server-owned workflow stop is already a terminal decision for this turn.
  // Do not ask the target model to restate it: tool-trained models can emit
  // literal <tool_call> markup even when no tool schemas are advertised. Emit a
  // deterministic final response so both real and simulated tool calls are
  // impossible and the user immediately sees why work stopped.
  if (workflowStopActive) {
    const content = serverControlV2Active
      ? (
        serverControlV2?.disposition === "complete"
          ? "The server reports that the active task is complete. No further tool call is permitted."
          : serverControlV2?.disposition === "await_user"
          ? String(serverControlV2?.requiredUserInput?.prompt || "The server is waiting for user input.")
          : `The server stopped the current workflow${serverControlV2?.blocker?.code ? ` (${serverControlV2.blocker.code})` : ""}. No further tool call is permitted for this control epoch.`
      )
      : workflowStopFinalResponse(
        nextCheckpoint.semanticBlocker,
        `${authoritativeGoal}\n${architectureGoal}`,
      );
    await emitDurableDeterministicFinal(content, "workflow_stop_final");
    await appendEventBestEffort(sessionId, {
      type: "workflow_stop_final_emitted",
      at: new Date().toISOString(),
      errorCode: String(
        serverControlV2?.blocker?.code
        || nextCheckpoint?.semanticBlocker?.errorCode
        || (serverControlV2?.disposition === "complete" ? "TASK_COMPLETE" : "SERVER_WORKFLOW_BLOCKED"),
      ).slice(0, 120),
      targetModelInvoked: false,
      toolRequestCount: 0,
    });
    return;
  }

  // Qwen multi_step_tool Jinja raises:
  //   raise_exception('No user query found in messages.')
  // which surfaces as applyPromptTemplate HTTP 400. Never send a user-less chat.
  const chatHasRealUser = (chat: Chat): boolean => chat.getMessagesArray().some((message) => {
    if (message.getRole() !== "user") return false;
    const text = String(message.getText() || "").trim();
    return Boolean(text) && !core.isMetaUserMessage(text);
  });
  if (!chatHasRealUser(modelChat)) {
    if (chatHasRealUser(history)) {
      console.warn(
        "[unreal-context-compactor] Compacted/model chat lost the user query; "
        + "falling back to inbound history to avoid Jinja 400.",
      );
      modelChat = history;
      debugAgentLog("H-userless", "generator.ts:before_respond", "fallback inbound history", {
        compactedLostUser: true,
      });
    } else {
      throw new Error(
        "Chat history has no user query. Qwen/Jinja templates require a user message "
        + "(applyPromptTemplate 400: No user query found in messages).",
      );
    }
  }

  const events: any[] = [];
  const requests: any[] = [];
  let deferredRequiredNextTool: any | null = null;
  let predictionHeartbeatCount = 0;
  const strictToolControlPlane = Boolean(config.strictToolControlPlane);
  // A persisted requiredNextTool is a safety gate even when the optional
  // strict mode is disabled. Otherwise a model can emit an unrelated tool
  // call and the checkpoint would record progress that never happened.
  const requiredToolGateActive = Boolean(
    !detachedSideQueryActive
    && nextCheckpoint?.requiredNextTool?.name
    && !core.isNonToolNextAction(nextCheckpoint.requiredNextTool.name),
  );
  const toolControlPlaneEnforced = strictToolControlPlane
    || serverControlV2Active
    || requiredToolGateActive
    || architectureToolForced
    || architectureEvidenceRefillActive
    || featureIntentEvidenceRefillActive
    || initialActiveProjectBootstrapForced
    || preRoutePlannerForced
    || catalogRefreshForced
    || exactRequiredToolForced
    || semanticForbiddenTools.length > 0
    || semanticForbiddenCallFingerprints.length > 0
    || workflowStopActive
    || detachedSideQueryActive;
  const bufferUntilPredictionComplete = Boolean(config.bufferUntilPredictionComplete)
    || requireCheckpointPersistence
    || Boolean(config.rejectTruncatedPredictions);
  const emitEvent = (event: any) => {
    if (event.kind === "fragment") ctl.fragmentGenerated(event.content, event.opts);
    else if (
      event.kind === "start"
      || event.kind === "name"
      || event.kind === "args"
      || event.kind === "end"
      || event.kind === "failure"
    ) emitHostFacingLifecycleEvent(event);
  };
  let streamedReasoningEventCount = 0;
  let reasoningTokensObserved = 0;
  let finalTokensObserved = 0;
  let toolJsonCharactersObserved = 0;
  let predictionAttemptSequence = 0;
  const recordEvent = (event: any) => {
    // Tool stages are always prepared durably before frontend dispatch. Atomic
    // text remains configurable for backward compatibility, while call ids and
    // requests never take the unsafe uncheckpointed streaming path.
    const durableToolStage = ["start", "name", "args", "end", "failure"].includes(
      String(event?.kind || ""),
    );
    const outputBuffered = toolControlPlaneEnforced || bufferUntilPredictionComplete || durableToolStage;
    const reasoningType = String(event?.opts?.reasoningType || "none");
    const streamAsProgress = Boolean(
      outputBuffered
      && config.streamReasoningProgress
      && event.kind === "fragment"
      && reasoningType !== "none"
    );
    if (streamAsProgress) {
      // Reasoning is transient progress, not a committed final answer or tool
      // request. Streaming it keeps the UI alive while atomic final/tool output
      // and truncation rejection remain intact.
      streamedReasoningEventCount += 1;
      emitEvent(event);
    } else if (outputBuffered) events.push(event);
    else emitEvent(event);
  };
  // Prepared/reconnect replay starts before this buffered prediction turn is
  // assembled. From this point on, every accepted tool stage is routed through
  // the same host transaction ledger so interleaved model callbacks cannot
  // expose a second lifecycle to LM Studio.
  hostToolCallLifecycle.setEmitter((event) => recordEvent(event));
  const recordDirectToolCallLifecycle = (
    request: any,
    callId: number,
    source: string,
  ): string => {
    const rawToolCallId = String(request?.id || `direct-${callId}`).trim();
    const directAttemptId = `${outerGenerateRequestId}:direct:${String(source || "server_owned")}`;
    const hostToolCallId = `${outerGenerateRequestId}:${String(source || "server_owned")}:${core.sha256(rawToolCallId).slice(0, 20)}`
      .slice(0, 240);
    const identity = {
      hostToolCallId,
      outerGenerateRequestId,
      predictionAttemptId: directAttemptId,
      modelLocalCallId: String(callId),
      rawToolCallId,
      toolCallId: rawToolCallId,
      toolName: String(request?.name || ""),
      source,
      callId,
      durableIdentity: true,
    };
    hostToolCallLifecycle.record({ stage: "start", ...identity });
    hostToolCallLifecycle.record({
      stage: "name",
      ...identity,
      name: String(request?.name || ""),
    });
    hostToolCallLifecycle.record({
      stage: "args",
      ...identity,
      content: JSON.stringify(request?.arguments || {}),
    });
    hostToolCallLifecycle.record({ stage: "end", ...identity, request });
    return hostToolCallId;
  };
  const runPredictionAttempt = async (
    predictionTools: any[],
    forceTool: boolean,
    progressLabel = "Model reasoning",
    effortOverride?: ReasoningEffort,
    thinkingOverride?: boolean,
  ): Promise<string> => {
    guardGeneratorAbort(ctl);
    const predictionStartedAt = Date.now();
    const predictionWallClockStartedAt = performance.now();
    const predictionWallClockMs = Number(config.predictionWallClockSeconds) * 1000;
    const predictionDeadlineAt = predictionWallClockStartedAt + predictionWallClockMs;
    predictionAttemptSequence += 1;
    // A bounded repair/reasoning retry is a new attempt within the same
    // control identity. Open it explicitly instead of monotonic-merging with
    // the prior attempt's completed state; otherwise a setup failure in the
    // retry can leave an authoritative-looking completed checkpoint behind.
    nextCheckpoint.predictionState = lifecycleState(
      nextCheckpoint,
      "pending",
      { stopReason: "prediction_attempt" },
    );
    if (nextCheckpoint.synthesisState && predictionTools.length === 0) {
      nextCheckpoint.synthesisState = lifecycleState(
        nextCheckpoint,
        "pending",
        { stopReason: "prediction_attempt" },
      );
    }
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "before_prediction_attempt_setup",
    );
    await appendEventBestEffort(sessionId, {
      type: "prediction_attempt_started",
      at: isoNow(),
      attempt: predictionAttemptSequence,
    });
    const remainingPredictionWallClockMs = () => {
      const now = performance.now();
      const remainingMs = predictionDeadlineAt - now;
      if (remainingMs <= 0) {
        throw predictionSupervisorError(
          "PREDICTION_WALL_CLOCK_EXCEEDED",
          now - predictionWallClockStartedAt,
        );
      }
      return remainingMs;
    };
    let formattedAttemptPrompt: any;
    let attemptInputTokens: number;
    let attemptToolSchemaTokens: number;
    try {
      formattedAttemptPrompt = await boundedPredictionSetupValue(
        ctl,
        remainingPredictionWallClockMs(),
        predictionWallClockStartedAt,
        () => model.applyPromptTemplate(modelChat),
      );
      attemptInputTokens = Math.max(0, Number(await boundedPredictionSetupValue(
        ctl,
        remainingPredictionWallClockMs(),
        predictionWallClockStartedAt,
        () => model.countTokens(formattedAttemptPrompt),
      )) || 0);
      attemptToolSchemaTokens = predictionTools.length > 0
        ? Math.max(0, Number(await boundedPredictionSetupValue(
          ctl,
          remainingPredictionWallClockMs(),
          predictionWallClockStartedAt,
          () => model.countTokens(JSON.stringify(predictionTools)),
        )) || 0)
        : 0;
    } catch (error) {
      await persistPredictionSetupFailure(error, "attempt_prompt_measurement");
      throw error;
    }
    const attemptEstimatedPromptTokens = attemptInputTokens + attemptToolSchemaTokens;
    let attemptMaxOutputTokens = Number(config.maxOutputReserve);
    let attemptSynthesisCompatibility = nextCheckpoint.predictionPolicy?.budgets?.synthesisCompatibility || null;
    if (predictionPhase === "synthesis_final") {
      const conservativePrefillTokensPerSecond = 512;
      const conservativeDecodeTokensPerSecond = 40;
      const finalizationMarginSeconds = 15;
      const remainingWallClockSeconds = remainingPredictionWallClockMs() / 1000;
      const estimatedPrefillSeconds = attemptInputTokens / conservativePrefillTokensPerSecond;
      const availableDecodeSeconds = remainingWallClockSeconds
        - estimatedPrefillSeconds - finalizationMarginSeconds;
      const maximumCompatibleOutputTokens = Math.floor(
        Math.max(0, availableDecodeSeconds) * conservativeDecodeTokensPerSecond,
      );
      if (maximumCompatibleOutputTokens < 512) {
        const error: any = new Error(
          "Synthesis prediction policy cannot fit within the remaining wall-clock budget.",
        );
        error.errorCode = "SYNTHESIS_BUDGET_INCOMPATIBLE";
        error.code = error.errorCode;
        error.details = {
          attemptInputTokens,
          remainingWallClockSeconds,
          estimatedPrefillSeconds,
          availableDecodeSeconds,
          maximumCompatibleOutputTokens,
        };
        await persistPredictionSetupFailure(error, "synthesis_budget_compatibility");
        throw error;
      }
      attemptMaxOutputTokens = Math.min(
        attemptMaxOutputTokens,
        maximumCompatibleOutputTokens,
        4096,
      );
      attemptSynthesisCompatibility = {
        configuredTokens: synthesisOutputReserve,
        effectiveTokens: attemptMaxOutputTokens,
        maximumCompatibleOutputTokens,
        estimatedPrefillSeconds,
        remainingWallClockSeconds,
        conservativePrefillTokensPerSecond,
        conservativeDecodeTokensPerSecond,
        finalizationMarginSeconds,
        calculationStage: "attempt_prompt",
        compatible: true,
      };
    }
    const attemptInitialNoProgressMs = initialPredictionNoProgressMs(
      attemptEstimatedPromptTokens,
      Number(config.predictionNoProgressSeconds) * 1000,
      predictionWallClockMs,
    );
    const attemptEffort = effortOverride || config.reasoningEffort;
    const attemptThinkingEnabled = thinkingOverride ?? thinkingEnabled;
    const attemptBudgets = {
      ...(nextCheckpoint.predictionPolicy?.budgets || {}),
      maxOutputTokens: attemptMaxOutputTokens,
      finalAnswerReserveTokens: predictionTools.length === 0
        ? attemptMaxOutputTokens
        : configuredOutputReserve,
      synthesisCompatibility: attemptSynthesisCompatibility,
      inputTokens: attemptInputTokens,
      toolSchemaTokens: attemptToolSchemaTokens,
      estimatedPromptTokens: attemptEstimatedPromptTokens,
      // This is the configured prompt-size ceiling. The supervisor applies
      // the smaller of this value and the hard wall time remaining after
      // setup/model-fence work, so the checkpoint does not claim a larger
      // allowance was actually applied.
      configuredPrefillNoProgressCeilingSeconds: attemptInitialNoProgressMs / 1000,
      setupElapsedSeconds: (performance.now() - predictionWallClockStartedAt) / 1000,
    };
    // Migration: do not retain the former ambiguous name in a newly signed
    // policy fingerprint when resuming an older checkpoint.
    delete (attemptBudgets as any).initialNoProgressSeconds;
    const attemptPolicy = {
      ...nextCheckpoint.predictionPolicy,
      budgets: attemptBudgets,
      reasoningControl: {
        ...(nextCheckpoint.predictionPolicy?.reasoningControl || {}),
        effort: reasoningRawConfig && attemptThinkingEnabled ? attemptEffort : null,
        enableThinking: Boolean(reasoningRawConfig && attemptThinkingEnabled),
      },
      attemptStartedAt: new Date(predictionStartedAt).toISOString(),
      policyRecordedAt: isoNow(),
    };
    delete attemptPolicy.fingerprint;
    nextCheckpoint.predictionPolicy = {
      ...attemptPolicy,
      fingerprint: core.sha256(core.stableStringify(attemptPolicy)),
    };
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "before_prediction_attempt",
    );
    // LM Studio callback ids are scoped to one model prediction and can be
    // reused by a bounded repair prediction in the same generator turn.
    const callbackFsm = createToolCallCallbackFsm();
    const predictionAttemptId = `${outerGenerateRequestId}:attempt-${predictionAttemptSequence}`;
    hostToolCallLifecycle.setActivePredictionAttempt(predictionAttemptId);
    const callbackHostToolCallIds = new Map<number, string>();
    const attemptQualifiedToolCallId = (callId: number, rawId: unknown): string => {
      const stableRawId = String(rawId || `callback-${callId}`).trim() || `callback-${callId}`;
      return `attempt-${predictionAttemptSequence}-${stableRawId}`;
    };
    const callbackHostToolCallId = (callId: number, rawId: unknown): string => {
      const stableRawId = String(rawId || `callback-${callId}`).trim() || `callback-${callId}`;
      const rawDigest = core.sha256(stableRawId).slice(0, 20);
      return `${predictionAttemptId}:model-${callId}-${rawDigest}`.slice(0, 240);
    };
    let lastSemanticProgressAt = predictionStartedAt;
    let lastSerializationProgressMonotonicAt = predictionWallClockStartedAt;
    let lastInferenceActivityAt = predictionStartedAt;
    let inferenceStartedMonotonicAt: number | null = null;
    const eventStartIndex = events.length;
    const requestStartIndex = requests.length;
    const markInferenceActivity = () => {
      lastInferenceActivityAt = Date.now();
      if (inferenceStartedMonotonicAt === null) {
        inferenceStartedMonotonicAt = performance.now();
      }
    };
    const markSerializationProgress = () => {
      markInferenceActivity();
      lastSerializationProgressMonotonicAt = performance.now();
    };
    let lastVisibleProgressAt = predictionStartedAt;
    const heartbeatIntervalMs = Number(config.predictionHeartbeatSeconds) * 1000;
    let heartbeat: ReturnType<typeof setInterval> | null = null;
    const emitHeartbeatIfDue = () => {
      const now = Date.now();
      if ((ctl as any)?.abortSignal?.aborted || now - lastVisibleProgressAt < heartbeatIntervalMs) {
        return;
      }
      predictionHeartbeatCount += 1;
      lastVisibleProgressAt = now;
      ctl.fragmentGenerated(
        `\n[Working: ${progressLabel} - ${Math.max(1, Math.floor((now - predictionStartedAt) / 1000))}s elapsed]\n`,
        {
          reasoningType: "reasoning",
          containsDrafted: false,
          isStructural: true,
        },
      );
    };
    if (config.streamReasoningProgress && heartbeatIntervalMs > 0) {
      heartbeat = setInterval(emitHeartbeatIfDue, Math.min(1000, heartbeatIntervalMs));
    }
    let attemptCallbacksActive = true;
    const attemptCallbacksAreActive = () => Boolean(
      attemptCallbacksActive && !(ctl as any)?.abortSignal?.aborted
    );
    let attemptCallbackFailure: unknown = null;
    let rejectAttemptCallbackFailure: (error: unknown) => void = () => {};
    const attemptCallbackFailurePromise = new Promise<never>((_resolve, reject) => {
      rejectAttemptCallbackFailure = reject;
    });
    const deferAttemptCallback = (callback: () => void) => {
      queueMicrotask(() => {
        if (!attemptCallbacksAreActive()) return;
        try {
          callback();
        } catch (error) {
          attemptCallbackFailure = error;
          attemptCallbacksActive = false;
          rejectAttemptCallbackFailure(error);
        }
      });
    };
    try {
      const predictionModelFence = await captureModelFence(
        model,
        resolvedTargetModel,
        contextLength,
        {
          signal: (ctl as any)?.abortSignal,
          timeoutMs: remainingPredictionWallClockMs(),
          timeoutCode: "PREDICTION_WALL_CLOCK_EXCEEDED",
        },
      );
      if (!modelFencesMatch(transactionModelFence, predictionModelFence)) {
        throw modelFenceChangedError(transactionModelFence, predictionModelFence);
      }
      const attemptReasoningRawConfig = qwen38ReasoningRawConfig(
        resolvedTargetModel,
        attemptThinkingEnabled,
        attemptThinkingEnabled ? attemptEffort : null,
      );
      const prediction = model.respond(modelChat, {
        maxTokens: attemptMaxOutputTokens,
        temperature: Number(config.temperature),
        topPSampling: Number(config.topPSampling),
        topKSampling: Number(config.topKSampling),
        minPSampling: Number(config.minPSampling),
        ...(attemptReasoningRawConfig ? { raw: attemptReasoningRawConfig } : {}),
        ...(predictionTools.length > 0 ? {
          rawTools: {
            type: "toolArray",
            tools: predictionTools,
            ...(forceTool ? { force: true } : {}),
          },
        } : {}),
        contextOverflowPolicy: "stopAtLimit",
        signal: ctl.abortSignal,
        onPredictionFragment(fragment: any) {
          deferAttemptCallback(() => {
            const reasoningType = String(fragment?.reasoningType || "none");
            // Private reasoning is inference activity only. Final text and tool
            // JSON reset this prediction's serialization watchdog, but neither
            // advances authoritative workflow state before the atomic commit.
            const observedTokens = Math.max(0, Number(fragment?.tokensCount || 0));
            if (reasoningType === "none") {
              // Final-answer bytes are committed only after the prediction
              // finishes, but their arrival is still real serialization
              // progress for the prediction watchdog.  The wall-clock budget
              // remains the hard bound on a backend that trickles output.
              if (observedTokens > 0 || String(fragment?.content || "").length > 0) {
                markSerializationProgress();
              } else {
                markInferenceActivity();
              }
              finalTokensObserved += observedTokens;
            } else {
              markInferenceActivity();
              reasoningTokensObserved += observedTokens;
            }
            if (
              config.streamReasoningProgress
              && String(fragment?.reasoningType || "none") !== "none"
            ) {
              lastVisibleProgressAt = Date.now();
            }
            recordEvent({
              kind: "fragment",
              content: String(fragment.content || ""),
              opts: fragmentOptions(fragment),
            });
          });
        },
        onToolCallRequestStart(callId: number, info: any) {
          deferAttemptCallback(() => {
            const qualifiedToolCallId = attemptQualifiedToolCallId(callId, info?.toolCallId);
            const hostToolCallId = callbackHostToolCallId(callId, info?.toolCallId);
            if (!callbackFsm.start(callId, qualifiedToolCallId)) return;
            callbackHostToolCallIds.set(callId, hostToolCallId);
            markSerializationProgress();
            hostToolCallLifecycle.record({
              stage: "start",
              hostToolCallId,
              outerGenerateRequestId,
              predictionAttemptId,
              modelLocalCallId: String(callId),
              rawToolCallId: String(info?.toolCallId || ""),
              toolCallId: qualifiedToolCallId,
              source: "model_callback",
              callId,
            });
          });
        },
        onToolCallRequestNameReceived(callId: number, name: string) {
          deferAttemptCallback(() => {
            if (!callbackFsm.name(callId, name)) return;
            const hostToolCallId = callbackHostToolCallIds.get(callId)
              || callbackHostToolCallId(callId, "");
            markSerializationProgress();
            hostToolCallLifecycle.record({
              stage: "name",
              hostToolCallId,
              outerGenerateRequestId,
              predictionAttemptId,
              modelLocalCallId: String(callId),
              toolName: name,
              name,
              source: "model_callback",
              callId,
            });
          });
        },
        onToolCallRequestArgumentFragmentGenerated(callId: number, content: string) {
          deferAttemptCallback(() => {
            if (!callbackFsm.args(callId, content)) return;
            if (String(content || "").length > 0) markSerializationProgress();
            else markInferenceActivity();
            toolJsonCharactersObserved += String(content || "").length;
            const hostToolCallId = callbackHostToolCallIds.get(callId)
              || callbackHostToolCallId(callId, "");
            hostToolCallLifecycle.record({
              stage: "args",
              hostToolCallId,
              outerGenerateRequestId,
              predictionAttemptId,
              modelLocalCallId: String(callId),
              toolCallId: attemptQualifiedToolCallId(callId, ""),
              source: "model_callback",
              callId,
              content,
            });
          });
        },
        onToolCallRequestEnd(callId: number, info: any) {
          deferAttemptCallback(() => {
            const rawRequest = info?.toolCallRequest || {};
            const rawToolCallId = String(rawRequest?.id || "").trim();
            const hostRequest = {
              ...rawRequest,
              id: attemptQualifiedToolCallId(callId, rawToolCallId),
            };
            const request = enrichToolRequestControl(
              hostRequest,
              sessionId,
              nextCheckpoint,
              authoritativeGoal,
              modelFacingToolDefinitions,
            );
            if (!callbackFsm.end(callId, request)) return;
            markSerializationProgress();
            const hostToolCallId = callbackHostToolCallIds.get(callId)
              || callbackHostToolCallId(callId, rawToolCallId);
            if (request !== hostRequest && (toolControlPlaneEnforced || bufferUntilPredictionComplete)) {
              replaceBufferedArgumentFragments(events, callId, request.arguments);
              hostToolCallLifecycle.replaceArguments(hostToolCallId, request.arguments);
            }
            hostToolCallLifecycle.record({
              stage: "end",
              hostToolCallId,
              outerGenerateRequestId,
              predictionAttemptId,
              modelLocalCallId: String(callId),
              rawToolCallId,
              toolCallId: hostRequest.id,
              toolName: request.name,
              source: "model_callback",
              callId,
              request,
            });
            requests.push({ callId, request, hostToolCallId });
          });
        },
        onToolCallRequestFailure(callId: number, error: Error) {
          deferAttemptCallback(() => {
            if (!callbackFsm.failure(callId, error)) return;
            markSerializationProgress();
            const hostToolCallId = callbackHostToolCallIds.get(callId)
              || callbackHostToolCallId(callId, "");
            const qualifiedToolCallId = attemptQualifiedToolCallId(callId, "");
            hostToolCallLifecycle.record({
              stage: "failure",
              hostToolCallId,
              outerGenerateRequestId,
              predictionAttemptId,
              modelLocalCallId: String(callId),
              toolCallId: qualifiedToolCallId,
              source: "model_callback",
              callId,
              error: String(error?.message || error),
              allowImplicitStart: true,
            });
          });
        },
      });
      // Model-fence verification happens after predictionStartedAt but before
      // the backend prediction is supervised. A loaded runner can delay that
      // verification longer than the semantic-silence budget. Anchor the
      // watchdog at the moment supervision can actually observe callbacks so
      // pre-response setup latency cannot cancel valid tool serialization.
      const predictionSupervisionStartedAt = performance.now();
      let supervisionWallClockMs: number;
      try {
        guardGeneratorAbort(ctl);
        supervisionWallClockMs = remainingPredictionWallClockMs();
      } catch (error) {
        // respond() is normally synchronous and cheap, but a hostile or loaded
        // backend can return its handle only after the attempt deadline. Once
        // the handle exists, cancel it before propagating that boundary error.
        attemptCallbacksActive = false;
        try {
          Promise.resolve(prediction?.cancel?.()).catch(() => {});
        } catch {
          // Preserve the original deadline/abort error.
        }
        throw error;
      }
      const predictionResult: any = await predictionResultWithSupervision(prediction, ctl, {
        wallClockMs: supervisionWallClockMs,
        noProgressMs: Number(config.predictionNoProgressSeconds) * 1000,
        initialNoProgressMs: Math.min(
          supervisionWallClockMs,
          attemptInitialNoProgressMs,
        ),
        failurePromise: attemptCallbackFailurePromise,
        getFailure: () => attemptCallbackFailure,
        beforeCancel: () => {
          // Some backends synchronously re-enter stream callbacks from their
          // cancel hook. Close the attempt before invoking backend cancel.
          attemptCallbacksActive = false;
        },
        onResultSettled: () => {
          // The first fulfillment/rejection reaction closes the attempt before
          // any callback queued later in that same backend task can execute.
          attemptCallbacksActive = false;
        },
        getLastProgressAt: () => Math.max(
          predictionSupervisionStartedAt,
          lastSerializationProgressMonotonicAt,
        ),
        getInferenceStartedAt: () => inferenceStartedMonotonicAt,
      });
      // A backend may still invoke queued callbacks after result() settles or
      // cancel() returns. Close this attempt before any post-result awaits so
      // stale output can never enter a later retry's shared buffers.
      attemptCallbacksActive = false;
      // setInterval may be delayed behind the prediction completion callback
      // on a loaded Windows runner. Enforce the absolute heartbeat deadline
      // once more before accepting completed output.
      if (config.streamReasoningProgress && heartbeatIntervalMs > 0) emitHeartbeatIfDue();
      nextCheckpoint.predictionActivity = {
        version: 1,
        inferenceActivityAt: new Date(lastInferenceActivityAt).toISOString(),
        semanticProgressAt: new Date(lastSemanticProgressAt).toISOString(),
      };
      guardGeneratorAbort(ctl);
      const completedModelFence = await captureModelFence(
        model,
        resolvedTargetModel,
        contextLength,
        {
          signal: (ctl as any)?.abortSignal,
          timeoutMs: remainingPredictionWallClockMs(),
          timeoutCode: "PREDICTION_WALL_CLOCK_EXCEEDED",
        },
      );
      if (!modelFencesMatch(predictionModelFence, completedModelFence)) {
        throw modelFenceChangedError(predictionModelFence, completedModelFence);
      }
      const backendPromptCandidates = [
        predictionResult?.stats?.promptTokens,
        predictionResult?.stats?.promptTokensCount,
        predictionResult?.stats?.promptEvalCount,
        predictionResult?.stats?.tokensEvaluated,
      ];
      const backendPromptValue = backendPromptCandidates
        .map((value) => Number(value))
        .find((value) => Number.isFinite(value) && value >= 0);
      nextCheckpoint.actualBackendPromptTokens = backendPromptValue ?? null;
      await appendEventBestEffort(sessionId, {
        type: "backend_prompt_measurement",
        at: isoNow(),
        actualBackendPromptTokens: backendPromptValue ?? null,
        available: backendPromptValue !== undefined,
        historyPromptTokens: attemptInputTokens,
        toolSchemaTokens: attemptToolSchemaTokens,
        totalEstimatedPromptTokens: attemptEstimatedPromptTokens,
      });
      return String(predictionResult?.stats?.stopReason || "");
    } catch (error: any) {
      attemptCallbacksActive = false;
      // Output is attempt-local until every boundary check succeeds. Discard
      // it for supervisor errors, aborts, callback FSM failures, and any other
      // exceptional exit so a retry can never inherit stale buffered output.
      events.splice(eventStartIndex);
      requests.splice(requestStartIndex);
      const signalAborted = Boolean((ctl as any)?.abortSignal?.aborted);
      const code = String(error?.code || (signalAborted ? "GENERATION_ABORTED" : ""));
      if (
        signalAborted
        || code === "GENERATION_ABORTED"
        || code === "PREDICTION_WALL_CLOCK_EXCEEDED"
        || code === "PREDICTION_NO_PROGRESS_EXCEEDED"
        || code === "PREDICTION_PREFILL_NO_PROGRESS_EXCEEDED"
        || code === "MODEL_INSTANCE_CHANGED"
        || code === "MODEL_FENCE_TIMEOUT"
      ) {
        // Every generated event from the cancelled prediction is uncommitted.
        // Retain only output prepared before this bounded prediction attempt.
        nextCheckpoint.predictionActivity = {
          version: 1,
          inferenceActivityAt: new Date(lastInferenceActivityAt).toISOString(),
          semanticProgressAt: new Date(lastSemanticProgressAt).toISOString(),
        };
        nextCheckpoint.predictionState = mergeLifecycleState(
          nextCheckpoint.predictionState,
          lifecycleState(nextCheckpoint, "pending", { stopReason: code.toLowerCase() }),
        );
        await persistCheckpoint(
          sessionId,
          nextCheckpoint,
          requireCheckpointPersistence,
          code === "MODEL_INSTANCE_CHANGED" || code === "MODEL_FENCE_TIMEOUT"
            ? "prediction_model_fence_rejected"
            : "prediction_supervisor_cancelled",
        );
        await appendEventBestEffort(sessionId, {
          type: code === "MODEL_INSTANCE_CHANGED" || code === "MODEL_FENCE_TIMEOUT"
            ? "prediction_model_fence_rejected"
            : "prediction_supervisor_cancelled",
          at: new Date().toISOString(),
          reason: code,
          elapsedMs: Number(error?.elapsedMs || Date.now() - predictionStartedAt),
          progressLabel,
          bufferedOutputDiscarded: true,
          reasoningTokensObserved,
          finalTokensObserved,
          toolJsonCharactersObserved,
          inferenceActivityAt: nextCheckpoint.predictionActivity.inferenceActivityAt,
          semanticProgressAt: nextCheckpoint.predictionActivity.semanticProgressAt,
          ...(code === "MODEL_INSTANCE_CHANGED" || code === "MODEL_FENCE_TIMEOUT" ? {
            expectedModelFence: error?.expectedModelFence || transactionModelFence,
            actualModelFence: error?.actualModelFence || null,
          } : {}),
        });
      }
      throw error;
    } finally {
      hostToolCallLifecycle.closePredictionAttempt(predictionAttemptId);
      if (heartbeat) clearInterval(heartbeat);
    }
  };
  const runPrediction = async (
    predictionTools: any[],
    forceTool: boolean,
    progressLabel = "Model reasoning",
    overrides: { effort?: ReasoningEffort; thinkingEnabled?: boolean } = {},
  ): Promise<string> => {
    const effortOrder: ReasoningEffort[] = ["xhigh", "medium", "low"];
    const configuredEffort = overrides.effort || config.reasoningEffort as ReasoningEffort;
    const recoveryThinkingEnabled = overrides.thinkingEnabled ?? thinkingEnabled;
    const persistedEffort = String(nextCheckpoint?.reasoningFallback?.effort || "") as ReasoningEffort;
    let effort = effortOrder.includes(persistedEffort) ? persistedEffort : configuredEffort;
    const floorRecoveryStrategy = "execute_exact_canonical_control";
    const emitServerOwnedFloorRecovery = async (): Promise<string | null> => {
      const control = core.compactServerControl(nextCheckpoint?.serverControl);
      const required = control?.requiredTool;
      const toolName = String(required?.name || "").trim();
      if (!toolName || !required?.args || typeof required.args !== "object") return null;
      if (!control) return null;
      if (!predictionTools.some((tool: any) => toolNamesMatch(
        toolName,
        tool?.function?.name || tool?.name || "",
      ))) return null;
      const recoveryPathDigest = core.sha256(core.stableStringify({
        toolName,
        args: required.args,
        epoch: control.epoch,
        fingerprint: control.controlFingerprint,
      })).slice(0, 16);
      const rawRequest = {
        id: directToolCallRequestId("server-owned-low-floor", recoveryPathDigest),
        type: "function",
        name: toolName,
        arguments: { ...required.args },
      };
      const request = enrichToolRequestControl(
        rawRequest,
        sessionId,
        nextCheckpoint,
        authoritativeGoal,
        modelFacingToolDefinitions,
      );
      const callId = requests.length;
      const hostToolCallId = recordDirectToolCallLifecycle(
        request,
        callId,
        "reasoning_floor_recovery",
      );
      requests.push({ callId, request, hostToolCallId });
      nextCheckpoint.reasoningFallback = {
        version: 1,
        status: "retrying",
        configuredEffort,
        effort,
        reason: "semantic_no_progress_at_effort_floor",
        attempts: Math.max(0, Number(nextCheckpoint?.reasoningFallback?.attempts || 0)) + 1,
        sameEffortRetries: 0,
        toolChoiceForced: false,
        thinkingSuppressed: false,
        serverOwnedTransition: true,
        recoveryStrategy: floorRecoveryStrategy,
        transitionKind: "canonical_required_tool",
        updatedAt: isoNow(),
      };
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "reasoning_effort_floor_direct_transition_selected",
      );
      await appendEventBestEffort(sessionId, {
        type: "reasoning_effort_floor_direct_transition_selected",
        at: isoNow(),
        effort,
        reason: "semantic_no_progress_at_effort_floor",
        tool: toolName,
        path: String(required.args.path || ""),
        transitionKind: "canonical_required_tool",
        taskSessionId: String(nextCheckpoint?.taskRouteOwnership?.taskSessionId || ""),
        modelSerializationBypassed: true,
        outputPrepared: false,
        recoveryStrategy: floorRecoveryStrategy,
      });
      return "server_owned_low_floor_recovery";
    };
    const persistedFloorRecovery = Boolean(
      nextCheckpoint?.reasoningFallback?.status === "retrying"
      && nextCheckpoint?.reasoningFallback?.reason === "semantic_no_progress_at_effort_floor"
    );
    if (persistedFloorRecovery) {
      const recovered = await emitServerOwnedFloorRecovery();
      if (recovered) return recovered;
      throw predictionSupervisorError("PREDICTION_NO_PROGRESS_EXCEEDED", 0);
    }
    while (true) {
      try {
        const result = await runPredictionAttempt(
          predictionTools,
          forceTool,
          progressLabel,
          effort,
          recoveryThinkingEnabled,
        );
        if (nextCheckpoint.reasoningFallback) {
          nextCheckpoint.reasoningFallback = {
            ...nextCheckpoint.reasoningFallback,
            status: "completed",
            effort,
            completedAt: isoNow(),
          };
        }
        return result;
      } catch (error: any) {
        if (String(error?.code || "") !== "PREDICTION_NO_PROGRESS_EXCEEDED") throw error;
        if (!recoveryThinkingEnabled) throw error;
        const index = effortOrder.indexOf(effort);
        if (index < 0) throw error;
        if (index >= effortOrder.length - 1) {
          // A second prediction at the immutable Low floor is not a recovery:
          // local backends may consume the entire budget before serializing a
          // forced call, even with thinking disabled.  Commit one deterministic
          // implementation-evidence read derived from an observed Unreal header
          // instead.  If no such transition is provable, persist the bounded
          // intent and fail closed without asking the model to guess a path.
          const recovered = await emitServerOwnedFloorRecovery();
          if (recovered) return recovered;
          nextCheckpoint.reasoningFallback = {
            version: 1,
            status: "retrying",
            configuredEffort,
            effort,
            reason: "semantic_no_progress_at_effort_floor",
            attempts: Math.max(0, Number(nextCheckpoint?.reasoningFallback?.attempts || 0)) + 1,
            sameEffortRetries: 0,
            toolChoiceForced: false,
            thinkingSuppressed: false,
            serverOwnedTransition: true,
            recoveryStrategy: floorRecoveryStrategy,
            updatedAt: isoNow(),
          };
          await persistCheckpoint(
            sessionId,
            nextCheckpoint,
            requireCheckpointPersistence,
            "reasoning_effort_floor_transition_unavailable",
          );
          await appendEventBestEffort(sessionId, {
            type: "reasoning_effort_floor_transition_unavailable",
            at: isoNow(),
            effort,
            reason: "semantic_no_progress_at_effort_floor",
            modelRetryStarted: false,
            recoveryStrategy: floorRecoveryStrategy,
          });
          throw error;
        }
        const nextEffort = effortOrder[index + 1];
        nextCheckpoint.reasoningFallback = {
          version: 1,
          status: "downgraded",
          configuredEffort,
          effort: nextEffort,
          reason: "semantic_no_progress",
          attempts: Math.max(0, Number(nextCheckpoint?.reasoningFallback?.attempts || 0)) + 1,
          updatedAt: isoNow(),
        };
        await persistCheckpoint(
          sessionId,
          nextCheckpoint,
          requireCheckpointPersistence,
          "reasoning_effort_downgraded",
        );
        await appendEventBestEffort(sessionId, {
          type: "reasoning_effort_downgraded",
          at: isoNow(),
          fromEffort: effort,
          toEffort: nextEffort,
          reason: "semantic_no_progress",
        });
        effort = nextEffort;
      }
    }
  };
  const unsafeStopReasons = new Set(["contextLengthReached", "failed", "modelUnloaded"]);
  const predictionTruncated = (reason: string): boolean => (
    unsafeStopReasons.has(reason)
    || (Boolean(config.rejectTruncatedPredictions) && reason === "maxPredictedTokensReached")
  );
  const recordPredictionCompletion = async (
    reason: string,
    recoveryKind = "",
  ): Promise<void> => {
    const truncatedPrediction = predictionTruncated(reason);
    const outputDigest = predictionOutputDigest(events, requests);
    nextCheckpoint.predictionState = mergeLifecycleState(
      nextCheckpoint.predictionState,
      lifecycleState(
        nextCheckpoint,
        truncatedPrediction ? "pending" : "completed",
        { outputDigest, stopReason: reason || "unspecified" },
      ),
    );
    if (nextCheckpoint.synthesisState && requests.length === 0) {
      nextCheckpoint.synthesisState = mergeLifecycleState(
        nextCheckpoint.synthesisState,
        lifecycleState(
          nextCheckpoint,
          truncatedPrediction ? "pending" : "completed",
          { outputDigest, stopReason: reason || "unspecified" },
        ),
      );
    }
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      truncatedPrediction ? "prediction_incomplete" : "prediction_completed",
    );
    await appendEventBestEffort(sessionId, {
      type: "prediction_completion",
      at: new Date().toISOString(),
      stopReason: reason || "unspecified",
      bufferedEventCount: events.length,
      streamedReasoningEventCount,
      reasoningTokensObserved,
      finalTokensObserved,
      toolJsonCharactersObserved,
      predictionHeartbeatCount,
      toolRequestCount: requests.length,
      outputCommitted: false,
      outputCommitPending: !truncatedPrediction,
      recoveryAttempt: Boolean(recoveryKind),
      recoveryKind,
      architectureFinalRecoveryAttempt: recoveryKind.startsWith("architecture_"),
      hostToolCallLifecycle: hostToolCallLifecycleSnapshot(),
      hostToolCallLifecycleDiagnostics: hostToolCallLifecycle.diagnostics().slice(-64),
    });
  };

  let stopReason = "";
  if (phaseServerOwnedDirectCall) {
    const phaseToolName = String(
      phaseControlDefinition?.function?.name || phaseControlDefinition?.name || "",
    );
    const rawRequest = {
      id: directToolCallRequestId("server-control", phaseControlKind),
      type: "function",
      name: phaseToolName,
      arguments: mergeServerOwnedArguments(
        {},
        phaseControlDefinition?.__serverOwnedInjectedArgs || {},
      ),
    };
    const request = enrichToolRequestControl(
      rawRequest,
      sessionId,
      nextCheckpoint,
      authoritativeGoal,
      modelFacingToolDefinitions,
    );
    const callId = 0;
    const phaseSource = toolNamesMatch(TASK_PLANNER_TOOL_NAME, phaseToolName)
      || /planner|bootstrap/i.test(String(phaseControlKind || ""))
      ? "planner/bootstrap_direct"
      : "server_owned_required_tool";
    const hostToolCallId = recordDirectToolCallLifecycle(request, callId, phaseSource);
    requests.push({ callId, request, hostToolCallId });
    stopReason = `server_owned_${phaseControlKind}`;
    await appendEventBestEffort(sessionId, {
      type: "server_control_tool_direct_emitted",
      at: new Date().toISOString(),
      phase: phaseControlKind,
      tool: phaseToolName,
      modelSerializationBypassed: true,
    });
    await recordPredictionCompletion(stopReason, phaseControlKind);
  } else if (exactServerOwnedDirectCall) {
    // There is no model choice left: the server owns the exact tool and every
    // schema-required argument.  Inject it directly so compact models cannot
    // waste a turn serializing, correcting, or retrying an already-authorized
    // request.  The normal control-plane validation below still guards commit.
    const rawRequest = {
      id: directToolCallRequestId(
        "server-owned",
        String(nextCheckpoint?.controlEpoch || "0"),
      ),
      type: "function",
      name: String(exactRequiredToolDefinition?.function?.name || exactRequiredToolName),
      arguments: mergeServerOwnedArguments(
        {},
        exactRequiredToolDefinition?.__serverOwnedInjectedArgs || {},
      ),
    };
    const request = enrichToolRequestControl(
      rawRequest,
      sessionId,
      nextCheckpoint,
      authoritativeGoal,
      modelFacingToolDefinitions,
    );
    const callId = 0;
    const hostToolCallId = recordDirectToolCallLifecycle(
      request,
      callId,
      "server_owned_required_tool",
    );
    requests.push({ callId, request, hostToolCallId });
    stopReason = "server_owned_direct_tool";
    await appendEventBestEffort(sessionId, {
      type: "server_required_tool_direct_emitted",
      at: new Date().toISOString(),
      requiredTool: exactRequiredToolName,
      taskSessionId: String(nextCheckpoint?.taskRouteOwnership?.taskSessionId || ""),
    });
    await recordPredictionCompletion(stopReason, "server_owned_direct_tool");
  } else {
    stopReason = await runPrediction(
      modelFacingToolDefinitions,
      architectureToolForced
        || featureIntentEvidenceRefillActive
        || initialActiveProjectBootstrapForced
        || preRoutePlannerForced
        || catalogRefreshForced
        || exactRequiredToolForced,
      architectureToolForced
        ? "Architecture validation"
        : (architectureEvidenceRefillActive || featureIntentEvidenceRefillActive
          ? "Source evidence analysis"
          : (exactRequiredToolForced
            ? `Running: ${exactRequiredToolName}`
            : "Model reasoning")),
    );
    await recordPredictionCompletion(stopReason);
  }
  if (stopReason === "maxPredictedTokensReached" && Boolean(config.rejectTruncatedPredictions)) {
    const discardedEventCount = events.length;
    const discardedToolCount = requests.length;
    events.length = 0;
    requests.length = 0;
    nextCheckpoint.outputRecovery = {
      version: 1,
      status: "retrying",
      attempt: 1,
      reason: "max_predicted_tokens_reached",
      bufferedOutputDiscarded: true,
      discardedEventCount,
      discardedToolCount,
      objectiveHash: String(nextCheckpoint?.objectiveHash || ""),
      taskSessionId: String(
        nextCheckpoint?.serverControl?.taskSessionId
        || nextCheckpoint?.taskRouteOwnership?.taskSessionId
        || "",
      ),
      controlEpoch: nextCheckpoint?.serverControl?.epoch ?? null,
      mutationGeneration: Number(nextCheckpoint?.mutationGeneration || 0),
      recoveryStrategy: "bounded_low_effort_retry",
      updatedAt: isoNow(),
    };
    nextCheckpoint.predictionState = lifecycleState(nextCheckpoint, "pending", {
      stopReason: "max_predicted_tokens_recovery",
    });
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "max_output_recovery_prepared",
    );
    await appendEventBestEffort(sessionId, {
      type: "max_output_recovery_started",
      at: isoNow(),
      attempt: 1,
      bufferedOutputDiscarded: true,
      discardedEventCount,
      discardedToolCount,
    });
    upsertLeadingSystemRule(
      modelChat,
      "[MAX_OUTPUT_RECOVERY]",
      "[MAX_OUTPUT_RECOVERY]\nReturn only the shortest complete final answer required to preserve verified coverage. "
      + "Do not repeat background, do not add speculative detail, and do not emit any tool except the exact "
      + "server-required tool if one is forced.",
    );
    const recoveryTools = exactRequiredToolForced && exactRequiredToolDefinition
      ? [exactRequiredToolDefinition]
      : [];
    stopReason = await runPrediction(
      recoveryTools,
      Boolean(exactRequiredToolForced),
      "Output completion recovery",
      { effort: "low", thinkingEnabled: false },
    );
    await recordPredictionCompletion(stopReason, "max_output_once");
    if (stopReason === "maxPredictedTokensReached") {
      events.length = 0;
      requests.length = 0;
      nextCheckpoint.coverageIncomplete = true;
      nextCheckpoint.outputRecovery = {
        ...nextCheckpoint.outputRecovery,
        status: "partial_report_emitted",
        attempt: 2,
        updatedAt: isoNow(),
      };
      const remainingFrontier = Array.isArray(nextCheckpoint?.remainingFrontier)
        ? nextCheckpoint.remainingFrontier.slice(0, 32)
        : [];
      const partial = [
        "출력 한도에 두 번 도달하여 생성된 부분 출력을 원자적으로 폐기했습니다.",
        "이 응답은 완료 보고가 아닙니다. 검증된 task/objective와 체크포인트는 보존되어 있습니다.",
        remainingFrontier.length
          ? `남은 범위: ${remainingFrontier.map((entry: any) => String(entry)).join(", ")}`
          : "남은 범위는 체크포인트의 evidence/frontier에서 이어서 처리해야 합니다.",
        "다음 실행은 기존 task를 이어받아 짧은 단위로 합성해야 합니다.",
      ].join("\n");
      await appendEventBestEffort(sessionId, {
        type: "max_output_recovery_exhausted",
        at: isoNow(),
        attempts: 2,
        coverageIncomplete: true,
        remainingFrontier,
      });
      await emitDurableDeterministicFinal(partial, "max_output_partial_report");
      return;
    }
    nextCheckpoint.outputRecovery = {
      ...nextCheckpoint.outputRecovery,
      status: "completed",
      attempt: 2,
      completedAt: isoNow(),
    };
  }
  if (predictionTruncated(stopReason)) {
    const safelyBuffered = toolControlPlaneEnforced || bufferUntilPredictionComplete;
    throw new Error(
      `Model prediction was discarded because it did not complete safely (stopReason=${stopReason}). `
      + (safelyBuffered
        ? "No buffered final text or tool call was committed; transient reasoning progress may already be visible. Compact the context or increase the model context/output limit."
      : "Atomic output was explicitly disabled, so already-streamed output may be partial. Enable atomic output before retrying."),
    );
  }
  const exactRequiredCallComplete = (): boolean => Boolean(
    exactRequiredToolForced
    && requests.length === 1
    && toolNamesMatch(exactRequiredToolName, requestedToolName(requests[0]?.request))
    && core.toolArgumentsSatisfy(
      nextCheckpoint?.requiredNextTool?.args,
      requests[0]?.request?.arguments,
    )
  );
  if (exactRequiredToolForced && !exactServerOwnedDirectCall && !exactRequiredCallComplete()) {
    const rejectedToolNames = requests
      .map((entry) => requestedToolName(entry.request))
      .filter(Boolean);
    await appendEventBestEffort(sessionId, {
      type: "server_required_tool_repair_started",
      at: new Date().toISOString(),
      requiredTool: exactRequiredToolName,
      receivedTools: rejectedToolNames,
      priorToolRequestCount: requests.length,
    });
    events.length = 0;
    requests.length = 0;
    injectServerRequiredToolRepairRule(modelChat, exactRequiredToolName, rejectedToolNames);
    stopReason = await runPrediction(modelFacingToolDefinitions, true);
    await recordPredictionCompletion(stopReason, "required_tool_serialization");
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Required-tool repair was discarded because it did not complete safely (stopReason=${stopReason}).`,
      );
    }
    const repaired = exactRequiredCallComplete();
    await appendEventBestEffort(sessionId, {
      type: repaired
        ? "server_required_tool_repair_completed"
        : "server_required_tool_repair_failed",
      at: new Date().toISOString(),
      requiredTool: exactRequiredToolName,
      receivedTools: requests.map((entry) => requestedToolName(entry.request)).filter(Boolean),
      toolRequestCount: requests.length,
    });
    if (!repaired) {
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "server_required_tool_repair_failed",
      );
      throw new Error(
        `The model did not serialize exactly one ${exactRequiredToolName} call after one bounded repair. `
        + "No generated tool call was committed.",
      );
    }
  }
  const incompleteFeatureIntentPaths = featureCompletionAuditRequired
    ? [...new Set(requests.flatMap((entry) => (
      featureIntentPayloadViolationPaths(entry.request, featureIntentContractTool)
    )))]
    : [];
  if (incompleteFeatureIntentPaths.length > 0) {
    await appendEventBestEffort(sessionId, {
      type: "feature_intent_payload_repair_started",
      at: new Date().toISOString(),
      missingRequiredPaths: incompleteFeatureIntentPaths,
      priorToolRequestCount: requests.length,
    });
    events.length = 0;
    requests.length = 0;
    injectFeatureIntentPayloadRepairRule(modelChat, incompleteFeatureIntentPaths);
    stopReason = await runPrediction(
      [featureIntentModelFacingTool].filter(Boolean),
      true,
      "Recovery: Feature Intent payload",
    );
    await recordPredictionCompletion(stopReason, "feature_intent_payload");
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Feature Intent payload repair was discarded because it did not complete safely (stopReason=${stopReason}).`,
      );
    }
    const repairedFeatureRequests = requests.filter((entry) => (
      toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requestedToolName(entry.request))
    ));
    const remainingFeatureViolations = [...new Set(repairedFeatureRequests.flatMap((entry) => (
      featureIntentPayloadViolationPaths(entry.request, featureIntentContractTool)
    )))];
    const repaired = repairedFeatureRequests.length === 1 && remainingFeatureViolations.length === 0;
    await appendEventBestEffort(sessionId, {
      type: repaired
        ? "feature_intent_payload_repair_completed"
        : "feature_intent_payload_repair_failed",
      at: new Date().toISOString(),
      missingRequiredPaths: remainingFeatureViolations,
      toolRequestCount: requests.length,
    });
    if (!repaired) {
      nextCheckpoint.requiredNextTool = null;
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "feature_intent_payload_repair_failed",
      );
      throw new Error(
        "Feature Intent output was discarded after one bounded payload repair because required JSON-schema "
        + `paths are still missing: ${(remainingFeatureViolations.length
          ? remainingFeatureViolations
          : incompleteFeatureIntentPaths).join(", ")}.`,
      );
    }
  }
  const producedFeatureIntentRequest = requests.find((entry) => (
    toolNamesMatch(FEATURE_INTENT_TOOL_NAME, requestedToolName(entry.request))
  ));
  const producedFeatureTargets = producedFeatureIntentRequest
    ? featureIntentRequestedTargetFiles(producedFeatureIntentRequest.request)
    : [];
  const knownAbsentFeatureTargets = knownAbsentFeatureIntentTargetFiles(
    messages,
    producedFeatureTargets,
  );
  const unreadFeatureTargets = producedFeatureIntentRequest
    ? unreadFeatureIntentTargetFiles(
      producedFeatureIntentRequest.request,
      architectureStatus.directSourceFileEvidencePaths,
      knownAbsentFeatureTargets,
    )
    : [];
  if (producedFeatureIntentRequest && knownAbsentFeatureTargets.length > 0) {
    await appendEventBestEffort(sessionId, {
      type: "feature_intent_new_target_absence_proven",
      at: new Date().toISOString(),
      targetFiles: knownAbsentFeatureTargets,
      evidence: "complete_exact_basename_search_zero_matches",
    });
  }
  if (unreadFeatureTargets.length > 0) {
    const requiredPath = unreadFeatureTargets[0];
    await appendEventBestEffort(sessionId, {
      type: "feature_intent_target_evidence_recovery_started",
      at: new Date().toISOString(),
      unreadTargetFiles: unreadFeatureTargets,
      selectedReadPath: requiredPath,
    });
    events.length = 0;
    requests.length = 0;
    nextCheckpoint.requiredNextTool = null;
    injectFeatureIntentRecoveryRule(modelChat, {
      kind: "read_selected_target",
      requiredReads: unreadFeatureTargets,
      nextTool: "read_file",
      nextToolArgs: { path: requiredPath },
    });
    const directReadTools = contractAwareToolDefinitions.filter((tool: any) => (
      DIRECT_SOURCE_FILE_TOOLS.some((name) => toolNamesMatch(
        name,
        tool?.function?.name || tool?.name || "",
      ))
    ));
    stopReason = await runPrediction(directReadTools, true, "Reading selected target source");
    await recordPredictionCompletion(stopReason, "feature_intent_target_evidence");
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Feature target evidence recovery was discarded because it did not complete safely (stopReason=${stopReason}).`,
      );
    }
    const requestedReadEntry = requests.length === 1 ? requests[0] : null;
    const requestedRead = requestedReadEntry?.request || null;
    const requestedReadName = requestedToolName(requestedRead);
    const modelRequestedReadPath = normalizeProjectSourcePath(
      requestedRead?.arguments?.path || requestedRead?.arguments?.filePath || "",
    );
    const directReadToolProduced = Boolean(
      requestedRead
      && DIRECT_SOURCE_FILE_TOOLS.some((name) => toolNamesMatch(name, requestedReadName))
    );
    let serverBoundPath = false;
    if (
      directReadToolProduced
      && modelRequestedReadPath !== normalizeProjectSourcePath(requiredPath)
    ) {
      // The server-owned Feature Intent request already identified the exact
      // unread target.  Asking the target model to copy that path is only a
      // serialization step, not a semantic choice.  Bind the one allowed
      // direct-read request to the authoritative path so a nearby header or
      // similarly named file cannot turn a recoverable read into a terminal
      // GUI failure.
      const reboundArguments = {
        ...(requestedRead.arguments && typeof requestedRead.arguments === "object"
          ? requestedRead.arguments
          : {}),
        path: requiredPath,
      };
      delete (reboundArguments as any).filePath;
      requestedRead.arguments = reboundArguments;
      if (requestedReadEntry) {
        replaceBufferedArgumentFragments(
          events,
          requestedReadEntry.callId,
          reboundArguments,
        );
      }
      serverBoundPath = true;
    }
    const requestedReadPath = normalizeProjectSourcePath(
      requestedRead?.arguments?.path || requestedRead?.arguments?.filePath || "",
    );
    const targetReadReady = Boolean(
      directReadToolProduced
      && requestedReadPath === normalizeProjectSourcePath(requiredPath)
    );
    await appendEventBestEffort(sessionId, {
      type: targetReadReady
        ? "feature_intent_target_evidence_recovery_completed"
        : "feature_intent_target_evidence_recovery_failed",
      at: new Date().toISOString(),
      requiredPath,
      requestedTool: requestedReadName,
      modelRequestedPath: modelRequestedReadPath,
      requestedPath: requestedReadPath,
      serverBoundPath,
    });
    if (!targetReadReady) {
      requests.length = 0;
      events.length = 0;
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "feature_intent_target_evidence_recovery_failed",
      );
      throw new Error(
        `Feature Intent selected unread target ${requiredPath}, but the bounded recovery did not serialize an equivalent direct-source read.`,
      );
    }
    deferredRequiredNextTool = {
      name: FEATURE_INTENT_TOOL_NAME,
      reference: "feature_intent_target_evidence_resume",
      // Preserve the complete semantic request that selected this target.  On
      // the next LM Studio prediction the control plane injects these exact
      // arguments, so the model cannot silently choose a different feature or
      // target after the required read completes.
      args: mergeServerOwnedArguments(
        {},
        producedFeatureIntentRequest?.request?.arguments || {},
      ),
    };
    nextCheckpoint.featureIntentResume = {
      mode: "awaiting_target_read",
      args: mergeServerOwnedArguments(
        {},
        producedFeatureIntentRequest?.request?.arguments || {},
      ),
      observedResultCount: observedToolResultCount(
        messages,
        FEATURE_INTENT_TOOL_NAME,
      ),
      observedDiscoveryResultCount: observedToolResultCountForNames(
        messages,
        ARCHITECTURE_DISCOVERY_TOOLS,
      ),
    };
  }
  if (requiredToolGateActive && !architectureValidationRequired && requests.length === 0) {
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "server_required_tool_missing",
    );
    await appendEventBestEffort(sessionId, {
      type: "server_required_tool_missing",
      at: new Date().toISOString(),
      requiredTool: nextCheckpoint?.requiredNextTool?.name || "",
      protocolControl: nextCheckpoint?.protocolControl || null,
    });
    throw new Error(
      `Server control requires ${nextCheckpoint?.requiredNextTool?.name}; `
      + "the prose-only prediction was discarded without executing that tool.",
    );
  }
  if (preRoutePlannerForced && requests.length === 0) {
    await appendEventBestEffort(sessionId, {
      type: "pre_route_planner_missing",
      at: new Date().toISOString(),
      discoveryCalls: architectureStatus.discoveryCallsSinceLastAttempt,
      discoveryLimit: preRouteDiscoveryLimit,
    });
    throw new Error(
      "Bounded pre-route discovery completed, but the model did not call unreal_agent_plan. "
      + "The prose-only output was discarded to prevent another source-read loop.",
    );
  }
  if (initialActiveProjectBootstrapForced && requests.length === 0) {
    await appendEventBestEffort(sessionId, {
      type: "initial_active_project_bootstrap_missing",
      at: new Date().toISOString(),
    });
    throw new Error(
      "A write-capable task must begin with unreal_get_active_project, but the model returned prose only. "
      + "The output was discarded before route planning."
    );
  }
  if (catalogRefreshForced && requests.length === 0) {
    await appendEventBestEffort(sessionId, {
      type: "tool_catalog_refresh_call_missing",
      at: new Date().toISOString(),
      routeHash,
      expectedTool: String(nextCheckpoint?.catalogRefresh?.tool || ""),
    });
    throw new Error(
      "The bounded Unreal Agent tool-catalog refresh produced no control call. "
      + "The prose-only output was discarded before routed work could be misreported."
    );
  }
  const incompleteArchitecturePaths = requireCompleteArchitectureProposal
    ? [...new Set(requests.flatMap((entry) => (
      architecturePayloadViolationPaths(entry.request, architectureContractTool)
    )))]
    : [];
  if (incompleteArchitecturePaths.length > 0) {
    // Some smaller local models acknowledge the complete schema in reasoning but
    // omit required sections from the emitted tool arguments. Do not send that
    // malformed call to the MCP server and enter a result/retry loop. Give the
    // model one bounded serialization-only retry with missing JSON paths; values
    // still have to be derived by the model from retained source evidence.
    await appendEventBestEffort(sessionId, {
      type: "architecture_payload_repair_started",
      at: new Date().toISOString(),
      missingRequiredPaths: incompleteArchitecturePaths,
      priorToolRequestCount: requests.length,
    });
    events.length = 0;
    requests.length = 0;
    injectArchitecturePayloadRepairRule(modelChat, incompleteArchitecturePaths);
    stopReason = await runPrediction([architectureContractTool], true);
    await recordPredictionCompletion(stopReason, "architecture_payload");
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Forced architecture payload repair was discarded because it did not complete safely (stopReason=${stopReason}).`,
      );
    }
    const retryArchitectureRequests = requests.filter((entry) => (
      toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
    ));
    const remainingViolationPaths = [...new Set(retryArchitectureRequests.flatMap((entry) => (
      architecturePayloadViolationPaths(entry.request, architectureContractTool)
    )))];
    if (retryArchitectureRequests.length === 0 || remainingViolationPaths.length > 0) {
      nextCheckpoint.requiredNextTool = {
        name: ARCHITECTURE_TOOL_NAME,
        reference: "architecture_payload_repair_failed",
        args: {},
      };
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "architecture_payload_repair_failed",
      );
      await appendEventBestEffort(sessionId, {
        type: "architecture_payload_repair_failed",
        at: new Date().toISOString(),
        reason: retryArchitectureRequests.length === 0
          ? "validator_call_missing"
          : "required_schema_paths_still_missing",
        missingRequiredPaths: remainingViolationPaths,
      });
      throw new Error(
        "Architecture validator output was discarded after one bounded payload repair because required JSON-schema "
        + `paths are still missing: ${(remainingViolationPaths.length
          ? remainingViolationPaths
          : incompleteArchitecturePaths).join(", ")}.`,
      );
    }
    await appendEventBestEffort(sessionId, {
      type: "architecture_payload_repair_completed",
      at: new Date().toISOString(),
      repairedRequiredPaths: incompleteArchitecturePaths,
    });
  }
  let architectureRequestProduced = requests.some((entry) => (
    toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
  ));
  if (
    architectureValidationRequired
    && !architectureStatus.validated
    && requests.length === 0
    && !architectureRequestProduced
  ) {
    // A smaller local model can understand a rejected proposal in prose but
    // still try to end the turn instead of producing the mandatory replacement
    // payload. Discard that unvalidated text and make one bounded recovery
    // prediction in the same GUI turn with only the validator schema available.
    // The model still derives every proposal value; this supplies no design
    // answer and cannot execute project writes.
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_recovery_started",
      at: new Date().toISOString(),
      reason: "proposal_validation_missing",
      directEvidenceCount: architectureStatus.directEvidenceCount,
      proposalAttempted: architectureStatus.attempted,
    });
    events.length = 0;
    requests.length = 0;
    injectArchitectureSubmissionRule(modelChat);
    stopReason = await runPrediction([architectureContractTool], true);
    await recordPredictionCompletion(stopReason, "architecture_final");
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Forced architecture recovery was discarded because it did not complete safely (stopReason=${stopReason}).`
      );
    }
    architectureRequestProduced = requests.some((entry) => (
      toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
    ));
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_recovery_completed",
      at: new Date().toISOString(),
      stopReason: stopReason || "unspecified",
      architectureRequestProduced,
    });
  }
  if (
    architectureValidationRequired
    && !architectureStatus.validated
    && requests.length === 0
    && !architectureRequestProduced
  ) {
    nextCheckpoint.requiredNextTool = {
      name: ARCHITECTURE_TOOL_NAME,
      reference: "architecture_final_blocked",
      args: {},
    };
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "architecture_final_blocked",
    );
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_blocked",
      at: new Date().toISOString(),
      reason: "proposal_validation_missing",
      directEvidenceCount: architectureStatus.directEvidenceCount,
      proposalAttempted: architectureStatus.attempted,
    });
    throw new Error(
      "Architecture final output was discarded because unreal_architecture_reasoning has not returned "
      + "proposalValidation.ok=true. Retry this generation; the validator tool is now required.",
    );
  }

  let synthesisCommitCallId: number | null = null;
  let preparedSynthesisOutput = "";
  const synthesisCommitTool: any = toolDefinitions.find((tool: any) => toolNamesMatch(
    "unreal_task_commit_synthesis",
    tool?.function?.name || tool?.name || "",
  ));
  const synthesisFinality = authoritativeSynthesisFinality(serverControlV2, nextCheckpoint);
  const explicitSynthesisFinal = synthesisFinality.eligible;
  const synthesisCommitRequired = Boolean(
    !detachedSideQueryActive
    && serverControlV2Active
    && explicitSynthesisFinal
    && requests.length === 0
    && events.some((event: any) => (
      event?.kind === "fragment"
      && String(event?.opts?.reasoningType || "none") === "none"
      && String(event?.content || "").length > 0
    ))
  );
  const synthesisCommitEligible = Boolean(synthesisCommitRequired && synthesisCommitTool);
  if (synthesisCommitRequired && !synthesisCommitTool) {
    nextCheckpoint.synthesisState = mergeLifecycleState(
      nextCheckpoint.synthesisState,
      lifecycleState(nextCheckpoint, "completed", {
        outputDigest: predictionOutputDigest(events, requests),
        stopReason: "synthesis_commit_tool_unavailable",
      }),
    );
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "synthesis_commit_tool_unavailable",
    );
    await appendEventBestEffort(sessionId, {
      type: "synthesis_commit_blocked",
      at: isoNow(),
      reason: "commit_tool_unavailable",
      finality: synthesisFinality.reason,
    });
    throw new Error(
      "Read-only synthesis output was not emitted because unreal_task_commit_synthesis is missing from the active tool catalog. Refresh the MCP tool catalog and retry the same task.",
    );
  }
  const synthesisTextPresent = events.some((event: any) => (
    event?.kind === "fragment"
    && String(event?.opts?.reasoningType || "none") === "none"
    && String(event?.content || "").length > 0
  ));
  const invalidReadOnlySynthesisFinal = Boolean(
    !detachedSideQueryActive
    && serverControlV2Active
    && (
      String(serverControlV2?.taskMode || "").trim().toLowerCase() === "read_only"
      || String(serverControlV2?.phase || "").trim().toLowerCase() === "synthesis"
    )
    && !explicitSynthesisFinal
    && !serverControlV2?.requiredTool
    && requests.length === 0
    && synthesisTextPresent
  );
  if (invalidReadOnlySynthesisFinal) {
    nextCheckpoint.synthesisState = lifecycleState(nextCheckpoint, "evidence_recovery", {
      outputDigest: predictionOutputDigest(events, requests),
      stopReason: `synthesis_finality_blocked_${synthesisFinality.reason}`,
    });
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "synthesis_finality_blocked",
    );
    await appendEventBestEffort(sessionId, {
      type: "synthesis_finality_blocked",
      at: isoNow(),
      reason: synthesisFinality.reason,
      outputWithheld: true,
    });
    throw new Error(
      `Read-only final output was withheld because the authoritative server phase is not a valid synthesis latch (${synthesisFinality.reason}).`,
    );
  }
  if (synthesisCommitEligible) {
    preparedSynthesisOutput = events
      .filter((event: any) => (
        event?.kind === "fragment"
        && String(event?.opts?.reasoningType || "none") === "none"
      ))
      .map((event: any) => String(event?.content || ""))
      .join("");
    const reportValidation = core.validateSynthesisReport(
      preparedSynthesisOutput,
      serverControlV2?.synthesisReadiness,
    );
    if (!reportValidation.ok) {
      throw new Error(
        `Synthesis output violated the server-owned evidence/report contract (${reportValidation.reason}); output was discarded before commit.`,
      );
    }
    if (preparedSynthesisOutput.length > 131_072) {
      throw new Error(
        "Synthesis output exceeds the durable exactly-once delivery bound; output was discarded before commit.",
      );
    }
    const outputDigest = core.sha256(preparedSynthesisOutput);
    const taskSessionId = String(serverControlV2?.taskSessionId || "").trim();
    const controlEpoch = Number(serverControlV2?.epoch);
    const controlFingerprint = String(
      serverControlV2?.controlFingerprint || "",
    ).trim();
    const mutationGeneration = Math.max(0, Number(nextCheckpoint?.mutationGeneration || 0));
    const synthesisTransactionId = core.sha256(core.stableStringify({
      taskSessionId,
      objectiveHash: String(nextCheckpoint?.objectiveHash || ""),
      controlEpoch,
      controlFingerprint,
      mutationGeneration,
      synthesisEvidenceBundleHash: String(
        serverControlV2?.synthesisReadiness?.synthesisEvidenceBundleHash || "",
      ).trim().toLowerCase(),
      outputDigest,
    }));
    const rawRequest = {
      id: `synthesis-commit-${synthesisTransactionId.slice(0, 32)}`,
      type: "function",
      name: String(
        synthesisCommitTool?.function?.name
        || synthesisCommitTool?.name
        || "unreal_task_commit_synthesis",
      ),
      arguments: {
        objectiveHash: String(nextCheckpoint?.objectiveHash || ""),
        controlEpoch,
        controlFingerprint,
        mutationGeneration,
        synthesisEvidenceBundleHash: String(
          serverControlV2?.synthesisReadiness?.synthesisEvidenceBundleHash || "",
        ).trim().toLowerCase(),
        outputDigest,
        synthesisTransactionId,
      },
    };
    const request = enrichToolRequestControl(
      rawRequest,
      sessionId,
      nextCheckpoint,
      authoritativeGoal,
      toolDefinitions,
    );
    const durablePreparedSynthesis = preparedSynthesisRecord(
      nextCheckpoint,
      preparedSynthesisOutput,
      outputDigest,
      synthesisTransactionId,
      serverControlV2,
    );
    if (!core.compactPreparedSynthesis(durablePreparedSynthesis)) {
      throw new Error(
        "Synthesis output could not be represented by the durable identity envelope; commit was withheld.",
      );
    }
    nextCheckpoint.preparedSynthesis = durablePreparedSynthesis;
    nextCheckpoint.synthesisState = lifecycleState(nextCheckpoint, "prepared", {
      outputDigest,
      stopReason: "synthesis_commit_prepared",
    });
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "synthesis_prepared_durable",
    );
    synthesisCommitCallId = Math.max(
      1_000_000,
      ...requests.map((entry: any) => Number(entry?.callId || 0) + 1),
    );
    const hostToolCallId = recordDirectToolCallLifecycle(
      request,
      synthesisCommitCallId,
      "synthesis_commit",
    );
    requests.push({
      callId: synthesisCommitCallId,
      request,
      hostToolCallId,
      serverOwnedSynthesisCommit: true,
    });
    await appendEventBestEffort(sessionId, {
      type: "synthesis_commit_prepared",
      at: isoNow(),
      taskSessionId,
      controlEpoch,
      outputDigest,
      synthesisTransactionId,
      toolCallId: request.id,
    });
  }

  const verdictByCallId = new Map<number, { ok: boolean; reason?: string }>();
  for (const entry of requests) {
    const requestedName = requestedToolName(entry.request);
    const requestArguments = entry.request?.arguments && typeof entry.request.arguments === "object"
      ? entry.request.arguments
      : {};
    const missingRequiredProposal = Boolean(
      requireCompleteArchitectureProposal
      && toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedName)
      && (
        !requestArguments.proposal
        || typeof requestArguments.proposal !== "object"
        || Array.isArray(requestArguments.proposal)
      )
    );
    const architectureToolRejected = architectureToolForced
      && !toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedName);
    const activeProjectBootstrapRejected = initialActiveProjectBootstrapForced
      && !toolNamesMatch("unreal_get_active_project", requestedName);
    const plannerToolRejected = preRoutePlannerForced
      && !toolNamesMatch(TASK_PLANNER_TOOL_NAME, requestedName);
    const sourceAnalysisAdmissionRejected = sourceAnalysisAdmissionRequired
      && !toolNamesMatch(TASK_PLANNER_TOOL_NAME, requestedName)
      && !toolNamesMatch("unreal_get_active_project", requestedName);
    const nonDurablePlannerRejected = nonDurablePlannerSuppressionRequired
      && toolNamesMatch(TASK_PLANNER_TOOL_NAME, requestedName);
    const catalogRefreshToolRejected = catalogRefreshForced
      && !toolNamesMatch(String(nextCheckpoint?.catalogRefresh?.tool || ""), requestedName);
    const discoveryToolRejected = architectureEvidenceRefillActive
      && !architectureDiscoveryToolAllowed(requestedName);
    const semanticToolRejected = !exactSemanticCallBlockingActive && semanticForbiddenTools.some(
      (forbidden: string) => toolNamesMatch(forbidden, requestedName),
    );
    const semanticCallFingerprint = exactSemanticCallBlockingActive
      ? core.toolCallFingerprint(requestedName, requestArguments)
      : "";
    const semanticCallRejected = semanticForbiddenCallFingerprints.includes(
      String(semanticCallFingerprint || "").toLowerCase(),
    );
    let ordinaryVerdict: any = { ok: true };
    if (semanticCallRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `semantic blocker forbids this exact ${requestedName || "<unnamed>"} call fingerprint; corrected arguments remain allowed`,
      };
    } else if (semanticToolRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `semantic blocker forbids ${requestedName || "<unnamed>"}; errorCode=${nextCheckpoint?.semanticBlocker?.errorCode || "BLOCKED"}`,
      };
    } else if (activeProjectBootstrapRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Initial bootstrap expected unreal_get_active_project, got ${requestedName || "<unnamed>"}.`,
      };
    } else if (catalogRefreshToolRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Tool-catalog synchronization expected ${nextCheckpoint?.catalogRefresh?.tool}; got ${requestedName || "<unnamed>"}.`,
      };
    } else if (plannerToolRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Pre-route discovery is complete; expected ${TASK_PLANNER_TOOL_NAME}, got ${requestedName || "<unnamed>"}.`,
      };
    } else if (sourceAnalysisAdmissionRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Durable source-analysis admission only allows ${TASK_PLANNER_TOOL_NAME} or unreal_get_active_project before task ownership; got ${requestedName || "<unnamed>"}.`,
      };
    } else if (nonDurablePlannerRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `${sourceWorkIntent.category} is non-durable and must not start ${TASK_PLANNER_TOOL_NAME}.`,
      };
    } else if (architectureToolRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Architecture submission is required; expected ${ARCHITECTURE_TOOL_NAME}, got ${requestedName || "<unnamed>"}.`,
      };
    } else if (discoveryToolRejected) {
      ordinaryVerdict = {
        ok: false,
        reason: `Architecture evidence refill only allows direct-source discovery or ${ARCHITECTURE_TOOL_NAME}; received ${requestedName || "<unnamed>"}.`,
      };
    } else if (missingRequiredProposal) {
      ordinaryVerdict = {
        ok: false,
        reason: "Architecture submission is required; the validator call must include one complete proposal object.",
      };
    } else if (toolControlPlaneEnforced) {
      ordinaryVerdict = validateToolRequest(entry.request, nextCheckpoint);
    }
    const verdict = detachedSideQueryActive
      ? (detachedSideQueryToolAllowed(requestedName)
        ? { ok: true }
        : {
          ok: false,
          reason: `Detached read-only side query forbids ${requestedName || "<unnamed>"}.`,
        })
      : ordinaryVerdict;
    verdictByCallId.set(entry.callId, verdict);
    if (!verdict.ok) {
      await appendEventBestEffort(sessionId, {
        type: "tool_call_rejected",
        at: new Date().toISOString(),
        request: entry.request,
        reason: verdict.reason,
      });
    }
  }

  const acceptedRequests = requests.filter((entry) => verdictByCallId.get(entry.callId)?.ok !== false);
  const synthesisCommitAccepted = Boolean(
    synthesisCommitCallId !== null
    && verdictByCallId.get(synthesisCommitCallId)?.ok !== false
  );
  if (deferredRequiredNextTool && acceptedRequests.length === 1) {
    nextCheckpoint.requiredNextTool = deferredRequiredNextTool;
    await appendEventBestEffort(sessionId, {
      type: "feature_intent_resume_locked",
      at: new Date().toISOString(),
      requiredTool: FEATURE_INTENT_TOOL_NAME,
      targetFiles: featureIntentRequestedTargetFiles(
        producedFeatureIntentRequest?.request,
      ),
      recoveryRead: acceptedRequests[0]?.request?.arguments?.path || "",
    });
  }
  if (acceptedRequests.length > 0) {
    const observedResults = core.snapshotMessages(messages)
      .flatMap((message: any) => message.toolResults || []);
    const observedToolResultCount = observedResults.length;
    const observedAnonymousToolResultCount = observedResults
      .filter((result: any) => !result.toolCallId).length;
    let anonymousRequestOffset = 0;
    nextCheckpoint.pendingToolCall = null;
    nextCheckpoint.pendingToolCalls = acceptedRequests.map((entry) => {
      const pending = {
        ...entry.request,
        hostToolCallId: String(entry.hostToolCallId || entry.request?.id || "").slice(0, 240),
        observedToolResultCount,
        callbackCallId: entry.callId,
        dispatchState: "prepared",
        callFingerprint: core.toolCallFingerprint(
          requestedToolName(entry.request),
          entry.request?.arguments || {},
        ),
        preparedAt: isoNow(),
      } as any;
      if (entry.serverOwnedSynthesisCommit) {
        pending.preparedSynthesisOutput = preparedSynthesisOutput;
        pending.preparedSynthesisOutputDigest = String(
          entry.request?.arguments?.outputDigest || "",
        );
      }
      if (!entry.request?.id) {
        pending.observedAnonymousToolResultCount = observedAnonymousToolResultCount + anonymousRequestOffset;
        anonymousRequestOffset += 1;
      }
      return pending;
    });
    const lowFloorTransitionPrepared = Boolean(
      stopReason === "server_owned_low_floor_recovery"
      && nextCheckpoint?.reasoningFallback?.status === "retrying"
      && nextCheckpoint?.reasoningFallback?.recoveryStrategy
        === "execute_exact_canonical_control"
    );
    if (lowFloorTransitionPrepared) {
      nextCheckpoint.reasoningFallback = {
        ...nextCheckpoint.reasoningFallback,
        status: "completed",
        completedAt: isoNow(),
      };
    }
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "pending_tool_calls",
    );
    if (lowFloorTransitionPrepared) {
      const preparedRequest = acceptedRequests[0]?.request;
      await appendEventBestEffort(sessionId, {
        type: "reasoning_effort_floor_direct_transition_prepared",
        at: isoNow(),
        tool: requestedToolName(preparedRequest),
        path: String(preparedRequest?.arguments?.path || ""),
        toolCallId: String(preparedRequest?.id || ""),
        modelSerializationBypassed: true,
        outputPrepared: true,
            recoveryStrategy: "execute_exact_canonical_control",
      });
    }
  }

  if (acceptedRequests.length > 0) {
    for (const pending of nextCheckpoint.pendingToolCalls || []) {
      pending.dispatchState = "emitting";
      pending.dispatchIntentAt = isoNow();
      pending.dispatchAttempt = Math.max(0, Number(pending.dispatchAttempt || 0)) + 1;
    }
    if (
      synthesisCommitAccepted
      && nextCheckpoint?.preparedSynthesis
      && typeof nextCheckpoint.preparedSynthesis === "object"
    ) {
      nextCheckpoint.preparedSynthesis = {
        ...nextCheckpoint.preparedSynthesis,
        status: "commit_sent",
        dispatchedAt: isoNow(),
      };
    }
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "tool_call_dispatch_intent",
    );
  }

  for (const event of events) {
      if (synthesisCommitAccepted && event.kind === "fragment") {
        // Final text is durable in pendingToolCalls and is not delivered until
        // the authoritative task owner returns the exact digest-bound ACK.
        continue;
      }
      if (event.kind !== "end") {
        emitEvent(event);
        continue;
      }
      const verdict = verdictByCallId.get(event.callId) || { ok: true };
      if (verdict.ok) {
        emitEvent(event);
        const pending = (nextCheckpoint.pendingToolCalls || []).find((item: any) => (
          Number(item?.callbackCallId) === Number(event.callId)
        ));
        if (pending) {
          pending.dispatchState = "emitted";
          pending.dispatchedAt = isoNow();
          await persistCheckpoint(
            sessionId,
            nextCheckpoint,
            requireCheckpointPersistence,
            "tool_call_dispatched",
          );
        }
      } else {
        const replaced = hostToolCallLifecycle.replaceTerminalWithFailure(
          String(event.hostToolCallId || ""),
          `Tool call rejected by control plane: ${verdict.reason}`,
        );
        if (replaced) emitEvent(event);
        else emitEvent({
          ...event,
          kind: "failure",
          error: `Tool call rejected by control plane: ${verdict.reason}`,
        });
      }
  }
  const committedOutputDigest = predictionOutputDigest(events, acceptedRequests);
  const committedSynthesisDigest = synthesisCommitAccepted
    ? String(
      acceptedRequests.find((entry: any) => entry.callId === synthesisCommitCallId)
        ?.request?.arguments?.outputDigest
      || committedOutputDigest,
    )
    : committedOutputDigest;
  nextCheckpoint.predictionState = mergeLifecycleState(
    nextCheckpoint.predictionState,
    lifecycleState(nextCheckpoint, "committed", {
      outputDigest: committedOutputDigest,
      stopReason: stopReason || "unspecified",
    }),
  );
  if (
    nextCheckpoint.synthesisState
    && synthesisCommitAccepted
  ) {
    nextCheckpoint.synthesisState = mergeLifecycleState(
      nextCheckpoint.synthesisState,
      lifecycleState(nextCheckpoint, "commit_sent", {
        outputDigest: committedSynthesisDigest,
        stopReason: "awaiting_authoritative_commit_ack",
      }),
    );
  }
  await persistCheckpoint(
    sessionId,
    nextCheckpoint,
    requireCheckpointPersistence,
    "prediction_output_committed",
  );
  await appendEventBestEffort(sessionId, {
    type: "prediction_output_committed",
    at: new Date().toISOString(),
    stopReason: stopReason || "unspecified",
    emittedEventCount: events.length,
    streamedReasoningEventCount,
    reasoningTokensObserved,
    finalTokensObserved,
    toolJsonCharactersObserved,
    predictionHeartbeatCount,
    toolRequestCount: requests.length,
    outputCommitted: !synthesisCommitAccepted,
    synthesisCommitSent: synthesisCommitAccepted,
    hostToolCallLifecycle: hostToolCallLifecycleSnapshot(),
    hostToolCallLifecycleDiagnostics: hostToolCallLifecycle.diagnostics().slice(-64),
  });
}

function generationSingleFlightKey(ctl: GeneratorController, history: Chat): string {
  const messages = plainMessages(history);
  let workingDirectory = "";
  try {
    workingDirectory = String(ctl.getWorkingDirectory() || "");
  } catch {
    workingDirectory = "";
  }
  const envSessionId = String(process.env.LMS_CONTEXT_COMPACTOR_SESSION_ID || "").trim();
  const marker = core.extractSessionMarker(messages) || envSessionId;
  const conversationSessionId = core.lmStudioConversationSessionFingerprint(
    workingDirectory,
    "",
  );
  const primaryIdentity = conversationSessionId
    || marker
    || core.baseSessionKey(messages, workingDirectory);
  const auxiliaryMetaRequest = trailingMetaUserMessage(messages);
  return core.sha256(core.stableStringify({
    primaryIdentity,
    requestClass: auxiliaryMetaRequest ? "auxiliary" : "primary",
  }));
}

async function generate(ctl: GeneratorController, history: Chat): Promise<void> {
  const flightKey = generationSingleFlightKey(ctl, history);
  if (activeGenerationFlights.has(flightKey)) {
    const error: any = new Error(
      "GENERATION_ALREADY_ACTIVE: another generator invocation is already active for this conversation.",
    );
    error.code = "GENERATION_ALREADY_ACTIVE";
    error.errorCode = "GENERATION_ALREADY_ACTIVE";
    throw error;
  }
  const flight = generateUnlocked(ctl, history);
  activeGenerationFlights.set(flightKey, flight);
  try {
    await flight;
  } finally {
    if (activeGenerationFlights.get(flightKey) === flight) {
      activeGenerationFlights.delete(flightKey);
    }
  }
}

export {
  architectureGateStatus,
  createToolCallCallbackFsm,
  createHostToolCallTransactionManager,
  enrichToolRequestControl,
  captureModelFence,
  generate,
  injectFeatureIntentAtomicRule,
  injectPreRoutePlannerHandoffRule,
  injectServerRequiredToolRule,
  injectTaskRouteOwnershipRule,
  hasTargetBoundDirectSourcePair,
  networkedArchitectureContractRequired,
  modelFencesMatch,
  normalizeProjectSourcePath,
  predictionResultWithSupervision,
  initialPredictionNoProgressMs,
  requiresArchitectureValidation,
  requiresDurableInspectionPlanning,
  resolveCurrentTurnCapConfig,
  reconcilePendingToolCalls,
  serverControlReplayGuardMarker,
  successfulServerOwnedRequiredToolDidNotAdvance,
  compactionWorkflowProgressSignature,
  compactToTarget,
  resolveTargetModel,
  upsertLeadingSystemRule,
};
// End of module.
