"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { spawn } = require("child_process");
const {
  assertEngineContainment,
  decodeBuildOutput,
  killProcessTree,
} = require("./build-executor");

// Unreal projects commonly wrap the test name in TEXT("..."). Accept both
// forms and arbitrary whitespace/newlines so discovery cannot silently skip
// the project's real Automation suite.
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
const SKIP_DIRS = new Set([".git", "Binaries", "DerivedDataCache", "Intermediate", "Saved"]);
// Keep discovery bounded to Unreal-relevant C/C++ and Objective-C++ source
// artifacts. These extensions are mirrored by unreal_capability_detection.py.
const AUTOMATION_SOURCE_EXTENSIONS = new Set([
  ".c", ".cc", ".cpp", ".cxx", ".mm",
  ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp",
]);
const MAX_AUTOMATION_FILTERS = 256;

function directoryExists(candidate) {
  try { return fs.statSync(candidate).isDirectory(); } catch { return false; }
}

function fileExists(candidate) {
  try { return fs.statSync(candidate).isFile(); } catch { return false; }
}

function cqTestRegisteredRoot(testName, testDirectory) {
  const name = String(testName || "").trim().replace(/^\.+|\.+$/g, "");
  const directory = String(testDirectory || "").trim().replace(/^\.+|\.+$/g, "");
  return directory && name ? `${directory}.${name}` : "";
}

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

function normalizeProjectRelativePath(projectRoot, value) {
  const rawValue = value && typeof value === "object"
    ? value.path || value.relativePath || ""
    : value;
  const raw = String(rawValue || "").trim().replace(/^project:\/\//i, "");
  if (!raw) return "";
  const root = path.resolve(projectRoot);
  const absolute = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(root, raw);
  const relative = path.relative(root, absolute);
  if (!relative || relative === ".") return "";
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return "";
  return relative.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
}

function moduleRootForTarget(projectRoot, target) {
  const relative = normalizeProjectRelativePath(projectRoot, target);
  if (!relative) return "";
  const parts = relative.split("/").filter(Boolean);
  if (parts.length >= 2 && parts[0].toLowerCase() === "source") {
    return parts.slice(0, 2).join("/");
  }
  if (parts.length >= 4 && parts[0].toLowerCase() === "plugins") {
    const sourceIndex = parts.findIndex((part, index) => (
      index >= 2 && part.toLowerCase() === "source" && Boolean(parts[index + 1])
    ));
    if (sourceIndex >= 0) return parts.slice(0, sourceIndex + 2).join("/");
  }
  return "";
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
    new RegExp(
      `^${propertySource}\\s*\\.\\s*AddRange\\s*\\(\\s*new\\s+(?:string\\s*)?\\[\\s*\\]`
        + `\\s*\\{\\s*${moduleList}\\s*\\}\\s*\\)\\s*;$`,
      "i"
    ),
    new RegExp(
      `^${propertySource}\\s*\\.\\s*AddRange\\s*\\(\\s*new\\s+(?:System\\.)?`
        + `(?:Collections\\.Generic\\.)?List\\s*<\\s*string\\s*>\\s*(?:\\(\\s*\\))?`
        + `\\s*\\{\\s*${moduleList}\\s*\\}\\s*\\)\\s*;$`,
      "i"
    ),
    new RegExp(
      `^${propertySource}\\s*=\\s*new\\s+(?:string\\s*)?\\[\\s*\\]`
        + `\\s*\\{\\s*${moduleList}\\s*\\}\\s*;$`,
      "i"
    ),
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
    const statement = text
      .slice(start, consumedUntil)
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/\/\/[^\r\n]*/g, " ");
    const scrubbed = statement.replace(
      /"([A-Za-z_][A-Za-z0-9_]*)"/g,
      moduleToken
    );
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

function moduleSourceContainers(projectRoot) {
  const root = path.resolve(projectRoot);
  const containers = [];
  const projectSource = path.join(root, "Source");
  if (fs.existsSync(projectSource)) containers.push(projectSource);
  const pluginsRoot = path.join(root, "Plugins");
  const pending = fs.existsSync(pluginsRoot) ? [pluginsRoot] : [];
  let complete = true;
  while (pending.length) {
    const current = pending.pop();
    let entries = [];
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch {
      complete = false;
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory() || SKIP_DIRS.has(entry.name)) continue;
      const child = path.join(current, entry.name);
      if (entry.name.toLowerCase() === "source") {
        containers.push(child);
      } else if (!new Set(["content", "config", "resources"]).has(entry.name.toLowerCase())) {
        pending.push(child);
      }
    }
  }
  return { containers: [...new Set(containers)], complete };
}

