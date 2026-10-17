"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function resolvePythonExe(env = process.env) {
  const explicit = String(env.PYTHON_EXE || env.PYTHON || "").trim();
  if (explicit) return explicit;

  const bundledRoot = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "python",
  );
  const bundledCandidates = process.platform === "win32"
    ? [path.join(bundledRoot, "python.exe")]
    : [
      path.join(bundledRoot, "bin", "python3.12"),
      path.join(bundledRoot, "bin", "python3"),
      path.join(bundledRoot, "bin", "python"),
    ];
  for (const candidate of bundledCandidates) {
    if (fs.existsSync(candidate)) return candidate;
  }

  const localRoot = path.join(env.LOCALAPPDATA || "", "Programs", "Python");
  if (fs.existsSync(localRoot)) {
    const versions = fs.readdirSync(localRoot)
      .filter((name) => name.toLowerCase().startsWith("python"))
      .sort()
      .reverse();
    for (const version of versions) {
      const candidate = path.join(localRoot, version, "python.exe");
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return process.platform === "win32" ? "python" : "python3";
}

module.exports = { resolvePythonExe };
