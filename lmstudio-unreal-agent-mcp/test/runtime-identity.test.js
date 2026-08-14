"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { componentIdentity, verifyRuntimeComponent } = require("../src/runtime-identity");

test("agent runtime identity is deterministic and verifiable", () => {
  const componentRoot = path.resolve(__dirname, "..");
  const identity = componentIdentity("agent", componentRoot);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-runtime-identity-"));
  try {
    const manifestPath = path.join(root, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({ components: { agent: identity } }));
    const result = verifyRuntimeComponent("agent", { componentRoot, manifestPath, required: true });
    assert.equal(result.verified, true);
    assert.equal(result.running.buildHash, identity.buildHash);
    assert.equal(identity.componentVersion, "0.3.16");
    assert.equal(identity.protocolVersion, 2);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("agent runtime identity fails closed after source drift", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-runtime-drift-"));
  const componentRoot = path.join(root, "agent");
  try {
    fs.cpSync(path.resolve(__dirname, "../src"), path.join(componentRoot, "src"), { recursive: true });
    fs.copyFileSync(path.resolve(__dirname, "../package.json"), path.join(componentRoot, "package.json"));
    const expected = componentIdentity("agent", componentRoot);
    const manifestPath = path.join(root, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({ components: { agent: expected } }));
    fs.writeFileSync(path.join(componentRoot, "src", "control-envelope.js"), "// drift\n");

    assert.throws(
      () => verifyRuntimeComponent("agent", { componentRoot, manifestPath, required: true }),
      /CONTROL_RUNTIME_VERSION_MISMATCH/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
