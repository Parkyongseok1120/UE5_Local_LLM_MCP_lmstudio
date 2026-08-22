"use strict";

const fs = require("fs");
const path = require("path");

function cqTestRegisteredRoot(testName, testDirectory) {
  const name = String(testName || "").trim().replace(/^\.+|\.+$/g, "");
  const directory = String(testDirectory || "").trim().replace(/^\.+|\.+$/g, "");
  return directory && name ? `${directory}.${name}` : "";
}

// Unreal projects commonly wrap the registered test name in TEXT("...").
// These roots intentionally exclude TEST_METHOD: CQTest methods are children
// of TEST_CLASS registrations, not independently runnable Automation roots.
const AUTOMATION_DECLARATION_PATTERNS = [
  {
    pattern: /\bIMPLEMENT_CUSTOM_[A-Z0-9_]*AUTOMATION_TEST\s*\([^,]+,\s*[^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g,
    registeredName: (match) => String(match[1] || "").trim(),
  },
  {
    pattern: /\bIMPLEMENT_(?!CUSTOM_)[A-Z0-9_]*AUTOMATION_TEST\s*\([^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g,
    registeredName: (match) => String(match[1] || "").trim(),
  },
  {
    pattern: /\b(?:BEGIN_DEFINE_SPEC|DEFINE_SPEC)\s*\([^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g,
    registeredName: (match) => String(match[1] || "").trim(),
  },
  {
    pattern: /\bTEST\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g,
    registeredName: (match) => cqTestRegisteredRoot(match[1], match[2]),
  },
  {
    pattern: /\bTEST_CLASS(?:_WITH_(?:ASSERTS|BASE|FLAGS|BASE_AND_FLAGS))?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g,
    registeredName: (match) => cqTestRegisteredRoot(match[1], match[2]),
  },
];

const AUTOMATION_SOURCE_EXTENSIONS = new Set([
  ".c", ".cc", ".cpp", ".cxx", ".mm",
  ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp",
]);

function isAutomationSourceFile(fileName) {
  return AUTOMATION_SOURCE_EXTENSIONS.has(path.extname(String(fileName || "")).toLowerCase());
}

function cppCodeOffsets(textValue) {
  const text = String(textValue || "");
  const code = new Uint8Array(text.length);
  let index = 0;
  while (index < text.length) {
    if (text.startsWith("//", index)) {
      const newline = text.indexOf("\n", index + 2);
      index = newline < 0 ? text.length : newline;
      continue;
    }
    if (text.startsWith("/*", index)) {
      const close = text.indexOf("*/", index + 2);
      index = close < 0 ? text.length : close + 2;
      continue;
    }
    if (text[index] === "R" && text[index + 1] === '"') {
      const open = text.indexOf("(", index + 2);
      if (open >= 0 && open - (index + 2) <= 16) {
        const delimiter = text.slice(index + 2, open);
        const close = text.indexOf(`)${delimiter}"`, open + 1);
        if (close >= 0) {
          index = close + delimiter.length + 2;
          continue;
        }
      }
    }
    if (text[index] === '"' || text[index] === "'") {
      const quote = text[index];
      index += 1;
      while (index < text.length) {
        if (text[index] === "\\") {
          index += 2;
          continue;
        }
        if (text[index] === quote) {
          index += 1;
          break;
        }
        index += 1;
      }
      continue;
    }
    code[index] = 1;
    index += 1;
  }
  return code;
}

function macroStartsInCode(text, codeOffsets, index) {
  if (codeOffsets[index] !== 1) return false;
  let logicalLineStart = text.lastIndexOf("\n", index - 1) + 1;
  while (logicalLineStart > 0) {
    const previousLineEnd = logicalLineStart - 1;
    const previousLineStart = text.lastIndexOf("\n", previousLineEnd - 1) + 1;
    if (!/\\\s*$/.test(text.slice(previousLineStart, previousLineEnd))) break;
    logicalLineStart = previousLineStart;
  }
  return !/^\s*#\s*define\b/.test(text.slice(logicalLineStart, index));
}

function parseAutomationDeclarations(textValue) {
  const text = String(textValue || "");
  const codeOffsets = cppCodeOffsets(text);
  const names = [];
  const identities = new Set();
  for (const declaration of AUTOMATION_DECLARATION_PATTERNS) {
    declaration.pattern.lastIndex = 0;
    for (const match of text.matchAll(declaration.pattern)) {
      if (!macroStartsInCode(text, codeOffsets, Number(match.index || 0))) continue;
      const name = declaration.registeredName(match);
      const identity = name.toLowerCase();
      if (name && !identities.has(identity)) {
        identities.add(identity);
        names.push(name);
      }
    }
  }
  return names;
}

function dependencyNamesFromBuildFile(buildFile) {
  let text = "";
  try { text = fs.readFileSync(buildFile, "utf8"); } catch {
    return { names: new Set(), complete: false };
  }
  const names = new Set();
  const codeOffsets = cppCodeOffsets(text);
  const propertyPattern = /\b(?:Public|Private|DynamicallyLoaded)DependencyModuleNames\b/g;
  const propertySource = "(?:Public|Private|DynamicallyLoaded)DependencyModuleNames";
  const moduleToken = '"<MODULE>"';
  const moduleList = `(?:${moduleToken}(?:\\s*,\\s*${moduleToken})*\\s*,?)?`;
  const staticStatements = [
    new RegExp(`^${propertySource}\\s*\\.\\s*Add\\s*\\(\\s*${moduleToken}\\s*\\)\\s*;$`, "i"),
    new RegExp(`^${propertySource}\\s*\\.\\s*AddRange\\s*\\(\\s*new\\s+(?:string\\s*)?\\[\\s*\\]\\s*\\{\\s*${moduleList}\\s*\\}\\s*\\)\\s*;$`, "i"),
    new RegExp(`^${propertySource}\\s*\\.\\s*AddRange\\s*\\(\\s*new\\s+(?:System\\.)?(?:Collections\\.Generic\\.)?List\\s*<\\s*string\\s*>\\s*(?:\\(\\s*\\))?\\s*\\{\\s*${moduleList}\\s*\\}\\s*\\)\\s*;$`, "i"),
    new RegExp(`^${propertySource}\\s*=\\s*new\\s+(?:string\\s*)?\\[\\s*\\]\\s*\\{\\s*${moduleList}\\s*\\}\\s*;$`, "i"),
  ];
  let complete = true;
  let consumedUntil = -1;
  for (const match of text.matchAll(propertyPattern)) {
    const start = Number(match.index || 0);
    if (start < consumedUntil || !codeOffsets[start]) continue;
    const semicolon = text.indexOf(";", start);
    if (semicolon < 0 || semicolon - start > 8192) {
      complete = false;
      continue;
    }
    consumedUntil = semicolon + 1;
    const statement = text.slice(start, consumedUntil)
      .replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\r\n]*/g, " ");
    const scrubbed = statement.replace(/"([A-Za-z_][A-Za-z0-9_]*)"/g, moduleToken);
    if (!staticStatements.some((pattern) => pattern.test(scrubbed))) {
      complete = false;
      continue;
    }
    for (const quoted of statement.matchAll(/"([A-Za-z_][A-Za-z0-9_]*)"/g)) {
      names.add(String(quoted[1] || ""));
    }
  }
  return { names, complete };
}

module.exports = {
  AUTOMATION_DECLARATION_PATTERNS,
  dependencyNamesFromBuildFile,
  isAutomationSourceFile,
  parseAutomationDeclarations,
};
