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

// The statement that declares a symbol: a prototype through its ";" (or an
// inline definition's signature through its "{"), or a member variable through
// its ";", plus an adjacent UFUNCTION/UPROPERTY annotation. "Owner::Name" looks
// only at the owner's class/struct body at brace depth zero. Lexical only.
function lexicalOwnerRegion(masked, owner) {
  const escaped = owner.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`\\b(?:class|struct|enum(?:\\s+class)?)\\s+(?:\\w+_API\\s+)?${escaped}\\b`, "g");
  const regions = [];
  for (const match of masked.matchAll(pattern)) {
    const from = match.index + match[0].length;
    const open = masked.indexOf("{", from);
    if (open < 0 || open >= from + 2048 || masked.slice(from, open).includes(";")) continue;
    const close = balancedEnd(masked, open, "{", "}");
    if (close >= 0) regions.push({ start: match.index, open, close });
  }
  return regions.length === 1 ? regions[0] : null;
}

function statementEnd(masked, from) {
  let paren = 0, brace = 0;
  for (let i = from; i < masked.length && i < from + 8192; i++) {
    const c = masked[i];
    if (c === "(") paren++;
    else if (c === ")") paren--;
    else if (paren === 0 && c === "{") { if (/\)\s*(?:const|override|final|noexcept|\s)*$/.test(masked.slice(Math.max(from, i - 256), i))) return i; brace++; }
    else if (paren === 0 && c === "}") { if (--brace < 0) return -1; }
    else if (paren === 0 && brace === 0 && c === ";") return i;
  }
  return -1;
}

function lexicalDeclarationWindow(content, symbolValue, contextLinesValue) {
  const symbol = String(symbolValue || "").trim(), parts = symbol.split("::");
  if (!parts.every(part => /^[A-Za-z_~][A-Za-z0-9_~]*$/.test(part)) || parts.length > 2)
    return failure("INVALID_SYMBOL", "symbol must be Name or Owner::Name", { retryAllowed: true });
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const masked = lexicalSource(content);
  const name = parts.at(-1);
  let scopeStart = 0, scopeEnd = masked.length;
  if (parts.length === 2) {
    const region = lexicalOwnerRegion(masked, parts[0]);
    if (!region) return failure("SYMBOL_NOT_FOUND", `No class/struct ${parts[0]} body found in this file.`, { retryAllowed: true });
    scopeStart = region.open + 1; scopeEnd = region.close;
  }
  const candidates = [];
  for (const match of masked.slice(0, scopeEnd).matchAll(new RegExp(`(?<![\\w~])${escape(name)}\\b`, "g"))) {
    if (match.index < scopeStart) continue;
    const before = masked.slice(scopeStart, match.index);
    if (parts.length === 2 && (before.split("{").length - before.split("}").length) !== 0) continue;
    const boundary = Math.max(before.lastIndexOf(";"), before.lastIndexOf("{"), before.lastIndexOf("}")) + 1;
    const raw = before.slice(boundary);
    // Blank (length-preserving) annotations, GENERATED_*BODY() and access labels.
    let head = raw.replace(/\b(?:public|protected|private)\s*:/g, label => " ".repeat(label.length));
    const annotations = [];
    for (const a of raw.matchAll(/\b(?:UFUNCTION|UPROPERTY|UE_DEPRECATED\w*|GENERATED_\w*BODY)\s*\(/g)) {
      const close = balancedEnd(raw, a.index + a[0].length - 1, "(", ")");
      if (close < 0) continue;
      head = head.slice(0, a.index) + " ".repeat(close - a.index + 1) + head.slice(close + 1);
      if (!a[0].startsWith("GENERATED_")) annotations.push({ index: a.index, close });
    }
    // A call, assignment, member access or qualified reference is not a declaration.
    if (/[=(.,]|->|::\s*$|\breturn\b/.test(head) || !/[\w>*&]\s*$/.test(head)) continue;
    const rest = masked.slice(match.index + name.length);
    if (!/^\s*(?:\(|=|;|\[|\{|:\s*\d)/.test(rest)) continue;
    const end = statementEnd(masked, match.index + name.length);
    if (end < 0) continue;
    // Start at the declaration's first token, or at an annotation directly before it.
    const own = head.search(/\S/);
    const adjacent = annotations.filter(a => a.index < own && !/\S/.test(head.slice(a.close + 1, own))).at(-1);
    candidates.push({ start: scopeStart + boundary + (adjacent ? adjacent.index : own), end });
  }
  if (!candidates.length) return failure("SYMBOL_NOT_FOUND", `No lexical declaration found: ${symbol}. Search it with contextLines or read an exact range.`, { retryAllowed: true });
  const allLines = content.split(/\r?\n/), lineAt = offset => content.slice(0, offset).split(/\r?\n/).length;
  const contextLines = clamp(contextLinesValue, 0, 0, 30), candidate = candidates[0];
  return { ok: true, symbol, allLines,
    start: Math.max(1, lineAt(candidate.start) - contextLines),
    end: Math.min(allLines.length, lineAt(candidate.end) + contextLines),
    candidateCount: candidates.length, candidateLines: candidates.slice(0, 16).map(value => lineAt(value.start)),
    selection: "first_lexical_declaration; overload_resolution_unverified" };
}

module.exports = { lexicalDeclarationWindow, lexicalSymbolWindow, lexicalOwnerRegion, lexicalSource };
