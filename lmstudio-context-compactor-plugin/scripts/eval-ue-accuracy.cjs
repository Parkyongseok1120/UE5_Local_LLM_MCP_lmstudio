#!/usr/bin/env node
"use strict";
// Plan §5 / W-A2: real-model accuracy on the synthetic UE normal/defect pairs.
// Compare off/auto on one build, or the same cases against a preserved runtime.
// Usage: node scripts/eval-ue-accuracy.cjs --model <id> --reps 1 --output <new-file>
// [--cases P1-N,P3-D,P7] [--groups off,auto] [--seed-base 7000]
// [--engine-root <verified-engine>] [--runtime-root <preserved-plugin-root>]
// [--resume] [--pause-ms <ms> [--pause-at run|rep]]
// Raw output is local-only: it contains full visible answers and source excerpts.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { Chat, ChatMessage, LMStudioClient, rawFunctionTool } = require("@lmstudio/sdk");
const fixture = require("./ue-accuracy-fixture.cjs");

const valueAfter = (flag, fallback) => { const i = process.argv.indexOf(flag); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback; };
// Compare the same fixture/runner against a preserved dirty-tree runtime.
const runtimeRoot = path.resolve(valueAfter('--runtime-root', path.resolve(__dirname, '..')));
const { createPredictionLoopHandler } = require(path.join(runtimeRoot, 'dist/prediction-loop.js'));
// Actual installed source backs this bounded evaluation catalog. This is not a
// substitute for the production MCP/GUI integration checks.
const SCHEMA_VERSION = 4;
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function createEngineLookup(engineRoot) {
  const build = JSON.parse(fs.readFileSync(path.join(engineRoot, 'Engine/Build/Build.version'), 'utf8'));
  const version = `${build.MajorVersion}.${build.MinorVersion}.${build.PatchVersion}`;
  if (version !== fixture.ENGINE_BUILD.version || build.Changelist !== fixture.ENGINE_BUILD.changelist)
    throw new Error('Engine build differs from the fixture evidence; refresh evidence before evaluating.');
  const excerpts = new Map();
  for (const source of Object.values(fixture.SOURCES).filter(item => item.kind === 'engine_source')) {
    const bytes = fs.readFileSync(path.join(engineRoot, source.path));
    if (hash(bytes) !== source.sha256) throw new Error(`Engine source changed: ${source.path}`);
    const [startLine, endLine] = source.lines;
    excerpts.set(source.symbol, { source, startLine, endLine,
      content: bytes.toString('utf8').split(/\r?\n/u).slice(startLine - 1, endLine).join('\n') });
  }
  return symbol => {
    const selected = excerpts.get(symbol === 'AActor' ? 'AActor::GetFunctionCallspace' : symbol);
    if (!selected) return { ok: false, errorCode: 'SYMBOL_NOT_IN_EVAL_CATALOG', symbol,
      supportedSymbols: [...excerpts.keys()] };
    const { source, startLine, endLine, content } = selected;
    return { ok: true, canonicalProject: fixture.PROJECT_IDENTITY, sourceMode: 'engine_local',
      evidenceSource: 'installed_engine_source', symbol: source.symbol,
      observedEngineIdentity: { status: 'observed', engineRoot, hashScope: 'file', sha256: source.sha256, version },
      path: `engine://${source.path}`, sha256: source.sha256, startLine, endLine, content,
      coverage: { completeness: 'partial', scope: 'symbol' } };
  };
}
const VERDICT_LINE = "End your answer with one final line exactly in this form: VERDICT: <label> | <severity or none>";

