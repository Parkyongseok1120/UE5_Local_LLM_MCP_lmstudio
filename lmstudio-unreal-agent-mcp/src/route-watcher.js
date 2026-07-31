"use strict";

function activeRouteFingerprint(context) {
  const value = context && typeof context === "object" ? context : {};
  const route = value.route && typeof value.route === "object" ? value.route : {};
  return [
    String(value.status || "none"),
    String(value.taskSessionId || ""),
    String(route.routeHash || ""),
    String(route.phase || ""),
  ].join(":");
}

function startActiveRouteWatcher({
  readContext,
  notify,
  intervalMs = 1000,
}) {
  if (typeof readContext !== "function" || typeof notify !== "function") {
    throw new TypeError("readContext and notify must be functions");
  }
  let stopped = false;
  let inFlight = false;
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
      } catch {
        // A client without list-changed support must not stop route enforcement.
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
  };
}

module.exports = {
  activeRouteFingerprint,
  startActiveRouteWatcher,
};
