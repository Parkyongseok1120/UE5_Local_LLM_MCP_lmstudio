"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { resolvePythonExe } = require("./validate-write");

function resolveGuardScript() {
  const root = path.resolve(
    process.env.UNREAL58_ROOT || path.join(os.homedir(), ".lmstudio", "Unreal58-RAG")
  );
  return path.join(root, "scripts", "mutation_semantic_guard.py");
}

function validateMutationSemanticText(text) {
  const script = resolveGuardScript();
  if (!fs.existsSync(script)) {
    return { ok: true, skipped: true, reason: "mutation_semantic_guard.py missing" };
  }
  const result = cp.spawnSync(resolvePythonExe(), [script], {
    cwd: path.dirname(path.dirname(script)),
    encoding: "utf8",
    input: String(text || ""),
    maxBuffer: 2 * 1024 * 1024,
    timeout: 15000,
  });
  if (result.error || result.status !== 0) {
    return {
      ok: false,
      infrastructureError: true,
      reason: String(result.error?.message || result.stderr || `guard exited ${result.status}`),
      hits: [],
    };
  }
  try {
    const payload = JSON.parse(String(result.stdout || "{}"));
    return {
      ok: payload.ok === true,
      hits: Array.isArray(payload.hits) ? payload.hits : [],
      skipped: false,
    };
  } catch (error) {
    return {
      ok: false,
      infrastructureError: true,
      reason: `invalid mutation semantic guard response: ${error.message}`,
      hits: [],
    };
  }
}

module.exports = {
  resolveGuardScript,
  validateMutationSemanticText,
};
