"use strict";

function configuredIntervalMs(env = process.env) {
  const seconds = Number(env.MCP_PROGRESS_INTERVAL_SECONDS || 3);
  const safeSeconds = Number.isFinite(seconds) ? seconds : 3;
  return Math.round(Math.max(2, Math.min(5, safeSeconds)) * 1000);
}

function createProgressHeartbeat({
  toolName,
  progressToken,
  sendProgress,
  sendMessage,
  intervalMs = configuredIntervalMs(),
  now = () => Date.now(),
  schedule = setInterval,
  cancel = clearInterval,
} = {}) {
  const startedAt = now();
  let phase = `Running: ${String(toolName || "unknown")}`;
  let sequence = 0;
  let finished = false;

  const emit = (completed = false) => {
    if (finished && !completed) return;
    const elapsed = Math.max(0, now() - startedAt);
    const message = completed
      ? `${phase} completed · ${(elapsed / 1000).toFixed(1)}s elapsed`
      : `${phase} · ${Math.floor(elapsed / 1000)}s elapsed`;
    if (progressToken !== undefined && progressToken !== null) {
      sequence += 1;
      Promise.resolve(sendProgress?.({
        progressToken,
        progress: sequence,
        message,
      })).catch(() => undefined);
    } else if (!completed) {
      Promise.resolve(sendMessage?.(`[${toolName}] ${message}`)).catch(() => undefined);
    }
  };

  const timer = schedule(() => emit(false), Math.max(10, Number(intervalMs || 3000)));
  if (timer && typeof timer.unref === "function") timer.unref();
  if (progressToken !== undefined && progressToken !== null) emit(false);

  return {
    setPhase(nextPhase) {
      phase = String(nextPhase || phase);
      if (progressToken !== undefined && progressToken !== null) emit(false);
    },
    finish() {
      if (finished) return;
      finished = true;
      cancel(timer);
      if (progressToken !== undefined && progressToken !== null) emit(true);
    },
  };
}

module.exports = { configuredIntervalMs, createProgressHeartbeat };
