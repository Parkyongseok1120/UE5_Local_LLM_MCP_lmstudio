"use strict";

// Engine independent. Read results can number their lines ("<n>\t<text>") so a
// model cites exact lines instead of counting them. The numbers are display
// only: hashes, receipts and edit matching always use the raw file text.

const LINE_NUMBER_FORMAT = "<n>\\t<text>";
const LINE_NUMBER_PREFIX = /^\d+\t/;
const LINE_NUMBER_PATCH_ERROR = "LINE_NUMBER_PREFIX_IN_PATCH";

function numberLines(lines, firstLine) {
  return lines.map((line, index) => `${firstLine + index}\t${line}`);
}

function stripLineNumberPrefixes(text) {
  return String(text).split("\n").map((line) => line.replace(LINE_NUMBER_PREFIX, "")).join("\n");
}

function hasLineNumberPrefix(text) {
  return String(text).split("\n").some((line) => LINE_NUMBER_PREFIX.test(line));
}

/**
 * Whether `oldText` looks copied from a numbered read: it carries prefixes, is
 * not found as written, but is found once the prefixes are removed. Content that
 * genuinely contains "<digits><TAB>" (for example a TSV) still matches as written
 * and is never rejected.
 */
function isLineNumberPrefixedPatch(oldText, content) {
  const text = String(oldText ?? "");
  if (!text || !hasLineNumberPrefix(text) || content.includes(text)) return false;
  const stripped = stripLineNumberPrefixes(text);
  return stripped.length > 0 && content.includes(stripped);
}

const LINE_NUMBER_PATCH_MESSAGE = `${LINE_NUMBER_PATCH_ERROR}: oldText starts lines with read line numbers ("<n>\\t"). `
  + "Line numbers are display only; remove the numeric prefix and the tab from every line of oldText and retry.";

module.exports = {
  LINE_NUMBER_FORMAT,
  LINE_NUMBER_PATCH_ERROR,
  LINE_NUMBER_PATCH_MESSAGE,
  LINE_NUMBER_PREFIX,
  hasLineNumberPrefix,
  isLineNumberPrefixedPatch,
  numberLines,
  stripLineNumberPrefixes,
};
