#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const DIRECT_SOURCE_FILES = Object.freeze([
  "src/budget-broker.ts",
  "src/context-manager.ts",
  "src/context-ports.ts",
  "src/delivery-controller.ts",
  "src/evidence-manager.ts",
  "src/evidence-telemetry.ts",
  "src/execution-config.ts",
  "src/execution-contracts.ts",
  "src/execution-instructions.ts",
  "src/execution-state.ts",
  "src/prediction-ui.ts",
  "src/raw-tool-intent.ts",
  "src/recovery-coordinator.ts",
  "src/runtime-identity.ts",
  "src/tool-boundary.ts",
  "src/tool-capability-registry.ts",

  "src/index.ts",
  "src/prediction-loop.ts",
  "src/context-budget.ts",
  "src/attachment-boundary.ts",
  "src/round-loop.ts",
  "src/prediction-stream.ts",
  "src/tool-scope.ts",
  "src/attachment-tools.ts",
  "src/direct-compaction-core.js",
  "src/compaction-tool-memory.js",
  "src/input-availability.js",
  "src/evidence-archive.js",
  "src/evidence-identity.js",
  "src/working-context.js",
  "src/working-context-boundary.ts",
  "src/continuity-assistant-evidence.js",
  "src/continuity-file-observations.js",
  "src/continuity-memory.js",
  "src/continuity-model-notes.js",
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
  let entry = "";
  try { index = fs.readFileSync(path.join(root, "src", "index.ts"), "utf8"); } catch { /* reported below */ }
  try { entry = fs.readFileSync(path.join(root, ".lmstudio", "entry.ts"), "utf8"); } catch { /* reported below */ }
  const directWiring = index.includes("./prediction-loop")
    && index.includes("./direct-config")
    && index.includes("./working-context-boundary")
    && /withPromptPreprocessor\s*\(/.test(index)
    && /withPredictionLoopHandler\s*\(\s*createPredictionLoopHandler\s*\(/.test(index);
  const preprocessorHostWiring = /withPromptPreprocessor\s*\(\s*handler\s*\)/.test(entry)
    && /host\.setPromptPreprocessor\s*\(\s*handler\s*\)/.test(entry);
  const legacyWiring = /withGenerator\s*\(|["']\.\/generator["']|["']\.\/compaction-core["']/.test(index);
  const issues = [];
  if (missing.length) issues.push(`missing: ${missing.join(", ")}`);
  if (index && !directWiring) issues.push("src/index.ts does not register the direct prediction-loop handler");
  if (entry && !preprocessorHostWiring) issues.push(".lmstudio/entry.ts does not register the prompt preprocessor with the host");
  if (legacyWiring) issues.push("src/index.ts still registers a removed legacy handler");
  const sourceLayoutVerified = missing.length === 0 && directWiring && preprocessorHostWiring && !legacyWiring;
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
    process.stdout.write("Activation is per chat. The integrated installer enables the top-level switch in existing chats; restart LM Studio after installation or update.\n");
  } else {
    process.stdout.write(`[FAIL] Context-compactor source verification failed: ${result.issues.join("; ")}\n`);
  }
  if (!result.ok) return 2;
  if (requireRuntime) {
    if (!json) process.stdout.write("[UNPROVEN] The currently open chat activation is host-owned and cannot be inferred from plugin source files alone.\n");
    return 3;
  }
  return 0;
}

if (require.main === module) process.exitCode = main();
module.exports = { DIRECT_SOURCE_FILES, inspect, main };
