"use strict";

// Conversation 1794506466121: a range edit succeeded with
//   UFUNCTION(BlueprintCallable, Category=\"PF|LaunchReaction\")
// in its newText, the quotes still escaped as in the JSON read result the
// model copied them from. The header no longer compiled, and eleven failed
// calls followed while the model tried to repair it. In C-family source a
// backslash before a quote is only valid inside a string or character
// literal, so a line whose only quotes are escaped is that copy artifact.

const C_FAMILY_SOURCE = /\.(?:h|hh|hpp|hxx|inl|ipp|tpp|c|cc|cpp|cxx|cs)$/iu;

/** The code of one line: line comment removed, char literals blanked, a block
 * comment state carried from the previous line. */
function codeOfLine(line, state) {
  let code = "", inString = false;
  for (let index = 0; index < line.length; index++) {
    const char = line[index], next = line[index + 1];
    if (state.blockComment) {
      if (char === "*" && next === "/") { state.blockComment = false; index++; }
      continue;
    }
    if (!inString && char === "/" && next === "/") break;
    if (!inString && char === "/" && next === "*") { state.blockComment = true; index++; continue; }
    if (!inString && char === "'") {
      const literal = /^'(?:\\.|[^'\\])'/u.exec(line.slice(index));
      if (literal) { index += literal[0].length - 1; continue; }
    }
    if (char === "\\") { code += char + (next ?? ""); index++; continue; }
    if (char === "\"") inString = !inString;
    code += char;
  }
  return code;
}

/** The first line of `text` that would write \" outside any string literal of
 * a C-family source file, unless the file already has that exact line. */
