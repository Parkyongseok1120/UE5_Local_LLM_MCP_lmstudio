"use strict";
// Opt-in live inference against an already loaded model. No installation or model reload.
const fs = require("node:fs");
const path = require("node:path");
const { Chat, LMStudioClient } = require("@lmstudio/sdk");
const { fakeController, handlePredictionLoop } = require("../test/helpers/prediction-loop-fixture.cjs");
const { LOADED_BUILD_ID } = require("../dist/generated-runtime-identity");
const arg = (key, fallback) => { const i = process.argv.indexOf(key); return i < 0 ? fallback : process.argv[i + 1]; };
async function main() {
  const identifier = arg("--model", "");
  const output = arg("--output", "");
  if (!identifier || !output) throw new Error("Use --model <already-loaded identifier> --output <artifact.json>");
  const client = new LMStudioClient();
  const model = await client.llm.model(identifier);
  const info = await model.getModelInfo();
  const expectedContext = Number(arg("--expect-context", info.contextLength));
  if (info.contextLength !== expectedContext) throw new Error(`Loaded C=${info.contextLength}, expected ${expectedContext}`);
  const history = Chat.from([
    { role: "system", content: "Review only the supplied synthetic fixture. Do not call tools. Answer concisely in Korean, with observations, limits and one next verification step. End with REPORT-END." },
    { role: "user", content: "Fixture: A Unity scene component has [SerializeField] float speed = 4; its saved Inspector speed is 7. A separate Update method assigns speed = 4 every frame. No runtime playthrough has been performed. Explain what is source-verified, what could overwrite the authored value, and what needs runtime verification. Do not invent a hierarchy or claim a test passed. Use at most 250 words." }
  ]);
  const calls = [];
  const live = {
    identifier, getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options), countTokens: text => model.countTokens(text),
    act: (chat, tools, options) => {
      const call = { maxTokens: options.maxTokens, toolNames: tools.map(t => t.name), stats: [], reasoningFragments: 0 };
      calls.push(call);
      return model.act(chat, tools, { ...options, temperature: 0, seed: 42,
        onPredictionFragment: fragment => { if (fragment.reasoningType === "reasoning") call.reasoningFragments++; options.onPredictionFragment?.(fragment); },
        onPredictionCompleted: result => { call.stats.push(result.stats); options.onPredictionCompleted?.(result); }
      });
    }
  };
  // Only the host controller is simulated; template, tokenizer and inference are the live model.
  const ctl = fakeController(history, live, { contextManagementMode: "deterministic", contextBudgetMode: "percentage",
    designGuidanceMode: "off", reportReviewMode: "off", outputRecoveryMode: "on", reasoningRecoveryMode: "on" });
  ctl.abortSignal = AbortSignal.timeout(300000);
  ctl.guardAbort = () => ctl.abortSignal.throwIfAborted();
  const started = Date.now();
  let error = null;
  try { await handlePredictionLoop(ctl); } catch (caught) { error = String(caught?.stack || caught); }
  const visible = ctl.blocks.filter(b => b.options?.style?.type !== "thinking").map(b => b.text).join("");
  const report = { kind: "live_model_percentage_budget_eval", buildId: LOADED_BUILD_ID, model: info,
    loadedContextLength: info.contextLength, elapsedMs: Date.now() - started, calls, error, visible,
    reportEndPresent: visible.includes("REPORT-END"),
    events: ctl.debugValues.filter(e => ["context_budget", "direct_context_measurement", "output_recovery", "direct_round_observation"].includes(e.event)),
    limits: ["Synthetic fixture; host controller simulated; real model template/tokenization/inference.",
      "No installed plugin GUI, editor playthrough or original audit-session replay was tested."] };
  fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ output, loadedContextLength: report.loadedContextLength, calls: calls.length, error,
    reportEndPresent: report.reportEndPresent, elapsedMs: report.elapsedMs }));
  if (error || !report.reportEndPresent || !calls.length) process.exitCode = 1;
}
main().catch(error => { console.error(error); process.exitCode = 1; });
