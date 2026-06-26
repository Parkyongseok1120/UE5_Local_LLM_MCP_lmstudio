"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { getActiveProject, resolveProjectSelection } = require("./unreal-detect.js");

async function resolveProjectRoot(workspaceRoot, configPath, hint) {
  const active = getActiveProject(configPath);
  const selection = await resolveProjectSelection(workspaceRoot, configPath, {
    hint: hint || active || undefined
  });
  if (!selection.selected) {
    return { ok: false, error: selection.error || "No project resolved.", selection };
  }
  return {
    ok: true,
    projectPath: selection.selected.projectPath,
    projectDir: selection.selected.projectDir,
    projectName: selection.selected.projectName
  };
}

async function scanSymbolImpact(workspaceRoot, configPath, options = {}) {
  const resolved = await resolveProjectRoot(workspaceRoot, configPath, options.hint);
  if (!resolved.ok) {
    return resolved;
  }

  const symbol = String(options.symbol || "").trim();
  if (symbol.length < 2) {
    return { ok: false, error: "symbol must be at least 2 characters." };
  }

  const root = path.resolve(options.projectDir || resolved.projectDir);
  const skip = new Set([".git", ".vs", "Binaries", "Intermediate", "Saved", "DerivedDataCache"]);
  const suffixes = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cs"]);
  const maxFiles = Number(options.maxFiles || 40);
  const matches = [];
  const pattern = new RegExp(symbol.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

  async function walk(dir) {
    if (matches.length >= maxFiles) {
      return;
    }
    let entries;
    try {
      entries = await fsp.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (matches.length >= maxFiles) {
        return;
      }
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (skip.has(entry.name)) {
          continue;
        }
        await walk(full);
        continue;
      }
      const ext = path.extname(entry.name).toLowerCase();
      if (!suffixes.has(ext) && !entry.name.endsWith(".Build.cs")) {
        continue;
      }
      let text;
      try {
        text = await fsp.readFile(full, "utf8");
      } catch {
        continue;
      }
      if (!pattern.test(text)) {
        continue;
      }
      const lineNumbers = [];
      text.split(/\r?\n/).forEach((line, index) => {
        if (pattern.test(line)) {
          lineNumbers.push(index + 1);
        }
      });
      matches.push({
        path: full,
        relativePath: path.relative(root, full).split(path.sep).join("/"),
        lineNumbers: lineNumbers.slice(0, 8),
        hitCount: lineNumbers.length
      });
    }
  }

  await walk(root);
  return {
    ok: true,
    symbol,
    projectRoot: root,
    projectName: resolved.projectName,
    matchCount: matches.length,
    matches,
    truncated: matches.length >= maxFiles
  };
}

function validateRefactorPlan(stage, planText) {
  const normalized = String(stage || "R0").trim().toUpperCase();
  const text = String(planText || "").trim();
  const lowered = text.toLowerCase();
  const issues = [];
  const warnings = [];
  const passed = [];

  if (!text) {
    return { ok: false, stage: normalized, issues: ["Plan text is empty."], warnings, passed };
  }

  const codeMarkers = ["#include", "uclass(", "generated_body()", "void ", "bool "];
  if (normalized === "R0") {
    if (codeMarkers.some((marker) => lowered.includes(marker))) {
      issues.push("R0 must not include code snippets or UCLASS/GENERATED_BODY blocks.");
    } else {
      passed.push("R0 has no obvious code blocks.");
    }
    if (/(ssot|owner|소유|단일 원본)/i.test(text)) {
      passed.push("SSOT/ownership language present.");
    } else {
      issues.push("R0 should name state owners (SSOT table or ownership section).");
    }
    if (/(file|path|파일|\.h|\.cpp|build\.cs)/i.test(text)) {
      passed.push("Impact file list or path references present.");
    } else {
      warnings.push("R0 should list impacted files or paths.");
    }
  }

  if (["R2", "R3", "R4"].includes(normalized)) {
    const fileHits = (lowered.match(/\.(?:h|cpp)\b/g) || []).length;
    if (fileHits > 5) {
      warnings.push(`${normalized} mentions many files (${fileHits}). Prefer ≤3 files per turn.`);
    }
    if (!/(build|ubt|compile)/i.test(text)) {
      warnings.push(`${normalized} should state how UBT/build verification will run.`);
    }
  }

  if (lowered.includes("lyra") && !lowered.includes("project-specific") && !lowered.includes("example")) {
    warnings.push("Lyra names should be labeled project-specific, not universal rules.");
  }

  return {
    ok: issues.length === 0,
    stage: normalized,
    issues,
    warnings,
    passed
  };
}

module.exports = {
  scanSymbolImpact,
  validateRefactorPlan
};