function projectModuleIndex(projectRoot) {
  const root = path.resolve(projectRoot);
  const sourceContainers = moduleSourceContainers(root);
  const modules = [];
  let complete = sourceContainers.complete;
  for (const container of sourceContainers.containers) {
    let entries = [];
    try { entries = fs.readdirSync(container, { withFileTypes: true }); } catch {
      complete = false;
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const moduleRoot = path.join(container, entry.name);
      const buildFile = path.join(moduleRoot, `${entry.name}.Build.cs`);
      if (!fs.existsSync(buildFile)) continue;
      const dependencyResolution = dependencyNamesFromBuildFile(buildFile);
      if (!dependencyResolution.complete) complete = false;
      modules.push({
        name: entry.name,
        relativeRoot: path.relative(root, moduleRoot).replace(/\\/g, "/"),
        dependencies: dependencyResolution.names,
      });
    }
  }
  return { modules, complete };
}

function resolveAutomationScopeRoots(projectRoot, scopeTargets) {
  const root = path.resolve(projectRoot);
  const suppliedTargets = Array.isArray(scopeTargets) ? scopeTargets : [];
  const scopeBound = suppliedTargets.length > 0;
  const targetRows = suppliedTargets.map((item) => {
    const rawValue = item && typeof item === "object"
      ? item.path || item.relativePath || ""
      : item;
    const label = String(rawValue || "").trim().replace(/\\/g, "/") || "<empty>";
    const target = normalizeProjectRelativePath(root, item);
    const moduleRoot = target ? moduleRootForTarget(root, target) : "";
    const modulePath = moduleRoot ? path.resolve(root, moduleRoot) : "";
    const mapped = Boolean(
      moduleRoot
      && modulePath
      && directoryExists(modulePath)
    );
    return { label, target, moduleRoot, mapped };
  });
  const bounded = targetRows.map((row) => row.target).filter(Boolean);
  const mappedRoots = scopeBound
    ? targetRows.filter((row) => row.mapped).map((row) => row.moduleRoot)
    : ["Source", "Plugins"];
  let dependencyGraphComplete = true;
  if (scopeBound && mappedRoots.length) {
    const moduleIndex = projectModuleIndex(root);
    dependencyGraphComplete = moduleIndex.complete;
    const selectedModuleNames = new Set(
      mappedRoots.map((item) => item.split("/").filter(Boolean).at(-1)).filter(Boolean)
    );
    let changed = true;
    while (changed) {
      changed = false;
      for (const module of moduleIndex.modules) {
        if (selectedModuleNames.has(module.name)) continue;
        if ([...module.dependencies].some((name) => selectedModuleNames.has(name))) {
          selectedModuleNames.add(module.name);
          mappedRoots.push(module.relativeRoot);
          changed = true;
        }
      }
    }
  }
  const scopeRoots = [...new Set(mappedRoots)]
    .sort((left, right) => left.localeCompare(right, "en", { sensitivity: "base" }));
  const roots = scopeRoots
    .map((relative) => path.resolve(root, relative))
    .filter((candidate) => {
      const relative = path.relative(root, candidate);
      return relative !== ".."
        && !relative.startsWith(`..${path.sep}`)
        && !path.isAbsolute(relative)
        && fs.existsSync(candidate);
    });
  return {
    scopeBound,
    scopeTargets: bounded,
    scopeRoots,
    unmappedScopeTargets: targetRows
      .filter((row) => !row.mapped)
      .map((row) => row.target || row.label),
    roots,
    dependencyGraphComplete,
  };
}

