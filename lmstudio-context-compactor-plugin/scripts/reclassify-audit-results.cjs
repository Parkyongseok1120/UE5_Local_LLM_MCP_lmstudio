#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { classifyRunOutcome, summarize } = require("./eval-audit-pressure.cjs");
const { auditAnswerMatchesOracle } = require("./availability-eval-core.cjs");

function reclassifyReport(report, sourcePath = "") {
  const runs = Array.isArray(report?.runs) ? report.runs.map(run => ({
    ...run,
    sourceAnswerEvaluation: run.answerEvaluation || null,
    answerEvaluation: auditAnswerMatchesOracle(run.visibleAnswer),
    outcome: classifyRunOutcome(run),
    legacyTimedOutFieldPresent: Object.hasOwn(run, "timedOut"),
  })) : [];
  const groups = [...new Set(runs.map(run => run.group).filter(Boolean))];
  return {
    schemaVersion: 3,
    classification: "non_mutating_saved_result_outcome_and_answer_reclassification",
    sourcePath: sourcePath || null,
    sourceGeneratedAt: report?.generatedAt || null,
    sourceHead: report?.sourceHead || null,
    sourceExperiment: report?.experiment || null,
    limitations: [
      "Only fields stored in the source JSON are classified.",
      "A missing legacy timedOut field is not inferred from elapsed time or a userStopped finish reason.",
      "External server logs remain separate runtime evidence and are not merged into this file.",
      "The source answer evaluation is preserved per run and the visible answer is rescored with the current deterministic rubric.",
    ],
    summaries: Object.fromEntries(groups.map(group => [group, summarize(runs, group)])),
    runs,
  };
}

function main() {
  const [, , inputArg, outputArg] = process.argv;
  if (!inputArg || !outputArg) {
    throw new Error("Usage: node scripts/reclassify-audit-results.cjs <input.json> <output.json>");
  }
  const input = path.resolve(inputArg);
  const output = path.resolve(outputArg);
  if (input === output) throw new Error("Output must differ from input; saved evidence is immutable");
  const report = JSON.parse(fs.readFileSync(input, "utf8"));
  const reclassified = reclassifyReport(report, input);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(reclassified, null, 2)}\n`, { flag: "wx" });
  process.stdout.write(`${JSON.stringify({ input, output, runs: reclassified.runs.length })}\n`);
}

if (require.main === module) {
  try { main(); }
  catch (error) {
    console.error(error?.stack || error);
    process.exitCode = 1;
  }
}

module.exports = { reclassifyReport };
