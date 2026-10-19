"use strict";

// Where an edit was aimed, located in the file's current text, so a failed
// replacement can offer the few lines needed to rebuild an exact oldText
// instead of a fixed file prefix. Lexical only: a candidate is a line that
// contains one of oldText's own distinctive lines; nothing is guessed.

const MAX_CANDIDATES = 5;
const MARGIN_LINES = 10;
const MIN_NEEDLE_CHARS = 6;

function locateOldText(content, oldText) {
  const lines = String(content).split(/\r?\n/u);
  const oldLines = String(oldText).split(/\r?\n/u);
  const needles = oldLines.map((line) => line.trim()).filter((line) => line.length >= MIN_NEEDLE_CHARS)
    .slice(0, 3).sort((a, b) => b.length - a.length);
  for (const needle of needles) {
    const offset = oldLines.findIndex((line) => line.trim() === needle);
    const hits = [];
    lines.forEach((line, index) => { if (line.includes(needle)) hits.push(index + 1 - offset); });
    const candidateLines = [...new Set(hits.filter((line) => line >= 1))].slice(0, MAX_CANDIDATES);
    if (candidateLines.length) {
      const first = candidateLines[0];
      return { candidateLines, startLine: Math.max(1, first - MARGIN_LINES),
        endLine: Math.min(lines.length, first + oldLines.length - 1 + MARGIN_LINES) };
    }
  }
  // No oldText line occurs now: search its longest identifier instead.
  const token = (String(oldText).match(/[A-Za-z_][A-Za-z0-9_]{3,}/gu) || []).sort((a, b) => b.length - a.length)[0];
  return token ? { candidateLines: [], searchQuery: token } : { candidateLines: [] };
}

/** The one narrow next step for a failed or unproven edit of `args.path`. */
function regionSuggestion(args, located, scopedArgs) {
  if (located.candidateLines.length) {
    return { details: { candidateLines: located.candidateLines }, suggestion: { tool: "read_file_range",
      args: scopedArgs(args, { path: args.path, startLine: located.startLine, endLine: located.endLine }) } };
  }
  if (located.searchQuery) {
    return { suggestion: { tool: "search_files",
      args: scopedArgs(args, { path: args.path, query: located.searchQuery, contextLines: 3 }) } };
  }
  return {};
}

module.exports = { locateOldText, regionSuggestion };
