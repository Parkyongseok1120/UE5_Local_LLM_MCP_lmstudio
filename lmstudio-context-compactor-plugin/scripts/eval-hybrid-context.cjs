#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { LMStudioClient } = require("@lmstudio/sdk");
const { oneRun, summarize } = require("./eval-audit-pressure.cjs");

function valueAfter(flag, fallback = "") { const i = process.argv.indexOf(flag); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback; }
function integerAfter(flag, fallback) { const n = Number(valueAfter(flag, fallback)); return Number.isSafeInteger(n) && n >= 0 ? n : fallback; }
const sha256 = value => crypto.createHash("sha256").update(value).digest("hex");
const median = values => { const sorted = values.filter(Number.isFinite).sort((a, b) => a - b); return sorted.length ? sorted[Math.floor(sorted.length / 2)] : null; };

function hybridSummary(runs, group) {
  const selected = runs.filter(run => run.group === group);
  const base = summarize(selected, group);
  return { ...base,
    finalInputTokens: selected.flatMap(run => run.fullInput.map(input => input.after)),
    medianFinalInputTokens: median(selected.flatMap(run => run.fullInput.map(input => input.after))),
    mandatoryFloorExceedsTarget: selected.reduce((n, run) => n
      + run.fullInput.filter(input => input.mandatoryFloorExceedsTarget === true).length, 0),
    projectedInputs: selected.reduce((n, run) => n + run.fullInput.filter(input => input.projectionApplied).length, 0),
    semanticSummaryCalls: selected.reduce((n, run) => n + run.semanticSummaryCalls, 0),
    semanticSummaryAccepted: selected.reduce((n, run) => n + run.semanticSummaryAccepted, 0),
    totalPromptTokens: selected.map(run => run.totalPromptTokens),
    totalPredictedTokens: selected.map(run => run.totalPredictedTokens),
  };
}

async function main() {
  const identifier = valueAfter("--model", "swift-qwen3.8-27b");
  const pairs = integerAfter("--pairs", 5);
  const maxTokens = integerAfter("--research-max-tokens", 1800);
  const evaluationTimeoutSeconds = integerAfter("--evaluation-timeout-seconds", 240);
  const output = path.resolve(valueAfter("--output", path.join(process.cwd(), "hybrid-context-abc.json")));
  const client = new LMStudioClient();
  const loaded = await client.llm.listLoaded();
  const handle = loaded.find(item => item.identifier === identifier);
  if (!handle) throw new Error(`Loaded model not found: ${identifier}`);
  let processInfo = {};
  try {
    const listed = JSON.parse(execFileSync("lms", ["ps", "--json"], { encoding: "utf8", windowsHide: true, timeout: 10000 }));
    const processes = Array.isArray(listed) ? listed : listed.models || listed.processes || [];
    processInfo = processes.find(item => item.identifier === identifier) || {};
  } catch { /* SDK execution remains valid while process metadata is unavailable. */ }
  const modelInfo = { ...handle, ...processInfo }, model = await client.llm.model(identifier), runs = [];
  const groups = { A: "legacy", B: "deterministic", C: "hybrid" };
  for (let pairIndex = 1; pairIndex <= pairs; pairIndex++) {
    const order = pairIndex % 2 ? ["A", "B", "C"] : ["C", "B", "A"];
    for (const group of order) {
      const run = await oneRun(model, modelInfo, group, "hybrid-abc", pairIndex, 9000 + pairIndex, "", {
        inputAvailabilityMode: "observe",
        auditContractSelected: false,
        contextManagementMode: groups[group],
        workingInputTargetTokens: 18000,
        workingInputTriggerTokens: 22000,
        softRemainingTokens: 6000,
        hardRemainingTokens: 3000,
        maxOutputReserve: 8192,
        safetyMarginTokens: 2048,
        assumedContextLength: 38912,
        toolResultProjectionChars: 512,
        semanticSummaryMaxTokens: 1024,
        researchMaxTokens: maxTokens,
        evaluationTimeoutSeconds,
        outputRecoveryMode: "on",
        outputRecoveryMaxTokens: 0,
        outputRecoverySeconds: 90,
        pressureProfile: "forced",
        historyMode: "pressure",
      });
      runs.push(run);
      process.stdout.write(`${JSON.stringify({ pairIndex, group, mode: groups[group],
        outcome: run.outcome, score: run.answerEvaluation.score,
        finalInput: run.fullInput.map(item => item.after), summaryCalls: run.semanticSummaryCalls,
        totalPromptTokens: run.totalPromptTokens, totalPredictedTokens: run.totalPredictedTokens,
        elapsedMs: run.elapsedMs, error: run.error })}\n`);
    }
  }
  const root = path.resolve(__dirname, "../..");
  const head = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8", windowsHide: true }).trim();
  const report = {
    experiment: "hybrid-working-context-A-B-C",
    generatedAt: new Date().toISOString(),
    sourceHead: head,
    sourceDirty: Boolean(execFileSync("git", ["status", "--porcelain", "--", "lmstudio-context-compactor-plugin"],
      { cwd: root, encoding: "utf8", windowsHide: true }).trim()),
    sourceBundleSha256: sha256(["prediction-loop.js", "working-context.js", "evidence-archive.js"]
      .map(name => `${name}:${sha256(fs.readFileSync(path.join(__dirname, "../dist", name)))}`).join("\n")),
    model: { identifier, path: modelInfo.path || null, quantization: modelInfo.quantization || null,
      loadedContextLength: modelInfo.contextLength || null, maxContextLength: modelInfo.maxContextLength || null,
      maxTokens, evaluationTimeoutSeconds, temperature: 0,
      seedPolicy: "same seed within each A/B/C pair" },
    groups: {
      A: "Luna baseline (legacy context mode)",
      B: "A plus deterministic archive/projection/working window",
      C: "B plus one bounded tool-free semantic handoff per accepted compaction event",
      D: "not run: tool schema optimization remains disabled until measurements show schema dominance",
    },
    requestedPairs: pairs,
    comparisons: { A: hybridSummary(runs, "A"), B: hybridSummary(runs, "B"), C: hybridSummary(runs, "C") },
    runs,
    interpretationBoundary: [
      "All model, template, loaded context, output cap, fixture scope, temperature and pair seed settings are shared within a pair.",
      "Token totals include semantic handoff calls; archive retrieval counts remain separate from source tool rereads.",
      "Shorter input is not counted as quality success without the deterministic answer oracle and final delivery outcome.",
      "Installed plugin, active chat and Unity/Unreal editor execution are not modified by this harness.",
    ],
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ output, comparisons: report.comparisons })}\n`);
  if (runs.some(run => run.outcome.executionOutcome !== "completed")) process.exitCode = 1;
}

if (require.main === module) main().catch(error => { console.error(error?.stack || error); process.exitCode = 1; });
module.exports = { hybridSummary };
