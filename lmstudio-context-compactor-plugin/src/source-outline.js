"use strict";

// When a body projection drops a project C++/C# source body, a few declaration
// names with line spans let the model re-read one span with read_file_range
// instead of the whole file. This is a light text heuristic, not a parser:
// spans are navigation hints, never evidence for a claim about the code.
const SOURCE_PATH = /\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx|inl|cs)$/iu;
const OUTLINE_MAX_ENTRIES = 32;
const OUTLINE_MAX_CHARS = 1536;
const DECLARATION_SCAN_LINES = 12;
const REFLECTION_MACRO = /^(UFUNCTION|UPROPERTY|UCLASS|USTRUCT|UENUM|UINTERFACE)\s*\(/u;
const TYPE_DECLARATION = /^(?:(?:public|private|protected|internal|static|sealed|abstract|partial|readonly|unsafe|template\s*<[^>]*>)\s+)*(class|struct|enum(?:\s+class)?|interface)\s+(?:[A-Z][A-Z0-9_]*_API\s+)?([A-Za-z_]\w*)/u;
const NAME = /^(?:[A-Za-z_]\w*::)*~?[A-Za-z_]\w*$/u;
const NOT_A_DECLARATION = new Set(["if", "for", "foreach", "while", "switch", "catch", "return", "else", "new", "delete",
  "throw", "case", "goto", "sizeof", "using", "typedef", "await", "yield", "co_return", "co_await", "lock", "base", "this",
  "nameof", "typeof", "default", "do"]);

/** Project source only: engine/plugin observations use another scheme. */
function isProjectSourcePath(value) {
  if (typeof value !== "string" || !SOURCE_PATH.test(value)) return false;
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//iu.exec(value);
  return !scheme || scheme[1].toLowerCase() === "project";
}

// Code text per line with comments and string/char literals blanked, so
// braces and parentheses inside them do not shift spans.
function codeLines(body) {
  let block = false;
  return body.split("\n").map(raw => {
    let out = "";
    for (let i = 0; i < raw.length; i++) {
      const c = raw[i], next = raw[i + 1];
      if (block) { if (c === "*" && next === "/") { block = false; i++; } continue; }
      if (c === "/" && next === "*") { block = true; i++; continue; }
      if (c === "/" && next === "/") break;
      if (c === "\"" || c === "'") {
        let j = i + 1;
        while (j < raw.length && raw[j] !== c) j += raw[j] === "\\" ? 2 : 1;
        out += c + c; i = j; continue;
      }
      out += c;
    }
    return out.replace(/\r$/u, "").trim();
  });
}

function callableName(text) {
  if (!text || text.startsWith("#") || /^[:,.]/u.test(text)) return null;
  const open = text.indexOf("(");
  if (open <= 0) return null;
  const head = text.slice(0, open);
  if (/[=.\-"'?]/u.test(head)) return null;
  const tokens = head.replace(/[*&]/gu, " ").split(/\s+/u).filter(Boolean);
  const name = tokens.at(-1);
  if (!name || !NAME.test(name) || NOT_A_DECLARATION.has(tokens[0]) || NOT_A_DECLARATION.has(name.split("::").at(-1))) return null;
  // A bare call has no return type. An out-of-line constructor has Class::
  // but, unlike a qualified call such as Super::BeginPlay();, no semicolon.
  return tokens.length >= 2 || (name.includes("::") && !text.endsWith(";")) ? name : null;
}

function propertyName(text) {
  const head = text.split(/[;={]/u)[0].replace(/\[[^\]]*\]/gu, "").trim();
  const name = head.split(/[\s*&]+/u).filter(Boolean).at(-1);
  return name && /^[A-Za-z_]\w*$/u.test(name) ? name : null;
}

/** Last line of the declaration at index: its matching close brace, or the
 * first `;` when a semicolon ends it before any body opens. */
function declarationEnd(lines, index) {
  let depth = 0, opened = false;
  for (let i = index; i < lines.length; i++) {
    for (const c of lines[i]) {
      if (c === ";" && !opened) return i;
      if (c === "{") { depth++; opened = true; }
      else if (c === "}" && opened && --depth === 0) return i;
    }
    if (!opened && i - index >= DECLARATION_SCAN_LINES) return index;
  }
  return opened ? lines.length - 1 : index;
}

function sourceOutline(body, { path: sourcePath, startLine, offsetBytes, lineNumbers = false } = {}) {
  if (typeof body !== "string" || !body || !isProjectSourcePath(sourcePath)) return null;
  if (offsetBytes > 0 && !(Number.isSafeInteger(startLine) && startLine > 0)) return null;
  // The plugin is compiled/shipped independently of the MCP shared JS tree.
  if (lineNumbers) body = body.replace(/^\d+\t/gmu, "");
  const first = Number.isSafeInteger(startLine) && startLine > 0 ? startLine : 1;
  const lines = codeLines(body), entries = [];
  let omitted = 0, chars = 0;
  const add = (start, end, label, name) => {
    const span = start === end ? `${first + start}` : `${first + start}-${first + end}`;
    const entry = `${span} ${label} ${name}`;
    if (entries.length >= OUTLINE_MAX_ENTRIES || chars + entry.length + 3 > OUTLINE_MAX_CHARS) { omitted++; return; }
    entries.push(entry); chars += entry.length + 3;
  };
  for (let i = 0; i < lines.length; i++) {
    const text = lines[i];
    if (!text) continue;
    const macro = REFLECTION_MACRO.exec(text);
    if (macro) {
      // The reflected declaration follows its macro, whose specifiers may span
      // lines, possibly after more macros.
      let j = i, depth = 0;
      do { for (const c of lines[j]) depth += c === "(" ? 1 : c === ")" ? -1 : 0; j++; }
      while (depth > 0 && j < lines.length && j - i <= DECLARATION_SCAN_LINES);
      while (j < lines.length && (!lines[j] || REFLECTION_MACRO.test(lines[j]))) j++;
      if (j >= lines.length) continue;
      const type = TYPE_DECLARATION.exec(lines[j]);
      if (type) continue; // The type line itself is outlined on its own visit.
      const name = macro[1] === "UPROPERTY" ? propertyName(lines[j]) : callableName(lines[j]) || propertyName(lines[j]);
      if (!name) continue;
      const end = macro[1] === "UPROPERTY" ? j : declarationEnd(lines, j);
      add(i, end, macro[1], name);
      i = end;
      continue;
    }
    const type = TYPE_DECLARATION.exec(text);
    if (type) {
      const end = declarationEnd(lines, i);
      // A forward declaration has no body to navigate to.
      if (end === i && text.endsWith(";")) continue;
      add(i, end, type[1].split(/\s+/u)[0], type[2]);
      continue; // Members inside the type are outlined too.
    }
    const name = callableName(text);
    if (!name) continue;
    const end = declarationEnd(lines, i);
    add(i, end, "fn", name);
    i = end;
  }
  if (!entries.length) return null;
  return { lineUnit: "source_file_line", approximate: true, entries, entriesOmitted: omitted,
    use: "Heuristic declaration spans, not evidence; re-read one span with a line-range source read (read_file_range) instead of the whole file." };
}

module.exports = { sourceOutline, isProjectSourcePath };