function caseHandlers(entry, engineLookup) {
  const allowed = new Set(entry.files);
  return {
    list_files: ({ folder }) => {
      const prefix = String(folder || '').replace(/^project:\/\//u, '').replace(/\/?$/u, '/');
      return { ok: true, folder: `project://${prefix}`, entries: entry.files.filter(file => file.startsWith(prefix)).map(file => `project://${file}`) };
    },
    read_file_range: args => allowed.has(String(args.path).replace(/^project:\/\//u, ''))
      ? fixture.readPayload(args.path, args.startLine, args.endLine)
      : { ok: false, errorCode: 'NOT_IN_CASE', message: 'Only the current scenario files are available.' },
    search_files: ({ query, cursor, maxResults = 4 }) => {
      if (typeof query !== 'string' || !query || !Number.isSafeInteger(maxResults) || maxResults < 1 || maxResults > 10)
        return { ok: false, errorCode: 'INVALID_SEARCH' };
      const scope = hash(JSON.stringify([entry.files, query]));
      let offset = 0;
      if (cursor) {
        const match = /^([a-f0-9]{64}):(\d+)$/u.exec(String(cursor));
        if (!match || match[1] !== scope || !Number.isSafeInteger(Number(match[2])))
          return { ok: false, errorCode: 'INVALID_CURSOR' };
        offset = Number(match[2]);
      }
      const matches = entry.files.flatMap(file => fixture.FILES.get(file).lines.flatMap((text, index) =>
        text.includes(query) ? [{ path: `project://${file}`, line: index + 1, text }] : []));
      if (offset > matches.length) return { ok: false, errorCode: 'INVALID_CURSOR' };
      const results = matches.slice(offset, offset + maxResults), next = offset + results.length;
      const truncated = next < matches.length;
      return { ok: true, query, scope: entry.folder, results, matchesReturned: results.length,
        matchesOmitted: matches.length - next, truncated, coverage: truncated ? 'partial' : 'complete',
        absenceVerified: matches.length === 0, ...(truncated ? { nextCursor: `${scope}:${next}` } : {}) };
    },
    unreal_symbol_lookup: ({ symbol }) => engineLookup(symbol),
  };
}

function tools(recorder, entry, engineLookup) {
  const handlers = caseHandlers(entry, engineLookup);
  const invoke = tool => async args => {
    const started = Date.now();
    const result = await handlers[tool](args);
    recorder.calls.push({ ordinal: recorder.calls.length + 1, tool, args, elapsedMs: Date.now() - started,
      resultSha256: hash(JSON.stringify(result)), result });
    return result;
  };
  const listFiles = rawFunctionTool({
    name: "list_files",
    description: "List the files under one folder of the active synthetic project.",
    parametersJsonSchema: { type: "object", properties: { folder: { type: "string" } }, required: ["folder"], additionalProperties: false },
    implementation: invoke('list_files'),
  });
  const readRange = rawFunctionTool({
    name: "read_file_range",
    description: "Read lines startLine..endLine of one synthetic project file (at most 220 lines).",
    parametersJsonSchema: { type: "object", properties: { path: { type: "string" }, startLine: { type: "integer", minimum: 1 },
      endLine: { type: "integer", minimum: 1 } }, required: ["path", "startLine", "endLine"], additionalProperties: false },
    implementation: invoke('read_file_range'),
  });
  for (const tool of [listFiles, readRange]) tool.pluginIdentifier = "mcp/unreal-agent";
  const search = rawFunctionTool({
    name: 'search_files', description: 'Search an exact, case-sensitive text in the current scenario files. Results may be paginated; scope excludes assets and other scenarios.',
    parametersJsonSchema: { type: 'object', properties: { query: { type: 'string', minLength: 1 },
      cursor: { type: 'string' }, maxResults: { type: 'integer', minimum: 1, maximum: 10 } }, required: ['query'], additionalProperties: false },
    implementation: invoke('search_files'),
  });
  search.pluginIdentifier = 'mcp/unreal-agent';
  const engineTool = rawFunctionTool({
    name: "unreal_symbol_lookup",
    description: `Read verified installed engine excerpts. Symbols: ${[...new Set(Object.values(fixture.SOURCES).filter(item => item.kind === 'engine_source').map(item => item.symbol))].join(', ')}. Other symbols are unavailable in this evaluation.`,
    parametersJsonSchema: { type: "object", properties: { symbol: { type: "string" } }, required: ["symbol"], additionalProperties: false },
    implementation: invoke('unreal_symbol_lookup'),
  });
  engineTool.pluginIdentifier = "mcp/unreal-rag";
  return [listFiles, readRange, search, engineTool];
}

function controller(history, tokenSource, session, overrides) {
  const blocks = [], debugValues = [];
  const config = { projectEngine: "unreal", projectIdentity: fixture.PROJECT_IDENTITY, observeOnly: false, showDebugInfo: true,
    contextManagementMode: "hybrid", inputAvailabilityMode: "observe", reviewProgress: false, separateAttachments: false, ...overrides };
  return {
    abortSignal: AbortSignal.timeout(Number(overrides.evaluationTimeoutMs || 420000)),
    guardAbort() { if (this.abortSignal.aborted) throw this.abortSignal.reason; },
    getPluginConfig() { return { get: key => config[key] }; },
    getWorkingDirectory() { return ""; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return session; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    debug(value) { debugValues.push(value); },
    createStatus() { return { setText() {}, setState() {}, remove() {} }; },
    createToolStatus() { return { setText() {}, setState() {}, setStatus() {}, remove() {} }; },
    createContentBlock(options) {
      const block = { options, text: "" };
      blocks.push(block);
      return { appendText(text) { block.text += text; }, appendToolRequest() {}, replaceToolRequest() {}, appendToolResult() {},
        setStyle(style) { block.options.style = style; } };
    },
    blocks, debugValues,
  };
}

function parseVerdict(answer) {
  const line = answer.trim().split(/\r?\n/u).at(-1)?.match(/^VERDICT:\s*(defect|not_defect|needs_runtime_proof)\s*\|\s*(P[0-3]|none)\s*$/u);
  if (!line) return { label: "invalid", severity: null, parsed: false };
  return { label: line[1], severity: line[2] === 'none' ? null : line[2], parsed: true };
}

function observeModelInput(chat) {
  const messages = chat.getMessagesArray();
  const instructions = messages.filter(message => message.isSystemPrompt()).map(message => message.getText()).join('\n');
  return { systemSha256: hash(instructions),
    guidanceIdsInSystem: [...instructions.matchAll(/\[reference id=([^\s\]]+)/gu)].map(match => match[1]),
    toolResults: messages.flatMap(message => message.getToolCallResults()).map(result => ({
      toolCallId: result.toolCallId, contentSha256: hash(result.content), contentChars: result.content.length,
    })) };
}

async function oneRun(model, entry, group, seed, engineLookup) {
  const recorder = { calls: [] };
  const history = Chat.empty();
  history.append("system", fixture.SYSTEM_PROMPT);
  // Earlier turn: the engine was identified, as in a real session.
  history.append("user", "Which Unreal Engine version does the active project use?");
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "seed_engine", name: "unreal_symbol_lookup", arguments: { symbol: "AActor" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "seed_engine",
    content: JSON.stringify(engineLookup("AActor")) }] }));
  history.append("assistant", `The installed engine source is Unreal Engine ${fixture.ENGINE_BUILD.version}.`);
  history.append("user", `${entry.question}\n${VERDICT_LINE}`);
  const predictions = [], turnMessages = [], inputObservations = [];
  const fixedModel = { identifier: model.identifier, getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options), countTokens: text => model.countTokens(text),
    act: (chat, actTools, options) => {
      inputObservations.push({ ordinal: inputObservations.length + 1, ...observeModelInput(chat) });
      return model.act(chat, actTools, { ...options, temperature: 0, seed,
      onMessage(message) { turnMessages.push(message); options.onMessage?.(message); },
      onPredictionCompleted(result) {
        // Store settings and counters, never reasoningContent or hidden text.
        predictions.push({ stats: result.stats, modelInfo: result.modelInfo,
          requestedMaxTokens: options.maxTokens,
          loadConfig: result.loadConfig, predictionConfig: result.predictionConfig });
        options.onPredictionCompleted?.(result);
      },
    }); } };
  const session = { tools: tools(recorder, entry, engineLookup), [Symbol.dispose]() {} };
  // "off" uses the product's own disable switch (a zero guidance budget).
  const config = group === "off" ? { designGuidanceMode: "auto", designGuidanceMaxTokens: 0 } : { designGuidanceMode: "auto" };
  const ctl = controller(history, fixedModel, session, config);
  const started = Date.now();
  let error = null;
  try { await createPredictionLoopHandler()(ctl); } catch (caught) { error = String(caught?.message || caught); }
  const answer = ctl.blocks.filter(block => block.options?.style?.type !== "thinking").map(block => block.text.trim()).filter(Boolean).join("\n");
  const verdict = parseVerdict(answer);
  let followup = null;
  if (entry.pair === 7 && !error) {
    for (const message of turnMessages) history.append(message);
    history.append('user', `Prepare the final findings from the same evidence. State whether F-1 is retained or withdrawn. ${VERDICT_LINE}`);
    const next = controller(history, fixedModel, session, config);
    try { await createPredictionLoopHandler()(next); } catch (caught) { error = String(caught?.message || caught); }
    const finalAnswer = next.blocks.filter(block => block.options?.style?.type !== 'thinking').map(block => block.text.trim()).filter(Boolean).join('\n');
    followup = { answer: finalAnswer, verdict: parseVerdict(finalAnswer) };
    ctl.debugValues.push(...next.debugValues);
  }
  const graded = fixture.gradeCase(entry.id, error ? { label: 'invalid' } : verdict);
  const guidance = ctl.debugValues.filter(value => value.event === "design_guidance_input");
  const rounds = ctl.debugValues.filter(value => value.event === "direct_round_observation");
  return { caseId: entry.id, group, seed, elapsedMs: Date.now() - started, error, verdict, graded,
    rounds: rounds.length, toolCalls: recorder.calls.length,
    promptTokens: rounds.reduce((sum, round) => sum + Number(round.predictionStats?.promptTokensCount || 0), 0),
    guidanceIncludedRounds: guidance.filter(value => value.outcome === "included").length,
    guidanceSelectedIds: [...new Set(guidance.flatMap(value => value.selectedIds || []))],
    guidanceTopics: [...new Set(guidance.map(value => `${value.primaryTopic || ""}/${value.supplementTopic || ""}`))],
    answer, answerTail: answer.slice(-600), followup, calls: recorder.calls, predictions, inputObservations,
    roundObservations: rounds, guidanceObservations: guidance,
    seedVerification: predictions.length && predictions.every(prediction => prediction.predictionConfig?.fields
      ?.some(field => field.key === 'llm.prediction.seed')) ? 'returned_by_runtime' : 'requested_only_not_returned_by_runtime',
    review: { status: 'pending', rationale: entry.rationale, evidence: entry.evidence,
      instructions: 'Review cause, cited source ranges, severity and counterevidence independently. P7 also needs explicit withdrawal and follow-up inspection. Label credit is not semantic credit.' } };
}

