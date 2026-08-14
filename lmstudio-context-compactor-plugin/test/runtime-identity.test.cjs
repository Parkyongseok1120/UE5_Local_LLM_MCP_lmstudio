"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { componentIdentity, verifyRuntimeComponent } = require("../dist/runtime-identity.js");

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

    assert.equal(
      verifyRuntimeComponent({ componentRoot, manifestPath, required: true }).verified,
      true
    );
    fs.writeFileSync(path.join(componentRoot, "src", "generator.ts"), "// drift\n");
    assert.throws(
      () => verifyRuntimeComponent({ componentRoot, manifestPath, required: true }),
      /CONTROL_RUNTIME_VERSION_MISMATCH/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
