"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { componentIdentity, verifyRuntimeComponent } = require("../dist/runtime-identity.js");
const { loadControlProtocolSpec } = require("../dist/control-protocol-spec.js");

test("packaged protocol spec resolves from the executing module runtime manifest", () => {
  const sourceRoot = path.resolve(__dirname, "..");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-module-protocol-"));
  const componentRoot = path.join(root, "isolated-component");
  const repositoryRoot = path.join(root, "isolated-repository");
  const moduleRoot = path.join(root, "installed-plugin");
  try {
    fs.mkdirSync(componentRoot, { recursive: true });
    fs.mkdirSync(repositoryRoot, { recursive: true });
    fs.mkdirSync(moduleRoot, { recursive: true });
    const protocolSpec = loadControlProtocolSpec({ componentRoot: sourceRoot });
    fs.writeFileSync(
      path.join(moduleRoot, "control-runtime.json"),
      JSON.stringify({ protocolSpec }),
    );

    assert.deepEqual(
      loadControlProtocolSpec({ componentRoot, repositoryRoot, moduleRoot }),
      protocolSpec,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("compactor runtime identity verifies and detects source drift", () => {
  const sourceRoot = path.resolve(__dirname, "..");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-runtime-identity-"));
  const componentRoot = path.join(root, "compactor");
  try {
    fs.cpSync(path.join(sourceRoot, "src"), path.join(componentRoot, "src"), { recursive: true });
    fs.copyFileSync(path.join(sourceRoot, "package.json"), path.join(componentRoot, "package.json"));
    fs.copyFileSync(path.join(sourceRoot, "manifest.json"), path.join(componentRoot, "manifest.json"));
    const expected = componentIdentity(componentRoot);
    const manifestPath = path.join(root, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({ components: { compactor: expected } }));

    const verified = verifyRuntimeComponent({ componentRoot, manifestPath, required: true });
    assert.equal(verified.verified, true);
    assert.equal(verified.bundleIntegrityVerified, true);
    assert.equal(verified.runtimeVerified, true);
    assert.equal(verified.runtimeStale, false);
    assert.throws(
      () => {
        fs.writeFileSync(manifestPath, JSON.stringify({
          expectedSourceGitCommit: "newer-source-head",
          components: { compactor: expected },
        }));
        return verifyRuntimeComponent({ componentRoot, manifestPath, required: true });
      },
      (error) => {
        assert.match(error.message, /CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH/);
        assert.equal(error.code, "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH");
        return true;
      },
    );
    fs.writeFileSync(manifestPath, JSON.stringify({ components: { compactor: expected } }));
    fs.writeFileSync(path.join(componentRoot, "src", "generator.ts"), "// drift\n");
    assert.throws(
      () => verifyRuntimeComponent({ componentRoot, manifestPath, required: true }),
      /CONTROL_RUNTIME_VERSION_MISMATCH/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("compactor runtime identity rejects a mismatched packaged commit", () => {
  const sourceRoot = path.resolve(__dirname, "..");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-runtime-commit-"));
  const componentRoot = path.join(root, "compactor");
  try {
    fs.cpSync(path.join(sourceRoot, "src"), path.join(componentRoot, "src"), { recursive: true });
    fs.copyFileSync(path.join(sourceRoot, "package.json"), path.join(componentRoot, "package.json"));
    fs.copyFileSync(path.join(sourceRoot, "manifest.json"), path.join(componentRoot, "manifest.json"));
    const expected = componentIdentity(componentRoot);
    const manifestPath = path.join(root, "control-runtime.json");
    fs.writeFileSync(manifestPath, JSON.stringify({ components: { compactor: expected } }));
    fs.writeFileSync(
      path.join(componentRoot, "control-runtime.json"),
      JSON.stringify({ components: { compactor: { ...expected, gitCommit: "other-commit" } } })
    );
    const prior = process.env.CONTROL_RUNTIME_GIT_COMMIT;
    process.env.CONTROL_RUNTIME_GIT_COMMIT = "other-commit";
    try {
      assert.throws(
        () => verifyRuntimeComponent({ componentRoot, manifestPath, required: true }),
        /CONTROL_RUNTIME_VERSION_MISMATCH: compactor differs in gitCommit/
      );
    } finally {
      if (prior === undefined) delete process.env.CONTROL_RUNTIME_GIT_COMMIT;
      else process.env.CONTROL_RUNTIME_GIT_COMMIT = prior;
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
