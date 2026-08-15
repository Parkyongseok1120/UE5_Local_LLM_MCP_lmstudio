"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const modulePath = path.resolve(__dirname, "../src/mcp-connection.js");

function identityStatus(env) {
  const result = spawnSync(
    process.execPath,
    ["-e", `process.stdout.write(JSON.stringify(require(${JSON.stringify(modulePath)}).getMcpIdentityStatus()))`],
    { encoding: "utf8", env: { ...process.env, ...env } }
  );
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("explicit identity sources are healthy and do not disclose identifiers", () => {
  const status = identityStatus({
    MCP_BRIDGE_PAIR_ID: "bridge-identity-test",
    MCP_CLIENT_INSTANCE_ID: "client-identity-test",
  });

  assert.deepEqual(status, {
    bridgePairSource: "environment",
    clientInstanceSource: "environment",
    degraded: false,
    degradationReasons: [],
  });
});

test("unusable shared state is observable as a local identity fallback", () => {
  const stateFile = path.join(os.tmpdir(), `mcp-identity-${process.pid}-${Date.now()}`);
  fs.writeFileSync(stateFile, "not a state directory", "utf8");
  try {
    const status = identityStatus({
      AGENT_STATE_ROOT: stateFile,
      MCP_BRIDGE_PAIR_ID: "",
      MCP_CLIENT_INSTANCE_ID: "",
    });

    assert.equal(status.degraded, true);
    assert.equal(status.bridgePairSource, "local-fallback");
    assert.equal(status.clientInstanceSource, "local-fallback");
    assert.deepEqual(status.degradationReasons, [
      "MCP_BRIDGE_LOCAL_FALLBACK",
      "MCP_CLIENT_LOCAL_FALLBACK",
    ]);
  } finally {
    fs.rmSync(stateFile, { force: true });
  }
});
