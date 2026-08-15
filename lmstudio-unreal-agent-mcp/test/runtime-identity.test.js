"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { componentIdentity, verifyRuntimeComponent } = require("../src/runtime-identity");

const protocolHashFields = [
  "transitionPolicyHash",
  "errorCatalogHash",
  "authorizationSchemaHash",
  "controlSchemaHash",
];

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
    for (const field of protocolHashFields) assert.match(identity[field], /^[0-9a-f]{64}$/);
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

test("agent runtime identity fails closed on protocol schema drift", () => {
  const componentRoot = path.resolve(__dirname, "..");
  const expected = componentIdentity("agent", componentRoot);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-runtime-protocol-"));
  try {
    const manifestPath = path.join(root, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({
      components: { agent: { ...expected, errorCatalogHash: "0".repeat(64) } },
    }));
    assert.throws(
      () => verifyRuntimeComponent("agent", { componentRoot, manifestPath, required: true }),
      /CONTROL_RUNTIME_VERSION_MISMATCH: agent differs in errorCatalogHash/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("standalone installed runtime verifies the protocol spec embedded in its manifest", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-runtime-embedded-protocol-"));
  const componentRoot = path.join(root, "agent");
  try {
    fs.cpSync(path.resolve(__dirname, "../src"), path.join(componentRoot, "src"), { recursive: true });
    fs.copyFileSync(path.resolve(__dirname, "../package.json"), path.join(componentRoot, "package.json"));
    const expected = componentIdentity("agent", componentRoot);
    const protocolSpec = JSON.parse(fs.readFileSync(
      path.resolve(__dirname, "../../config/control_protocol_spec.json"),
      "utf8"
    ));
    const manifestPath = path.join(componentRoot, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({
      protocolSpec,
      components: { agent: expected },
    }));
    const installedRuntime = require(path.join(componentRoot, "src", "runtime-identity.js"));
    assert.equal(installedRuntime.verifyRuntimeComponent("agent", {
      componentRoot,
      repositoryRoot: componentRoot,
      manifestPath,
      required: true,
    }).verified, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("agent runtime identity rejects a mismatched packaged commit", () => {
  const componentRoot = path.resolve(__dirname, "..");
  const expected = componentIdentity("agent", componentRoot);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-runtime-commit-"));
  const manifestPath = path.join(root, "control-runtime.json");
  const prior = process.env.CONTROL_RUNTIME_GIT_COMMIT;
  try {
    fs.writeFileSync(
      manifestPath,
      JSON.stringify({ components: { agent: { ...expected, gitCommit: "other-commit" } } })
    );
    process.env.CONTROL_RUNTIME_GIT_COMMIT = "different-commit";
    assert.throws(
      () => verifyRuntimeComponent("agent", { componentRoot, manifestPath, required: true }),
      /CONTROL_RUNTIME_VERSION_MISMATCH: agent differs in gitCommit/
    );
  } finally {
    if (prior === undefined) delete process.env.CONTROL_RUNTIME_GIT_COMMIT;
    else process.env.CONTROL_RUNTIME_GIT_COMMIT = prior;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
