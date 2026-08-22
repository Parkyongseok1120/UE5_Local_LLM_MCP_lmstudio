"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { resolvePythonExe } = require("./python-executable");

const SOURCE_GUARD_EXTENSIONS = new Set([".h", ".hpp", ".inl", ".cpp", ".c", ".cc", ".cxx"]);

function candidateGuardScripts() {
  const candidates = [];
  const envRoot = String(process.env.UNREAL58_ROOT || "").trim();
  if (envRoot) {
    candidates.push(path.join(path.resolve(envRoot), "scripts", "mutation_semantic_guard.py"));
  }
  // Repo / portable package layout: lmstudio-unreal-agent-mcp/src -> ../../scripts
  candidates.push(path.resolve(__dirname, "..", "..", "scripts", "mutation_semantic_guard.py"));
  candidates.push(
    path.join(os.homedir(), ".lmstudio", "Unreal58-RAG", "scripts", "mutation_semantic_guard.py")
  );
  return candidates;
}

function resolveGuardScript() {
  const candidates = candidateGuardScripts();
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return candidates[0];
}

function validateMutationSemanticText(text) {
  const script = resolveGuardScript();
  if (!fs.existsSync(script)) {
    return {
      ok: false,
      infrastructureError: true,
      reason: "mutation_semantic_guard.py missing",
      hits: [],
    };
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

function compactAdvisoryText(value, maxChars = 240) {
  const text = String(value || "").replace(/\s+/gu, " ").trim();
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 3))}...`;
}

function mutationSemanticAdvisory(targetPath, text, evaluator = validateMutationSemanticText) {
  if (!SOURCE_GUARD_EXTENSIONS.has(path.extname(String(targetPath || "")).toLowerCase())) {
    return null;
  }
  let result;
  try {
    result = evaluator(text);
  } catch (error) {
    result = {
      ok: false,
      infrastructureError: true,
      reason: String(error?.message || error),
      hits: [],
    };
  }
  if (result?.ok === true || result?.skipped === true) return null;
  const hits = Array.isArray(result?.hits) ? result.hits : [];
  if (result?.infrastructureError === true || hits.length === 0) {
    return {
      code: "SEMANTIC_GUARD_UNAVAILABLE",
      severity: "warning",
      blocking: false,
      source: "unreal_api_denylist",
      message: compactAdvisoryText(
        `Semantic advisory unavailable: ${result?.reason || "guard returned no usable result"}`,
      ),
    };
  }
  return {
    code: "UNREAL_API_SEMANTIC_FINDINGS",
    severity: "warning",
    blocking: false,
    source: "unreal_api_denylist",
    findingCount: hits.length,
    message: `${hits.length} non-blocking Unreal API semantic finding${hits.length === 1 ? "" : "s"}; review before relying on the edit.`,
    findings: hits.slice(0, 3).map((hit) => ({
      term: compactAdvisoryText(hit?.term || hit?.code || "semantic_finding", 80),
      message: compactAdvisoryText(hit?.message || hit?.reason || "Review this API usage."),
    })),
  };
}

function probeMutationSemanticGuard() {
  const script = resolveGuardScript();
  if (!fs.existsSync(script)) {
    return {
      ok: false,
      present: false,
      importable: false,
      pythonProbe: false,
      reason: "mutation_semantic_guard.py missing",
    };
  }
  const denylist = path.join(path.dirname(script), "unreal_api_denylist.py");
  if (!fs.existsSync(denylist)) {
    return {
      ok: false,
      present: true,
      importable: false,
      pythonProbe: false,
      reason: "unreal_api_denylist.py missing",
    };
  }
  const result = validateMutationSemanticText("");
  if (!result.ok && result.infrastructureError) {
    return {
      ok: false,
      present: true,
      importable: false,
      pythonProbe: false,
      reason: result.reason,
    };
  }
  return {
    ok: true,
    present: true,
    importable: true,
    pythonProbe: true,
    reason: null,
  };
}

module.exports = {
  candidateGuardScripts,
  mutationSemanticAdvisory,
  resolveGuardScript,
  validateMutationSemanticText,
  probeMutationSemanticGuard,
};
