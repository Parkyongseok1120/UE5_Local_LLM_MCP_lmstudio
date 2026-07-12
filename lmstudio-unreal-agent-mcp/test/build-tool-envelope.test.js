"use strict";

const assert = require("assert");
const test = require("node:test");
const { buildResponsePayload } = require("../src/context-ux");

test("build failure payload marks MCP isError when ok is false", () => {
  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 1, stdout: "error C123", stderr: "", error: "" },
    build: { target: "GameEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Game.uproject",
    command: "Build.bat GameEditor Win64 Development",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });
  assert.strictEqual(payload.ok, false);
  const shouldError = !payload.ok;
  assert.strictEqual(shouldError, true);
});