function validateResume(saved, identity) {
  if (saved.schemaVersion !== SCHEMA_VERSION || saved.identityHash !== hash(JSON.stringify(identity)))
    throw new Error('Incompatible evaluation schema/build/fixture/settings. Use a new output path; old runs cannot be mixed.');
  if (!Array.isArray(saved.runs)) throw new Error('Invalid evaluation runs');
  const keys = saved.runs.map(run => `${run.rep}|${run.caseId}|${run.group}`);
  if (new Set(keys).size !== keys.length) throw new Error('Duplicate evaluation runs');
  return saved.runs;
}

function assertBuiltIdentity() {
  const source = require('./build-runtime-identity.cjs').renderIdentity(runtimeRoot);
  const expected = source.match(/ef-build-v2-[a-f0-9]{64}/u)?.[0];
  if (expected !== require(path.join(runtimeRoot, 'dist/generated-runtime-identity.js')).LOADED_BUILD_ID)
    throw new Error('Compiled evaluation build is stale. Run npm run build before evaluating.');
}

function observedConfigHash(prediction) {
  if (!prediction.loadConfig || !prediction.predictionConfig) throw new Error('Effective model settings were not observed.');
  const cap = prediction.predictionConfig.fields?.find(field => field.key === 'llm.prediction.maxPredictedTokens')?.value;
  const explicitCap = Number.isSafeInteger(prediction.requestedMaxTokens) && prediction.requestedMaxTokens > 0;
  if (explicitCap && (!cap || cap.checked !== true || cap.value !== prediction.requestedMaxTokens))
    throw new Error('Effective output cap differs from the requested harness budget.');
  const stable = value => {
    if (Array.isArray(value)) {
      // Seed is intentionally varied per repetition/case; tools are request
      // definitions and are fingerprinted by runner/build identity instead.
      const fields = value.filter(item => !['llm.prediction.seed', 'llm.prediction.tools',
        ...(explicitCap ? ['llm.prediction.maxPredictedTokens'] : [])].includes(item?.key));
      return fields.map(stable).sort((a, b) => a?.key && b?.key ? a.key.localeCompare(b.key) : 0);
    }
    return value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
  };
  return hash(JSON.stringify(stable({ load: prediction.loadConfig, prediction: prediction.predictionConfig })));
}

