"use strict";

// Lexical candidates only. Preserve original offsets while masking comments
// and literals; macro expansion, inheritance and overload choice stay unknown.
const { failure } = require("./direct-response.js");
const { clamp } = require("./direct-runtime-shared.js");

function lexicalSource(content) {
  return content.replace(/R"([^ ()\\\t\r\n]{0,16})\([\s\S]*?\)\1"|\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g,
    value => value.replace(/[^\r\n]/g, " "));
}

function balancedEnd(source, opening, left, right) {
  let depth = 0;
  for (let i = opening; i < source.length; i++) {
    if (source[i] === left) depth++;
    else if (source[i] === right && --depth === 0) return i;
  }
  return -1;
}

function lexicalSymbolWindow(content, symbolValue, contextLinesValue) {
  const symbol = String(symbolValue || "").trim(), parts = symbol.split("::");
  if (!parts.every(part => /^[A-Za-z_~][A-Za-z0-9_~]*$/.test(part)))
    return failure("INVALID_SYMBOL", "symbol must be a C/C++ identifier", { retryAllowed: true });
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const masked = lexicalSource(content);
  const qualified = new RegExp(`(?<![\\w])${parts.map(escape).join("\\s*::\\s*")}\\s*\\(`, "gm");
  const matches = [...masked.matchAll(qualified)];
  const candidates = [];
  // An owner-qualified request must not silently select another owner's leaf.
  // Inline declarations can be inspected with a leaf request or exact range.
  for (const match of matches) {
    const argumentsEnd = balancedEnd(masked, match.index + match[0].lastIndexOf("("), "(", ")");
    if (argumentsEnd < 0) continue;
    const brace = masked.indexOf("{", argumentsEnd + 1);
    if (brace < 0) continue;
    const suffix = masked.slice(argumentsEnd + 1, brace);
    // Calls/declarations and control expressions must not borrow the next
    // function's body. Complex constructor/requirement syntax stays a range read.
    if (suffix.length > 1024 || !/^\s*(?:(?:const|volatile|override|final|noexcept(?:\s*\([^;{}]*\))?|[&]{1,2})\s*)*(?:->\s*[\w\s:*&<>]+)?\s*$/.test(suffix)) continue;
    const endOffset = balancedEnd(masked, brace, "{", "}");
    if (endOffset >= 0) candidates.push({ match, endOffset });
  }
  if (!candidates.length) return failure(matches.length ? "SYMBOL_BODY_NOT_FOUND" : "SYMBOL_NOT_FOUND",
    `No bounded lexical definition found: ${symbol}. Search the declaration and read its exact range.`, { retryAllowed: true });
  const allLines = content.split(/\r?\n/), lineAt = offset => content.slice(0, offset).split(/\r?\n/).length;
  const contextLines = clamp(contextLinesValue, 3, 0, 30), candidate = candidates[0];
  return { ok: true, symbol, allLines,
    start: Math.max(1, lineAt(candidate.match.index) - contextLines),
    end: Math.min(allLines.length, lineAt(candidate.endOffset) + contextLines),
    candidateCount: candidates.length, candidateLines: candidates.slice(0, 16).map(value => lineAt(value.match.index)),
    selection: "first_lexical_definition; overload_resolution_unverified" };
}

module.exports = { lexicalSymbolWindow };
