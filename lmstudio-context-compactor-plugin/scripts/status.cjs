#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const DIRECT_SOURCE_FILES = Object.freeze([
  "src/index.ts",
  "src/prediction-loop.ts",
  "src/round-loop.ts",
  "src/direct-compaction-core.js",
  "src/compaction-tool-memory.js",
  "src/continuity-file-observations.js",
  "src/continuity-memory.js",
  "src/continuity-objectives.js",
  "src/continuity-text.js",
  "src/durable-memory-sanitizer.js",
  "src/direct-config.ts",
]);

function inspect(root = path.resolve(__dirname, "..")) {
  const required = [
    "manifest.json",
    "package.json",
    ".lmstudio/entry.ts",
    ...DIRECT_SOURCE_FILES,
  ];
  const missing = required.filter((relative) => !fs.existsSync(path.join(root, relative)));
  let index = "";
  try { index = fs.readFileSync(path.join(root, "src", "index.ts"), "utf8"); } catch { /* reported below */ }
  const directWiring = index.includes("./prediction-loop")
    && index.includes("./direct-config")
    && /withPredictionLoopHandler\s*\(\s*handlePredictionLoop\s*\)/.test(index);
  const legacyWiring = /withGenerator\s*\(|["']\.\/generator["']|["']\.\/compaction-core["']/.test(index);
  const issues = [];
  if (missing.length) issues.push(`missing: ${missing.join(", ")}`);
  if (index && !directWiring) issues.push("src/index.ts does not register the direct prediction-loop handler");
  if (legacyWiring) issues.push("src/index.ts still registers a removed legacy handler");
  const sourceLayoutVerified = missing.length === 0 && directWiring && !legacyWiring;
  return {
    ok: sourceLayoutVerified,
    sourceLayoutVerified,
    installedSourceComplete: missing.length === 0,
    executionMode: sourceLayoutVerified ? "transparent_context_only" : "unknown",
    modelOwner: sourceLayoutVerified ? "lmstudio_selected_model" : "unknown",
    toolsOwner: sourceLayoutVerified ? "lmstudio_selected_model" : "unknown",
    runtimeActivation: "not_machine_verifiable",
    runtimeActivationProven: false,
    missing,
    issues,
  };
}

function main(argv = process.argv.slice(2)) {
  const json = argv.includes("--json");
  const requireRuntime = argv.includes("--require-runtime");
  const unknown = argv.filter((arg) => arg !== "--json" && arg !== "--require-runtime");
  if (unknown.length) {
    const error = `Unknown argument: ${unknown.join(", ")}`;
    if (json) process.stdout.write(`${JSON.stringify({ ok: false, error })}\n`);
    else process.stderr.write(`[FAIL] ${error}\n`);
    return 4;
  }
  const result = inspect();
  if (json) process.stdout.write(`${JSON.stringify(result)}\n`);
  else if (result.ok) {
    process.stdout.write("[PASS] Transparent context-compactor source layout verified.\n");
    process.stdout.write("Default policy: keep the top-level chat-plugin switch OFF. Enable that single switch only for a long chat that needs compaction; there is no nested compaction gate.\n");
  } else {
    process.stdout.write(`[FAIL] Context-compactor source verification failed: ${result.issues.join("; ")}\n`);
  }
  if (!result.ok) return 2;
  if (requireRuntime) {
    if (!json) process.stdout.write("[UNPROVEN] Chat activation is host-owned; default policy keeps it OFF.\n");
    return 3;
  }
  return 0;
}

if (require.main === module) process.exitCode = main();
module.exports = { DIRECT_SOURCE_FILES, inspect, main };
