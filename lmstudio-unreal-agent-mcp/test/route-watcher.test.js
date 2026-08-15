"use strict";

const assert = require("assert");
const test = require("node:test");

const {
  activeRouteFingerprint,
  startActiveRouteWatcher,
} = require("../src/route-watcher");

test("route watcher notifies only on status/hash changes and can stop", async () => {
  let context = { status: "none" };
  const notifications = [];
  const watcher = startActiveRouteWatcher({
    readContext: () => context,
    notify: async (next, fingerprint) => {
      notifications.push({ next, fingerprint });
    },
    intervalMs: 60_000,
  });
  try {
    assert.strictEqual(watcher.timer.hasRef(), false);
    assert.strictEqual(await watcher.poll(), false);
    context = {
      status: "active",
      taskSessionId: "task_12345678",
      route: { routeHash: "route-1", phase: "planner" },
    };
    assert.strictEqual(await watcher.poll(), true);
    assert.strictEqual(notifications.length, 1);
    assert.strictEqual(await watcher.poll(), false);

    context = { ...context, route: { routeHash: "route-2", phase: "executor" } };
    assert.strictEqual(await watcher.poll(), true);
    assert.strictEqual(notifications.length, 2);
    assert.strictEqual(
      notifications[1].fingerprint,
      activeRouteFingerprint(context)
    );

    watcher.stop();
    context = { status: "blocked", taskSessionId: "task_12345678" };
    assert.strictEqual(await watcher.poll(), false);
    assert.strictEqual(notifications.length, 2);
  } finally {
    watcher.stop();
  }
});

test("route watcher swallows notification failures without losing new baseline", async () => {
  let context = { status: "none" };
  let attempts = 0;
  const watcher = startActiveRouteWatcher({
    readContext: () => context,
    notify: async () => {
      attempts += 1;
      throw new Error("client does not support notifications");
    },
    intervalMs: 60_000,
  });
  try {
    context = { status: "ambiguous_or_corrupt" };
    assert.strictEqual(await watcher.poll(), true);
    assert.strictEqual(attempts, 1);
    assert.deepStrictEqual(watcher.lastNotificationError, {
      code: "TOOLS_LIST_CHANGED_NOTIFY_FAILED",
      message: "client does not support notifications",
    });
    assert.strictEqual(watcher.notificationFailureCount, 1);
    assert.strictEqual(await watcher.poll(), false);
    assert.strictEqual(attempts, 1);
  } finally {
    watcher.stop();
  }
});

test("route watcher observes control changes even when a legacy routeHash is stable", async () => {
  let context = {
    status: "active",
    taskSessionId: "task_12345678",
    route: { routeHash: "stable-route", phase: "executor", activeTools: ["read_file"] },
    state: {
      controlEpoch: 1,
      controlState: {
        disposition: "continue",
        allowedTools: ["read_file"],
        requiredTool: null,
      },
    },
  };
  const notifications = [];
  const watcher = startActiveRouteWatcher({
    readContext: () => context,
    notify: async (_next, fingerprint) => notifications.push(fingerprint),
    intervalMs: 60_000,
  });
  try {
    context = {
      ...context,
      state: {
        ...context.state,
        controlEpoch: 2,
        controlState: {
          disposition: "continue",
          allowedTools: [],
          requiredTool: null,
          blocker: { code: "EVIDENCE_STAGNATION" },
        },
      },
    };
    assert.strictEqual(await watcher.poll(), true);
    assert.strictEqual(notifications.length, 1);
  } finally {
    watcher.stop();
  }
});