function cleanTestNames(value) {
  if (!Array.isArray(value)) return [];
  const namesByIdentity = new Map();
  for (const item of value) {
    const name = String(item || "").trim();
    const identity = name.toLowerCase();
    if (name && !namesByIdentity.has(identity)) namesByIdentity.set(identity, name);
  }
  return [...namesByIdentity.values()]
    .sort((left, right) => left.localeCompare(right, "en", { sensitivity: "base" }));
}

function automationFilterMatches(testName, testFilter) {
  const name = String(testName || "").trim().toLowerCase();
  const filter = String(testFilter || "").trim().toLowerCase();
  return Boolean(name && filter && (name === filter || name.startsWith(`${filter}.`)));
}

function validateAutomationFilter(testFilter, discoveredNames) {
  const rawFilter = String(testFilter || "");
  const filter = rawFilter.trim();
  if (!filter) {
    return {
      ok: false,
      errorCode: "AUTOMATION_FILTER_REQUIRED",
      error: "testFilter is required when Automation tests are declared",
      filter,
      expectedTests: [],
    };
  }
  if (/[;,\r\n\u2028\u2029]/.test(rawFilter) || /[\u0000-\u001f\u007f]/.test(rawFilter)) {
    return {
      ok: false,
      errorCode: "AUTOMATION_FILTER_UNSAFE",
      error: "testFilter cannot contain command separators, newlines, or control characters",
      filter,
      expectedTests: [],
    };
  }
  const names = cleanTestNames(discoveredNames);
  const expectedTests = names.filter((name) => automationFilterMatches(name, filter));
  if (Array.isArray(discoveredNames) && expectedTests.length === 0) {
    return {
      ok: false,
      errorCode: "AUTOMATION_FILTER_NO_MATCH",
      error: "testFilter does not match a discovered project Automation test",
      filter,
      expectedTests,
    };
  }
  return { ok: true, errorCode: "", error: "", filter, expectedTests };
}

function automationError(message, errorCode) {
  const error = new Error(message);
  error.errorCode = errorCode;
  return error;
}

function resolveEditorCmdPaths(engineRoot, hostPlatform = process.platform) {
  const binaries = path.join(engineRoot, "Engine", "Binaries");
  if (hostPlatform === "win32") {
    return [path.join(binaries, "Win64", "UnrealEditor-Cmd.exe")];
  }
  if (hostPlatform === "darwin") {
    return [
      path.join(binaries, "Mac", "UnrealEditor-Cmd"),
      path.join(binaries, "Mac", "UnrealEditor.app", "Contents", "MacOS", "UnrealEditor"),
    ];
  }
  return [
    path.join(binaries, "Linux", "UnrealEditor-Cmd"),
    path.join(binaries, "Linux", "UnrealEditor"),
  ];
}

function resolveEditorCmd(engineRoot, hostPlatform = process.platform) {
  const candidates = resolveEditorCmdPaths(engineRoot, hostPlatform);
  const selected = candidates.find((candidate) => fileExists(candidate));
  if (!selected) {
    const error = new Error(`UnrealEditor-Cmd was not found under engine root: ${engineRoot}`);
    error.errorCode = "UNREAL_EDITOR_CMD_NOT_FOUND";
    throw error;
  }
  assertEngineContainment(selected, engineRoot, hostPlatform);
  return selected;
}

