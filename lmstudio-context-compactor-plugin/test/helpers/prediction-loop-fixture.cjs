"use strict";
// Shared fake LM Studio controller for prediction-loop handler tests.
const { createPredictionLoopHandler: productionHandler } = require("../../dist/prediction-loop.js");
// These fixtures must not inherit the developer machine's saved Allow all policy.
function createPredictionLoopHandler(...args) {
  return productionHandler(args[0], args[1], args[2], args[3], () => ({}));
}

function fakeController(history, tokenSource, overrides = {}, tools = []) {
  const blocks = [];
  const statuses = [];
  const config = {
    projectEngine: "auto",
    projectIdentity: "",
    // Keep existing fixture scenarios on the compatibility path. Fresh config
    // behavior is covered separately by the explicit undefined-value test.
    contextManagementMode: "legacy",
    observeOnly: false,
    showDebugInfo: true,
    softRemainingTokens: 14000,
    hardRemainingTokens: 8000,
    maxOutputReserve: 4096,
    safetyMarginTokens: 1024,
    assumedContextLength: 32768,
    recentCompleteTurns: 0,
    compactAboveMessageCount: 4,
    maxCheckpointChars: 12000,
    maxToolResultChars: 1200,
    ...overrides,
  };
  const session = {
    tools,
    disposed: false,
    [Symbol.dispose]() { this.disposed = true; },
  };
  const ctl = {
    abortSignal: new AbortController().signal,
    guardAbort() {},
    getPluginConfig() { return { get: (key) => config[key] }; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return session; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    createToolStatus(callId, initialState) {
      const status = { callId, state: initialState, states: [initialState] };
      statuses.push(status);
      return { setStatus(state) { status.state = state; status.states.push(state); } };
    },
    debug(value) { ctl.debugValue = value; },
    createStatus(initialState) {
      const status = { state: initialState, texts: [initialState.text], states: [initialState], removed: false };
      statuses.push(status);
      return {
        setText(text) { status.state = { ...status.state, text }; status.texts.push(text); },
        setState(state) { status.state = state; status.states.push(state); },
        remove() { status.removed = true; },
      };
    },
    createContentBlock(options) {
      const block = { options, text: "", requests: [], results: [], styles: [] };
      blocks.push(block);
      return {
        appendText(text) { block.text += text; },
        appendToolRequest(request) { block.requests.push(request); },
        replaceToolRequest(request) {
          const index = block.requests.findIndex(value => value.callId === request.callId);
          if (index >= 0) block.requests[index] = request;
        },
        appendToolResult(result) { block.results.push(result); },
        setStyle(style) { block.styles.push(style); block.options.style = style; },
      };
    },
    blocks,
    statuses,
    session,
    debugValues: [],
    debugValue: null,
  };
  ctl.debug = (value) => {
    ctl.debugValue = value;
    ctl.debugValues.push(value);
  };
  return ctl;
}

module.exports = { fakeController, createPredictionLoopHandler, handlePredictionLoop: createPredictionLoopHandler() };
