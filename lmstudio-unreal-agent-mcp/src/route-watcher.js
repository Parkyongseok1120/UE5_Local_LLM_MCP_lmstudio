"use strict";

function stableToolNames(value) {
  return [...new Set(
    (Array.isArray(value) ? value : [])
      .map((name) => String(name || "").trim())
      .filter(Boolean)
  )].sort();
}

function activeRouteFingerprint(context) {
  const value = context && typeof context === "object" ? context : {};
  const route = value.route && typeof value.route === "object" ? value.route : {};
  const state = value.state && typeof value.state === "object" ? value.state : {};
  const control = value.control && typeof value.control === "object"
    ? value.control
    : state.controlState && typeof state.controlState === "object"
      ? state.controlState
      : {};
  const requiredTool = control.requiredTool && typeof control.requiredTool === "object"
    ? control.requiredTool
    : {};
  const blocker = control.blocker && typeof control.blocker === "object"
    ? control.blocker
    : {};
  // routeHash is a route identity, not a proof that every effective control
  // field changed with it.  Include the actual tool/control projection so a
  // route-state transition cannot be missed by an advisory list-changed
  // notification merely because an older producer kept the same routeHash.
  return JSON.stringify({
    status: String(value.status || "none"),
    errorCode: String(value.errorCode || ""),
    taskSessionId: String(value.taskSessionId || state.taskSessionId || ""),
    routeHash: String(route.routeHash || ""),
    phase: String(route.phase || ""),
    routeTools: stableToolNames(route.activeTools),
    controlEpoch: Number(
      value.controlEpoch ?? state.controlEpoch ?? control.epoch ?? 0
    ) || 0,
    disposition: String(control.disposition || ""),
    allowedTools: stableToolNames(control.allowedTools),
    requiredTool: String(requiredTool.name || ""),
    blockerCode: String(blocker.code || ""),
    mutationGeneration: Number(
      value.mutationGeneration ?? state.mutationGeneration ?? control.mutationGeneration ?? 0
    ) || 0,
    controlFingerprint: String(control.fingerprint || state.controlFingerprint || ""),
  });
}

function startActiveRouteWatcher({
  readContext,
  notify,
  onNotifyError,
  intervalMs = 1000,
}) {
  if (typeof readContext !== "function" || typeof notify !== "function") {
    throw new TypeError("readContext and notify must be functions");
  }
  let stopped = false;
  let inFlight = false;
  let lastNotificationError = null;
  let notificationFailureCount = 0;
  let lastFingerprint = activeRouteFingerprint(readContext());

  async function poll() {
    if (stopped || inFlight) return false;
    inFlight = true;
    try {
      const context = readContext();
      const fingerprint = activeRouteFingerprint(context);
      if (fingerprint === lastFingerprint) return false;
      lastFingerprint = fingerprint;
      try {
        await notify(context, fingerprint);
        lastNotificationError = null;
      } catch (error) {
        // A client without list-changed support must not stop route enforcement,
        // but the degradation must be observable to its host.
        notificationFailureCount += 1;
        lastNotificationError = {
          code: "TOOLS_LIST_CHANGED_NOTIFY_FAILED",
          message: String(error?.message || error || "unknown notification failure").slice(0, 500),
        };
        if (typeof onNotifyError === "function") {
          try {
            onNotifyError(lastNotificationError, context, fingerprint);
          } catch {
            // Diagnostic hooks are never allowed to break route enforcement.
          }
        }
      }
      return true;
    } finally {
      inFlight = false;
    }
  }

  const timer = setInterval(() => {
    void poll();
  }, Math.max(50, Number(intervalMs) || 1000));
  if (typeof timer.unref === "function") timer.unref();

  return {
    poll,
    stop() {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
    },
    timer,
    get fingerprint() {
      return lastFingerprint;
    },
    get lastNotificationError() {
      return lastNotificationError && { ...lastNotificationError };
    },
    get notificationFailureCount() {
      return notificationFailureCount;
    },
  };
}

module.exports = {
  activeRouteFingerprint,
  startActiveRouteWatcher,
};
