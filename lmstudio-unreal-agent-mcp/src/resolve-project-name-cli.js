#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const {
  normalizeProjectName,
  resolveExactProjectNameSelection,
} = require(path.join(__dirname, "unreal-detect"));

const MAX_STDIN_BYTES = 64 * 1024;
const DEFAULT_CONFIG_PATH = path.resolve(__dirname, "..", "config", "agent-mcp.json");

function inputError(errorCode, message) {
  const error = new Error(message);
  error.errorCode = errorCode;
  return error;
}

async function readStdinJson() {
  process.stdin.setEncoding("utf8");
  let raw = "";
  let size = 0;
  for await (const chunk of process.stdin) {
    size += Buffer.byteLength(chunk, "utf8");
    if (size > MAX_STDIN_BYTES) {
      throw inputError("STDIN_TOO_LARGE", `stdin JSON exceeds ${MAX_STDIN_BYTES} bytes`);
    }
    raw += chunk;
  }
  if (!raw.trim()) throw inputError("INVALID_INPUT", "stdin must contain one JSON object");
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw inputError("INVALID_JSON", "stdin is not valid JSON");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw inputError("INVALID_INPUT", "stdin JSON must be an object");
  }
  return payload;
}

function validateInput(payload) {
  if (typeof payload.workspaceRoot !== "string") {
    throw inputError("INVALID_WORKSPACE_ROOT", "workspaceRoot must be an absolute directory path");
  }
  if (typeof payload.target !== "string") {
    throw inputError("INVALID_TARGET", "target must be a project name string");
  }
  if (payload.configPath !== undefined && typeof payload.configPath !== "string") {
    throw inputError("INVALID_CONFIG_PATH", "configPath must be an absolute file path when provided");
  }

  const workspaceRoot = payload.workspaceRoot.trim();
  const target = payload.target.trim();
  const suppliedConfigPath = payload.configPath === undefined
    ? ""
    : payload.configPath.trim();
  const configPath = suppliedConfigPath || DEFAULT_CONFIG_PATH;

  if (!workspaceRoot || !path.isAbsolute(workspaceRoot)) {
    throw inputError("INVALID_WORKSPACE_ROOT", "workspaceRoot must be an absolute directory path");
  }
  let workspaceStat;
  try {
    workspaceStat = fs.statSync(workspaceRoot);
  } catch {
    throw inputError("INVALID_WORKSPACE_ROOT", "workspaceRoot does not exist or is not readable");
  }
  if (!workspaceStat.isDirectory()) {
    throw inputError("INVALID_WORKSPACE_ROOT", "workspaceRoot must be a directory");
  }
  if (!path.isAbsolute(configPath)) {
    throw inputError("INVALID_CONFIG_PATH", "configPath must be absolute when provided");
  }
  if (suppliedConfigPath) {
    let configStat;
    try {
      configStat = fs.statSync(configPath);
    } catch {
      throw inputError("INVALID_CONFIG_PATH", "configPath does not exist or is not readable");
    }
    if (!configStat.isFile()) {
      throw inputError("INVALID_CONFIG_PATH", "configPath must be a file");
    }
  }
  if (!target || !normalizeProjectName(target)) {
    throw inputError("INVALID_TARGET", "target must contain a project name");
  }
  return {
    workspaceRoot: path.resolve(workspaceRoot),
    configPath: path.resolve(configPath),
    target,
  };
}

function candidatePayload(candidate) {
  if (!candidate) return null;
  return {
    projectName: candidate.projectName,
    projectFile: candidate.projectFile,
    projectPath: candidate.projectPath,
    projectDir: candidate.projectDir,
    preferredTarget: candidate.preferredTarget,
    engineAssociation: candidate.engineAssociation,
  };
}

function resultPayload(input, result) {
  return {
    ok: Boolean(result.selected),
    errorCode: result.errorCode || null,
    error: result.error || null,
    target: input.target,
    normalizedName: result.normalizedName,
    selectionReason: result.selectionReason,
    selected: candidatePayload(result.selected),
    suggestions: Array.isArray(result.suggestions) ? result.suggestions : [],
    candidateCount: Array.isArray(result.rawProjects) ? result.rawProjects.length : 0,
    searchRoots: Array.isArray(result.roots) ? result.roots : [],
  };
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function run() {
  try {
    const input = validateInput(await readStdinJson());
    const result = await resolveExactProjectNameSelection(
      input.workspaceRoot,
      input.configPath,
      { name: input.target },
    );
    const payload = resultPayload(input, result);
    writeJson(payload);
    if (!payload.ok) {
      process.stderr.write(`${payload.errorCode}: ${payload.error}\n`);
      return 1;
    }
    return 0;
  } catch (error) {
    const errorCode = String(error?.errorCode || "PROJECT_NAME_RESOLUTION_FAILED");
    const message = String(error?.message || "project name resolution failed");
    writeJson({ ok: false, errorCode, error: message });
    process.stderr.write(`${errorCode}: ${message}\n`);
    return 1;
  }
}

if (require.main === module) {
  run().then((exitCode) => {
    process.exitCode = exitCode;
  });
}

module.exports = {
  DEFAULT_CONFIG_PATH,
  MAX_STDIN_BYTES,
  candidatePayload,
  readStdinJson,
  resultPayload,
  run,
  validateInput,
};
