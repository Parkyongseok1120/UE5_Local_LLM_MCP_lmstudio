"use strict";

const fs = require("fs");
const path = require("path");
const { assertEngineContainment } = require("./build-executor");

const MAX_AUTOMATION_FILTERS = 256;

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
  const selected = candidates.find((candidate) => {
    try { return fs.statSync(candidate).isFile(); } catch { return false; }
  });
  if (!selected) {
    throw automationError(
      `UnrealEditor-Cmd was not found under engine root: ${engineRoot}`,
      "UNREAL_EDITOR_CMD_NOT_FOUND"
    );
  }
  assertEngineContainment(selected, engineRoot, hostPlatform);
  return selected;
}

function automationArgs(projectPath, testFilter, options = {}) {
  const validation = validateAutomationFilter(
    testFilter,
    Array.isArray(options.discoveredNames) ? options.discoveredNames : undefined
  );
  if (!validation.ok) throw automationError(validation.error, validation.errorCode);
  return [
    path.resolve(projectPath),
    "-unattended",
    "-nop4",
    "-NullRHI",
    "-nosplash",
    "-stdout",
    "-FullStdOutLogOutput",
    `-ExecCmds=Automation RunTests ${validation.filter};Quit`,
    "-TestExit=Automation Test Queue Empty",
  ];
}

module.exports = {
  MAX_AUTOMATION_FILTERS,
  automationArgs,
  automationFilterMatches,
  cleanTestNames,
  resolveEditorCmd,
  resolveEditorCmdPaths,
  validateAutomationFilter,
};
