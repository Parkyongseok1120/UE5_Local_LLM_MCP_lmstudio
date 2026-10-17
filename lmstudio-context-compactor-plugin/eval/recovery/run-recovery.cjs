#!/usr/bin/env node
"use strict";

// Run the recovery scenarios against a model loaded in LM Studio.
//   npm run build   (in lmstudio-context-compactor-plugin)
//   node eval/recovery/run-recovery.cjs --model <identifier> --out <dir> [--scenario <id>] [--seed <n>]
// Writes runs/<scenario>.json (calls, inputs, debug events, answer) and summary.json (scores).

const fs = require("node:fs");
const path = require("node:path");
const { runScenario } = require("./recovery-harness.cjs");
const { scoreRun } = require("./score-recovery.cjs");

const arg = (flag, fallback = "") => {
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
};

async function main() {
  const modelId = arg("--model");
  const outDir = path.resolve(arg("--out", "eval-results/recovery"));
  if (!modelId) throw new Error("--model <LM Studio model identifier> is required");
  const { LMStudioClient } = require("node:module").createRequire(path.join(__dirname, "..", "..", "package.json"))("@lmstudio/sdk");
  const scenarios = JSON.parse(fs.readFileSync(path.join(__dirname, "recovery-scenarios.json"), "utf8"));
  const seed = Number(arg("--seed", "61000"));
  const model = await new LMStudioClient().llm.model(modelId);
  const fixed = {
    identifier: model.identifier,
    getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options),
    countTokens: text => model.countTokens(text),
    act: (chat, tools, options) => model.act(chat, tools, { ...options, temperature: 0, seed }),
  };
  fs.mkdirSync(path.join(outDir, "runs"), { recursive: true });
  const scores = [];
  for (const scenario of scenarios.scenarios) {
    if (arg("--scenario") && scenario.id !== arg("--scenario")) continue;
    process.stdout.write(`${scenario.id} ...\n`);
    const run = await runScenario(scenario, scenarios.fixture, fixed);
    fs.writeFileSync(path.join(outDir, "runs", `${scenario.id}.json`), JSON.stringify(run, null, 1));
    const score = scoreRun(scenario, run);
    scores.push(score);
    process.stdout.write(`  ${score.status}\n`);
  }
  fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify({ model: modelId, seed, temperature: 0,
    note: "Model-behaviour observation for this model and settings only.", scores }, null, 1));
}

main().catch(error => { console.error(error?.stack || error); process.exitCode = 1; });