function discoverAutomationTests(projectRoot, options = {}) {
  const maxFiles = Math.max(1, Math.min(5000, Number(options.maxFiles || 2000)));
  const scope = resolveAutomationScopeRoots(projectRoot, options.scopeTargets);
  const roots = scope.roots;
  const declarations = [];
  const declarationKeys = new Set();
  let inspectedFileCount = 0;
  let limitReachedWithUnvisitedEntries = false;
  let discoveryComplete = true;
  const pending = [...roots];
  while (pending.length && inspectedFileCount < maxFiles) {
    const current = pending.pop();
    let entries = [];
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch {
      discoveryComplete = false;
      continue;
    }
    entries.sort((left, right) => left.name.localeCompare(right.name, "en", { sensitivity: "base" }));
    for (let entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
      const entry = entries[entryIndex];
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) pending.push(path.join(current, entry.name));
        continue;
      }
      if (!entry.isFile() || !isAutomationSourceFile(entry.name)) continue;
      inspectedFileCount += 1;
      const absolutePath = path.join(current, entry.name);
      let text = "";
      try { text = fs.readFileSync(absolutePath, "utf8"); } catch {
        discoveryComplete = false;
        continue;
      }
      const codeOffsets = cppCodeOffsets(text);
      for (const declarationPattern of AUTOMATION_DECLARATION_PATTERNS) {
        const { pattern } = declarationPattern;
        pattern.lastIndex = 0;
        for (const match of text.matchAll(pattern)) {
          if (!macroStartsInCode(text, codeOffsets, Number(match.index || 0))) continue;
          const name = declarationPattern.registeredName(match);
          const nameIdentity = name.toLowerCase();
          if (!name || declarationKeys.has(nameIdentity)) continue;
          declarationKeys.add(nameIdentity);
          declarations.push({
            name,
            sourceFile: path.relative(path.resolve(projectRoot), absolutePath).replace(/\\/g, "/"),
            moduleRoot: moduleRootForTarget(projectRoot, absolutePath),
          });
        }
      }
      if (inspectedFileCount >= maxFiles) {
        limitReachedWithUnvisitedEntries = entries
          .slice(entryIndex + 1)
          .some((remaining) => (
            (remaining.isDirectory() && !SKIP_DIRS.has(remaining.name))
            || (remaining.isFile() && isAutomationSourceFile(remaining.name))
          ));
        break;
      }
    }
  }
  declarations.sort((left, right) => (
    left.name.localeCompare(right.name, "en", { sensitivity: "base" })
    || left.sourceFile.localeCompare(right.sourceFile, "en", { sensitivity: "base" })
  ));
  const names = declarations.map((item) => item.name);
  const rootsFound = [...new Set(names.map((name) => name.split(".")[0]).filter(Boolean))];
  // A scoped build must never execute a broad shared prefix that also owns
  // declarations outside the active slice. Exact declaration roots still run
  // Complex/Spec children while keeping every other module out of the plan.
  const suggestedFilters = scope.scopeBound
    ? cleanTestNames(names)
    : rootsFound.sort((left, right) => left.localeCompare(right, "en", { sensitivity: "base" }));
  return {
    names,
    tests: declarations,
    count: names.length,
    inspectedFileCount,
    truncated: Boolean(
      pending.length
      || limitReachedWithUnvisitedEntries
      || !discoveryComplete
      || scope.unmappedScopeTargets.length > 0
      || (scope.scopeBound && !scope.dependencyGraphComplete)
    ),
    suggestedFilter: suggestedFilters.length === 1 ? suggestedFilters[0] : "",
    suggestedFilters,
    scopeBound: scope.scopeBound,
    scopeTargets: scope.scopeTargets,
    scopeRoots: scope.scopeRoots,
    unmappedScopeTargets: scope.unmappedScopeTargets,
    dependencyGraphComplete: scope.dependencyGraphComplete,
    discoveryComplete,
  };
}

function automationArgs(projectPath, testFilter, options = {}) {
  const validation = validateAutomationFilter(
    testFilter,
    Array.isArray(options.discoveredNames) ? options.discoveredNames : undefined
  );
  if (!validation.ok) throw automationError(validation.error, validation.errorCode);
  const filter = validation.filter;
  return [
    path.resolve(projectPath),
    "-unattended",
    "-nop4",
    "-NullRHI",
    "-nosplash",
    "-stdout",
    "-FullStdOutLogOutput",
    `-ExecCmds=Automation RunTests ${filter};Quit`,
    "-TestExit=Automation Test Queue Empty",
  ];
}

