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

module.exports = { escapedQuoteIssue, escapedQuoteMessage, C_FAMILY_SOURCE };
