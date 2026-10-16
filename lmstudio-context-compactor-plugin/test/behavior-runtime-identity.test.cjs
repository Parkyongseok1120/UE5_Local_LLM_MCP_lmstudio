"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { renderIdentity } = require("../scripts/build-runtime-identity.cjs");

test("build identity binds compile settings and SDK patch inputs, with platform-neutral line endings", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "behavior-build-id-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "src")); fs.mkdirSync(path.join(root, "scripts"));
  const files = { "src/index.ts": "export const value = 1;\n", "manifest.json": "{}\n",
    "package.json": "{}\n", "package-lock.json": "{}\n", "tsconfig.json": "{}\n",
    "scripts/patch-lmstudio-sdk.cjs": "// patch v1\n" };
  for (const [file, content] of Object.entries(files)) fs.writeFileSync(path.join(root, file), content);
  const first = renderIdentity(root);
  for (const [file, content] of Object.entries(files)) fs.writeFileSync(path.join(root, file), content.replace(/\n/g, "\r\n"));
  assert.equal(renderIdentity(root), first);
  fs.writeFileSync(path.join(root, "src/generated-runtime-identity.ts"), "ignored generated output");
  assert.equal(renderIdentity(root), first);
  for (const file of ["src/index.ts", "tsconfig.json", "scripts/patch-lmstudio-sdk.cjs"]) {
    fs.writeFileSync(path.join(root, file), `${files[file]} // changed\n`);
    assert.notEqual(renderIdentity(root), first, file);
    fs.writeFileSync(path.join(root, file), files[file]);
  }
});

test("a loaded module reports a newer disk install without claiming it has reloaded", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "behavior-loaded-id-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "src")); fs.mkdirSync(path.join(root, "dist"));
  for (const file of ["runtime-identity.js", "generated-runtime-identity.js"])
    fs.copyFileSync(path.join(__dirname, "../dist", file), path.join(root, "dist", file));
  const { LOADED_BUILD_ID } = require(path.join(root, "dist/generated-runtime-identity.js"));
  fs.writeFileSync(path.join(root, "manifest.json"), JSON.stringify({ revision: 1 }));
  const installed = path.join(root, "src/generated-runtime-identity.ts");
  fs.writeFileSync(installed, `export const LOADED_BUILD_ID = "${LOADED_BUILD_ID}";`);
  const runtime = require(path.join(root, "dist/runtime-identity.js"));
  assert.equal(runtime.runtimeInstallationIdentity().loadedMatchesInstalled, true);
  const replacement = "ef-build-v2-" + "0".repeat(64);
  fs.writeFileSync(installed, `export const LOADED_BUILD_ID = "${replacement}";`);
  const next = runtime.runtimeInstallationIdentity();
  assert.equal(next.loadedBuildId, LOADED_BUILD_ID);
  assert.equal(next.installedBuildId, replacement);
  assert.equal(next.loadedMatchesInstalled, false);
  fs.unlinkSync(installed);
  assert.equal(runtime.runtimeInstallationIdentity().loadedMatchesInstalled, null);
});

test("behavior evaluation corpus keeps positive and counterexample variants without claiming execution", () => {
  const corpus = require("./fixtures/behavior-evaluation-cases.json");
  assert.equal(corpus.status, "specified_not_executed");
  assert.equal(corpus.executionPolicy.additionalModelOrJudge, false);
  assert.equal(corpus.executionPolicy.automaticSuccessClassification, false);
  assert.equal(corpus.executionPolicy.productionStaticValidationGate, false);
  assert.equal(corpus.cases.length, 14);
  assert.equal(new Set(corpus.cases.map(c => c.id)).size, 14);
  for (const item of corpus.cases) {
    assert.ok(item.variants.length >= 2, item.id);
    assert.equal(new Set(item.variants.map(v => v.id)).size, item.variants.length, item.id);
    for (const key of ["expectedContract", "allowedAlternatives", "requiredEvidence", "reviewQuestions", "costMetricIds"])
      assert.ok(item[key].length > 0, `${item.id}.${key}`);
    for (const id of item.costMetricIds) assert.ok(corpus.metrics[id], id);
  }
});