function parseAutomationOutput(output, exitCode = 0, options = {}) {
  const text = String(output || "");
  // Current Unreal versions report each result through AutomationController as
  // `Test Completed. Result={Success}`. Keep the older command-line wording as
  // a fallback for engines that still emit `Automation Test Succeeded`.
  const completedTests = [];
  for (const match of text.matchAll(/Test Completed\.\s*Result=\{([^}]+)\}([^\r\n]*)/gi)) {
    const tail = String(match[2] || "");
    const pathMatch = tail.match(/\bPath=\{([^}]+)\}/i);
    const nameMatch = tail.match(/\bName=\{([^}]+)\}/i);
    completedTests.push({
      name: String(pathMatch?.[1] || nameMatch?.[1] || "").trim(),
      result: String(match[1] || "").trim().toLowerCase(),
    });
  }
  if (completedTests.length === 0) {
    for (const match of text.matchAll(/Automation Test (Succeeded|Failed)\s*\(([^)]+)\)/gi)) {
      completedTests.push({
        name: String(match[2] || "").trim(),
        result: String(match[1] || "").trim().toLowerCase(),
      });
    }
  }
  const succeededTests = completedTests
    .filter((item) => ["success", "succeeded"].includes(item.result))
    .map((item) => item.name)
    .filter(Boolean);
  const failedTests = completedTests
    .filter((item) => !["success", "succeeded"].includes(item.result))
    .map((item) => item.name)
    .filter(Boolean);
  const succeeded = completedTests.length > 0
    ? completedTests.filter((item) => ["success", "succeeded"].includes(item.result)).length
    : (text.match(/Automation Test Succeeded/gi) || []).length;
  const failed = completedTests.length > 0
    ? completedTests.length - succeeded
    : (text.match(/Automation Test Failed/gi) || []).length;
  const terminalExitMatch = text.match(/TEST COMPLETE\.\s*EXIT CODE:\s*(-?\d+)/i);
  const terminalExitCode = terminalExitMatch ? Number(terminalExitMatch[1]) : 0;
  const queueEmpty = /Automation Test Queue Empty/i.test(text) || Boolean(terminalExitMatch);
  const expectedTests = cleanTestNames(options.expectedTests);
  const succeededKeys = new Set(succeededTests.map((name) => name.toLowerCase()));
  const missingTests = expectedTests.filter((name) => {
    const expected = name.toLowerCase();
    return ![...succeededKeys].some((succeededName) => (
      succeededName === expected || succeededName.startsWith(`${expected}.`)
    ));
  });
  const common = {
    succeededCount: succeeded,
    failedCount: failed,
    queueEmpty,
    terminalComplete: queueEmpty,
    completedTests,
    succeededTests: cleanTestNames(succeededTests),
    failedTests: cleanTestNames(failedTests),
    expectedTests,
    missingTests,
  };
  if (failed > 0 || Number(exitCode) !== 0 || terminalExitCode !== 0) {
    return {
      ok: false,
      errorCode: failed > 0 ? "AUTOMATION_TEST_FAILED" : "AUTOMATION_PROCESS_FAILED",
      ...common,
    };
  }
  if (!queueEmpty) {
    return {
      ok: false,
      errorCode: "AUTOMATION_INCOMPLETE",
      ...common,
    };
  }
  if (succeeded === 0) {
    return {
      ok: false,
      errorCode: "NO_AUTOMATION_TESTS_EXECUTED",
      ...common,
    };
  }
  if (missingTests.length > 0) {
    return {
      ok: false,
      errorCode: "AUTOMATION_COVERAGE_INCOMPLETE",
      ...common,
    };
  }
  return {
    ok: true,
    errorCode: "",
    ...common,
  };
}

