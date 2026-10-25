"use strict";
// Compatibility fix for @lmstudio/sdk 1.5.0 internalAct: handlePredictionEnd
// resolves predictionPromise without marking its channel finished. Aborting
// after returned tool results then sends cancel to that already closed channel.
// Also preserve effective guard arguments and completed peer results on failure.
// Every replacement is version/anchor checked before either bundle is written.
const fs = require("node:fs"), path = require("node:path");
const root = path.join(__dirname, "../node_modules/@lmstudio/sdk");
const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
if (version !== "1.5.0") throw new Error(`SDK lifecycle patch must be reviewed for version ${version}`);
const before = "handlePredictionEnd: endPacket => {\n                const predictionResult = makePredictionResult({";
const after = "handlePredictionEnd: endPacket => {\n                finished = true; // context-compactor: completed prediction channel\n                const predictionResult = makePredictionResult({";
const patches = [
  [before, after],
  ['await baseOpts.guardToolCall(predictionsPerformed, callId, new GuardToolCallController(request, tool, resultContainer));',
    'await baseOpts.guardToolCall(predictionsPerformed, callId, new GuardToolCallController(request, tool, resultContainer));\n                        abortController.signal.throwIfAborted(); // context-compactor: guard may outlive its round'],
  ['// Now we need to call RequestFinalized\n', '// context-compactor: a closed round cannot dispatch a new tool.\n                    abortController.signal.throwIfAborted();\n                    // Now we need to call RequestFinalized\n'],
  [`// Now we need to call RequestFinalized
                    safeCallCallback(logger, "onToolCallRequestFinalized", baseOpts.onToolCallRequestFinalized, [
                        predictionsPerformed,
                        callId,
                        {
                            toolCallRequest: request,`, `// Now we need to call RequestFinalized
                    safeCallCallback(logger, "onToolCallRequestFinalized", baseOpts.onToolCallRequestFinalized, [
                        predictionsPerformed,
                        callId,
                        {
                            toolCallRequest: pushedRequest, // context-compactor: effective request`],
  ['const toolCallResults = [];', 'const toolCallResults = [];\n        let assistantMessageEmitted = false, roundFailed = false; // context-compactor: failed batch handoff'],
  ['// Append and emit the assistant message.', '// Append and emit the assistant message.\n            if (roundFailed) return; // context-compactor: closed failure boundary'],
  ['safeCallCallback(logger, "onMessage", baseOpts.onMessage, [assistantMessage]);',
    'assistantMessageEmitted = true; // context-compactor: request emission\n            safeCallCallback(logger, "onMessage", baseOpts.onMessage, [assistantMessage]);'],
  ['await finalPromise;', `try {
            await finalPromise;
        } catch (error) {
            // context-compactor: preserve observations completed before failure.
            // No waiting or retrying effects whose completion is unknown.
            roundFailed = true;
            abortController.abort(error);
            if (toolCallResults.length) {
                const completedIds = new Set(toolCallResults.map(result => result.data.toolCallId));
                if (!assistantMessageEmitted) {
                    const requests = toolCallRequests.filter(request => completedIds.has(request.id));
                    safeCallCallback(logger, "onMessage", baseOpts.onMessage, [ChatMessage.from({
                        role: "assistant", content: requests.map(toolCallRequest => ({type: "toolCallRequest", toolCallRequest})),
                    })]);
                    assistantMessageEmitted = true;
                }
                safeCallCallback(logger, "onMessage", baseOpts.onMessage, [ChatMessage.from({
                    role: "tool", content: toolCallResults.sort((a, b) => a.index - b.index).map(result => result.data),
                })]);
            }
            throw error;
        }`],
];
const updates = ["index.cjs", "index.mjs"].map(name => {
  const file = path.join(root, "dist", name);
  let source = fs.readFileSync(file, "utf8");
  for (const [original, replacement] of patches) {
    if (source.includes(replacement)) continue;
    if (source.split(original).length !== 2) throw new Error(`SDK contract patch target changed in ${name}: ${original.slice(0, 70)}`);
    source = source.replace(original, replacement);
  }
  return {file, source};
});
for (const {file, source} of updates) fs.writeFileSync(file, source);
