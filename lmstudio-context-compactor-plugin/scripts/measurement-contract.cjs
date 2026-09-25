"use strict";
const { visibleAssistantText } = require('../dist/continuity-text');

function visibleBlocks(blocks) {
  return blocks.filter(b => b.roleOverride === 'assistant' && b.includeInContext !== false
    && b.style?.type !== 'thinking').map(b => visibleAssistantText(b.text || '')).join('\n');
}

function evaluateCompletion({ text, finishReason, calls, oracle, failure }) {
  const visible = visibleAssistantText(text || '').trim();
  let answer = null;
  try { answer = JSON.parse(visible.replace(/^```(?:json)?\s*/u, '').replace(/\s*```$/u, '')); } catch { /* No valid final mapping. */ }
  const generationCompleted = !failure && ['eosFound', 'stopStringFound'].includes(finishReason);
  const duplicateReads = calls.length - new Set(calls).size;
  const mappingCorrect = Boolean(answer && Object.entries(oracle).every(([file, value]) => answer[file] === value));
  const allReadOnce = Object.keys(oracle).every(file => calls.filter(call => call === file).length === 1);
  return { generationCompleted, mappingCorrect, allReadOnce, duplicateReads, visible,
    success: generationCompleted && mappingCorrect && allReadOnce && duplicateReads === 0 };
}
module.exports = { visibleBlocks, evaluateCompletion };