function escapedQuoteIssue(filePath, text, existingContent = "") {
  if (!C_FAMILY_SOURCE.test(String(filePath || "")) || !String(text || "").includes("\\\"")) return null;
  const existing = new Set(String(existingContent || "").split(/\r?\n/u).map(line => line.trim()));
  const state = { blockComment: false };
  const lines = String(text).split(/\r?\n/u);
  for (const [index, line] of lines.entries()) {
    const code = codeOfLine(line, state);
    // A raw string (R"(...)") or a string continued from the line before
    // cannot be judged one line at a time.
    if (/R"[^(\s]*\(/u.test(code) || /\\$/u.test(line)) return null;
    if (!code.includes("\\\"") || /(?:^|[^\\])"/u.test(code.replace(/\\\\/gu, ""))) continue;
    if (existing.has(line.trim())) continue;
    return { line: index + 1, text: line.slice(0, 300), suggestedText: line.replace(/\\([\\"])/gu, "$1").slice(0, 300) };
  }
  return null;
}

function escapedQuoteMessage(issue, label = "newText") {
  return `${label} line ${issue.line} would write \\" outside any string literal: ${JSON.stringify(issue.text)}. `
    + "These are the escapes of a JSON read result copied as text, and the file would not compile. "
    + `Write the quotes as the source has them: ${JSON.stringify(issue.suggestedText)}.`;
}

// Conversation 1794590083146: a range edit succeeded with newText whose lines
// began with the two characters backslash and "t" (the tab escape of a JSON
// read result, sent with its backslash doubled). The .cpp no longer compiled
// (C2017) and the turn spent ten more rounds repairing it. In C and C++ a
// backslash followed by t, n or r is only valid inside a string or character
// literal; outside one it is that copy artifact. C# is excluded: verbatim,
// raw and interpolated strings span lines and nest quotes, and a line-based
// reading of them reports real code (63 lines in UnrealBuildTool).
const C_OR_CPP_SOURCE = /\.(?:h|hh|hpp|hxx|inl|ipp|tpp|c|cc|cpp|cxx)$/iu;
const WHITESPACE_ESCAPE = /\\[tnr]/u;
// The text after these directives is not C++ tokens (<sys\types.h>, a message).
const FREE_TEXT_DIRECTIVE = /^\s*#\s*(?:include|import|pragma|error|warning|line)\b/u;

/** The characters of one line that lie outside string literals, character
 * literals and comments. `state` carries what is open at the end of the line:
 * a block comment, a raw string, or a string continued with a backslash. */
function codeOutsideLiterals(line, state) {
  let code = "", index = 0;
  while (index < line.length) {
    if (state.raw) {
      const end = line.indexOf(state.raw, index);
      if (end < 0) return code;
      index = end + state.raw.length; state.raw = null; continue;
    }
    if (state.block) {
      const end = line.indexOf("*/", index);
      if (end < 0) return code;
      index = end + 2; state.block = false; continue;
    }
    const char = line[index], next = line[index + 1];
    if (state.string) {
      if (char === "\\") { index += 2; continue; }
      if (char === "\"") state.string = false;
      index += 1; continue;
    }
    if (char === "/" && next === "/") return code;
    if (char === "/" && next === "*") { state.block = true; index += 2; continue; }
    if (char === "\"" || /[RuUL8]/u.test(char)) {
      const raw = /^(?:u8|u|U|L)?R"([^()\\\s]{0,16})\(/u.exec(line.slice(index));
      if (raw && (index === 0 || !/\w/u.test(line[index - 1]))) { state.raw = `)${raw[1]}"`; index += raw[0].length; continue; }
    }
    if (char === "\"") { state.string = true; index += 1; continue; }
    if (char === "'") {
      const literal = /^'(?:\\.[^']{0,8}|[^'\\])'/u.exec(line.slice(index));
      if (literal) { index += literal[0].length; continue; }
    }
    code += char; index += 1;
  }
  // An ordinary string ends with its line unless a backslash continues it.
  if (state.string && !line.endsWith("\\")) state.string = false;
  return code;
}

/** The first line of the file after an edit (`updated`) that holds \t, \n or
 * \r outside any string, character literal or comment of a C/C++ source file,
 * unless the file before the edit (`previous`) already had that exact line.
 * The whole file is read so that strings, raw strings and comments opened
 * above the edited lines are respected. */
function escapedWhitespaceIssue(filePath, updated, previous = "") {
  const text = String(updated ?? "");
  if (!C_OR_CPP_SOURCE.test(String(filePath || "")) || !WHITESPACE_ESCAPE.test(text)) return null;
  const existing = new Set(String(previous || "").split(/\r?\n/u).map(line => line.trim()));
  const state = { block: false, raw: null, string: false };
  for (const [index, line] of text.split(/\r?\n/u).entries()) {
    const freeText = !state.block && !state.raw && !state.string && FREE_TEXT_DIRECTIVE.test(line);
    const code = codeOutsideLiterals(line, state);
    if (freeText || !WHITESPACE_ESCAPE.test(code) || existing.has(line.trim())) continue;
    const escapes = [...new Set(code.match(/\\[tnr]/gu))];
    return { line: index + 1, text: line.slice(0, 300), escapes,
      suggestedText: line.replace(/\\t/gu, "\t").replace(/\\r/gu, "").replace(/\\n/gu, "\n").slice(0, 300) };
  }
  return null;
}

/** Words rather than nested escapes: this message is itself delivered as JSON. */
function escapedWhitespaceMessage(issue, label = "newText") {
  const names = issue.escapes.map(escape => `a backslash followed by "${escape[1]}"`).join(" and ");
  return `${label} would write ${names} as plain characters outside any string or comment (file line ${issue.line} after the edit). `
    + "That is the escape of a JSON read result sent with its backslash doubled, and the file would not compile. "
    + "Send real tab and line-break characters: in the tool arguments a tab is the JSON escape with ONE backslash. "
    + "suggestedText holds that line as the source needs it.";
}

/** The refusal for an edit that would write copied JSON escapes, or null.
 * `regions` are the edit's new texts as [{ text, label, editIndex? }],
 * `updated` the whole file after the edit and `previous` the file before it. */
function copiedEscapeRefusal(filePath, regions, updated, previous = "") {
  for (const region of regions) {
    const issue = escapedQuoteIssue(filePath, region.text, previous);
    if (issue) return { errorCode: "ESCAPED_QUOTE_IN_CODE", message: escapedQuoteMessage(issue, region.label),
      details: { ...issue, ...(region.editIndex !== undefined ? { editIndex: region.editIndex } : {}) } };
  }
  const issue = escapedWhitespaceIssue(filePath, updated, previous);
  return issue ? { errorCode: "ESCAPED_WHITESPACE_IN_CODE", details: issue,
    message: escapedWhitespaceMessage(issue, regions.length === 1 ? regions[0].label : "edits[].newText") } : null;
}

/** replace_in_file / apply_edit_bundle regions in the shape copiedEscapeRefusal reads. */
function newTextRegions(regions, indexed) {
  return regions.map((region, index) => ({ text: region.newText,
    ...(indexed ? { label: `edits[${index}].newText`, editIndex: index } : { label: "newText" }) }));
}

module.exports = { escapedQuoteIssue, escapedQuoteMessage, escapedWhitespaceIssue, escapedWhitespaceMessage,
  copiedEscapeRefusal, newTextRegions, C_FAMILY_SOURCE };