function assertConsistentSettings(runs, candidate) {
  const observed = [...runs, candidate].flatMap(run => run.predictions || []).map(observedConfigHash);
  if (new Set(observed).size > 1) throw new Error('Effective model settings changed. Run excluded; use a new output path.');
}

async function main() {
  assertBuiltIdentity();
  const identifier = valueAfter("--model", "swift-1.5-qwen3.8-27b");
  const reps = Number(valueAfter("--reps", "1"));
  const output = path.resolve(valueAfter("--output", "ue-accuracy-eval.json"));
  const only = valueAfter("--cases", "");
  // --resume keeps the runs already in --output and skips them; --pause-ms rests
  // between runs, or with --pause-at rep only when a repetition ends (machine
  // load), never before the first run executed here or after the last.
  const pauseMs = Number(valueAfter("--pause-ms", "0"));
  const pauseAtRep = valueAfter("--pause-at", "run") === "rep";
  const groups = valueAfter('--groups', 'off,auto').split(',');
  const seedBase = Number(valueAfter('--seed-base', '7000'));
  if (!Number.isSafeInteger(reps) || reps < 1 || !Number.isSafeInteger(seedBase) || seedBase < 0
    || !Number.isFinite(pauseMs) || pauseMs < 0 || !groups.length || groups.some(group => !['off', 'auto'].includes(group))
    || new Set(groups).size !== groups.length) throw new Error('Invalid repetition, seed, pause or group options');
  const engineRoot = path.resolve(valueAfter('--engine-root', 'C:/Program Files/Epic Games/UE_5.8'));
  const engineLookup = createEngineLookup(engineRoot);
  const allCases = fixture.CASES.filter(entry => entry.variant !== 'scoring_rule');
  const cases = allCases.filter(entry => !only || only.split(',').includes(entry.id));
  if (!cases.length || (only && only.split(',').some(id => !allCases.some(entry => entry.id === id)))) throw new Error('Unknown/empty case selection');
  if (fs.existsSync(output) && !process.argv.includes('--resume')) throw new Error('Output already exists; use --resume or a new path.');
  const model = await new LMStudioClient().llm.model(identifier);
  const identity = { model: identifier, modelInfo: await model.getModelInfo(), contextLength: await model.getContextLength(),
    promptTemplateProbeSha256: hash(await model.applyPromptTemplate(Chat.from([
      { role: 'system', content: 'Evaluation identity probe.' }, { role: 'user', content: 'Probe.' },
    ]))),
    buildId: require(path.join(runtimeRoot, 'dist/generated-runtime-identity.js')).LOADED_BUILD_ID,
    runtimeRoot,
    fixtureSha256: hash(fs.readFileSync(require.resolve('./ue-accuracy-fixture.cjs'))),
    runnerSha256: hash(fs.readFileSync(__filename)), engine: fixture.ENGINE_BUILD, engineRoot,
    sdkVersion: JSON.parse(fs.readFileSync(path.resolve(path.dirname(require.resolve('@lmstudio/sdk')), '../package.json'), 'utf8')).version,
    temperature: 0, seedBase, cases: cases.map(entry => entry.id), groups,
    inheritedModelSettings: 'Verified across all completed predictions; changed effective settings reject the candidate run.' };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const lock = fs.openSync(`${output}.lock`, 'wx');
  try {
  const runs = process.argv.includes("--resume") && fs.existsSync(output) ? validateResume(JSON.parse(fs.readFileSync(output, "utf8")), identity) : [];
  const save = summary => {
    const temporary = `${output}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ schemaVersion: SCHEMA_VERSION, identity, identityHash: hash(JSON.stringify(identity)),
      updatedAt: new Date().toISOString(), summary, runs }, null, 1), { flag: 'wx' });
    fs.renameSync(temporary, output);
  };
  const done = new Set(runs.map(run => `${run.rep}|${run.caseId}|${run.group}`));
  let ran = false, lastRep = 0;
  for (let rep = 1; rep <= reps; rep++) {
    for (const entry of cases) {
      const index = allCases.indexOf(entry); // A filtered rerun keeps the same seed/order.
      // AB/BA alternation per case and repetition.
      const order = (index + rep) % 2 ? ["off", "auto"] : ["auto", "off"];
      for (const group of order) {
        if (!groups.includes(group)) continue;
        if (done.has(`${rep}|${entry.id}|${group}`)) continue;
        if (ran && pauseMs > 0 && (!pauseAtRep || rep !== lastRep)) await new Promise(resolve => setTimeout(resolve, pauseMs));
        ran = true; lastRep = rep;
        const run = await oneRun(model, entry, group, seedBase + rep * 100 + index, engineLookup);
        assertConsistentSettings(runs, run);
        runs.push({ rep, ...run });
        process.stdout.write(`${JSON.stringify({ rep, caseId: run.caseId, group, label: run.verdict.label, severity: run.verdict.severity,
          score: run.graded.score, guidanceIncludedRounds: run.guidanceIncludedRounds, rounds: run.rounds, elapsedMs: run.elapsedMs, error: run.error })}\n`);
        save(null);
      }
    }
  }
  const summary = Object.fromEntries(groups.map(group => {
    const byRep = [...new Set(runs.map(run => run.rep))].map(rep => fixture.summarize(
      runs.filter(run => run.group === group && run.rep === rep).map(run => run.graded)).correctness);
    return [group, byRep];
  }));
  save(summary);
  process.stdout.write(`${JSON.stringify(summary)}\n`);
  } finally { fs.closeSync(lock); fs.unlinkSync(`${output}.lock`); }
}

if (require.main === module) main().catch(error => { console.error(error); process.exit(1); });
module.exports = { SCHEMA_VERSION, hash, parseVerdict, caseHandlers, createEngineLookup, validateResume, assertConsistentSettings, observeModelInput };