async function runAutomationTests(options = {}) {
  const {
    engineRoot,
    projectPath,
    testFilter,
    timeoutMs = 30 * 60 * 1000,
    logPath = "",
    hostPlatform = process.platform,
    scopeTargets,
  } = options;
  if (!engineRoot || !projectPath || !testFilter) {
    return { ok: false, errorCode: "INVALID_AUTOMATION_PLAN", error: "engineRoot, projectPath, and testFilter are required" };
  }
  const projectRoot = path.dirname(path.resolve(projectPath));
  const discovery = discoverAutomationTests(projectRoot, { scopeTargets });
  const discoveredNames = discovery.names;
  if (discovery.truncated) {
    return {
      ok: false,
      errorCode: discovery.unmappedScopeTargets.length
        ? "AUTOMATION_SCOPE_UNMAPPED"
        : "AUTOMATION_DISCOVERY_TRUNCATED",
      error: discovery.unmappedScopeTargets.length
        ? "One or more Automation scope targets could not be mapped to an existing source module"
        : "Automation declaration discovery did not complete",
      testFilter: String(testFilter || "").trim(),
      expectedTests: [],
      automationCoverage: discovery,
    };
  }
  const filterValidation = validateAutomationFilter(testFilter, discoveredNames);
  if (!filterValidation.ok) {
    return {
      ok: false,
      errorCode: filterValidation.errorCode,
      error: filterValidation.error,
      testFilter: filterValidation.filter,
      expectedTests: [],
      automationCoverage: discovery,
    };
  }
  const expectedTests = filterValidation.expectedTests;
  let executable;
  try {
    executable = resolveEditorCmd(path.resolve(engineRoot), hostPlatform);
  } catch (error) {
    return {
      ok: false,
      errorCode: error.errorCode || "UNREAL_EDITOR_CMD_NOT_FOUND",
      error: String(error.message || error),
      testFilter: filterValidation.filter,
      expectedTests,
      automationCoverage: discovery,
    };
  }
  const args = automationArgs(projectPath, filterValidation.filter, {
    discoveredNames,
  });
  return await new Promise((resolve) => {
    const child = spawn(executable, args, {
      cwd: path.dirname(path.resolve(projectPath)),
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      detached: hostPlatform !== "win32",
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    const finish = async (exitCode, timedOut = false, spawnError = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      let stdout = "";
      let stderr = "";
      let outputDecodeError = "";
      let logPersistenceError = "";
      try {
        stdout = decodeBuildOutput(stdoutChunks, { hostPlatform });
        stderr = decodeBuildOutput(stderrChunks, { hostPlatform });
      } catch (error) {
        outputDecodeError = String(error?.message || error);
      }
      try {
        if (logPath) {
          const fullOutput = `${stdout}\n${stderr}`.trim();
          await fsp.mkdir(path.dirname(logPath), { recursive: true });
          await fsp.writeFile(logPath, fullOutput, "utf8");
        }
      } catch (error) {
        logPersistenceError = String(error?.message || error);
      }
      const fullOutput = `${stdout}\n${stderr}`.trim();
      const parsed = timedOut
        ? { ok: false, errorCode: "AUTOMATION_TIMEOUT", succeededCount: 0, failedCount: 0, queueEmpty: false }
        : spawnError
          ? { ok: false, errorCode: "AUTOMATION_PROCESS_FAILED", succeededCount: 0, failedCount: 0, queueEmpty: false }
          : outputDecodeError
            ? { ok: false, errorCode: "AUTOMATION_OUTPUT_DECODE_FAILED", succeededCount: 0, failedCount: 0, queueEmpty: false }
            : logPersistenceError
              ? { ok: false, errorCode: "AUTOMATION_LOG_WRITE_FAILED", succeededCount: 0, failedCount: 0, queueEmpty: false }
              : parseAutomationOutput(fullOutput, exitCode, { expectedTests });
      resolve({
        ...parsed,
        exitCode: exitCode ?? 1,
        timedOut,
        error: (timedOut ? `Automation timed out after ${timeoutMs}ms` : "")
          || spawnError
          || outputDecodeError
          || logPersistenceError,
        outputDecodeError,
        logPersistenceError,
        stdout,
        stderr,
        executable,
        args,
        fullLogPath: logPath || null,
        testFilter: filterValidation.filter,
        expectedTests,
        automationCoverage: discovery,
      });
    };
    child.stdout.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    child.stderr.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));
    const timer = setTimeout(() => {
      Promise.resolve(killProcessTree(child.pid, hostPlatform))
        .catch(() => undefined)
        .then(() => finish(1, true));
    }, timeoutMs);
    child.on("close", (code) => { void finish(code ?? 1); });
    child.on("error", (error) => { void finish(1, false, String(error.message || error)); });
  });
}

module.exports = {
  MAX_AUTOMATION_FILTERS,
  resolveEditorCmdPaths,
  resolveEditorCmd,
  resolveAutomationScopeRoots,
  moduleRootForTarget,
  discoverAutomationTests,
  automationFilterMatches,
  validateAutomationFilter,
  automationArgs,
  parseAutomationOutput,
  runAutomationTests,
};
