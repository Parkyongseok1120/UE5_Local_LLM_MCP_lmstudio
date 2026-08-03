"use strict";

/**
 * Static verification for staged Unreal C++ campaign definitions (no UE required).
 * Scans the active project's Source/<module> for required files and code signatures.
 *
 * Requires STAGE_CAMPAIGN_PROJECT_ROOT or E2E_WORKSPACE (Unreal project root).
 * Optional STAGE_CAMPAIGN_SOURCE_MODULE (C++ module folder under Source/).
 * Optional STAGE_CAMPAIGN_STAGES_PATH (defaults to scripts/stage_campaign_stages.json).
 */

const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_STAGES_PATH = path.join(__dirname, "stage_campaign_stages.json");

function resolveProjectRoot() {
  const root = String(
    process.env.STAGE_CAMPAIGN_PROJECT_ROOT
    || process.env.E2E_WORKSPACE
    || ""
  ).trim();
  if (!root) {
    throw new Error(
      "Set STAGE_CAMPAIGN_PROJECT_ROOT or E2E_WORKSPACE to the Unreal project root "
      + "(directory that contains the .uproject)."
    );
  }
  return path.resolve(root);
}

function detectSourceModule(projectRoot) {
  const forced = String(process.env.STAGE_CAMPAIGN_SOURCE_MODULE || "").trim();
  if (forced) return forced;
  const sourceRoot = path.join(projectRoot, "Source");
  if (!fs.existsSync(sourceRoot)) {
    throw new Error(`Source/ missing under project root: ${projectRoot}`);
  }
  const modules = fs.readdirSync(sourceRoot, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith("."))
    .map((e) => e.name)
    .sort();
  if (modules.length === 1) return modules[0];
  if (modules.length === 0) {
    throw new Error(`No C++ modules found under ${sourceRoot}`);
  }
  throw new Error(
    `Multiple Source modules (${modules.join(", ")}). `
    + "Set STAGE_CAMPAIGN_SOURCE_MODULE to the target module name."
  );
}

function stagesPath() {
  return process.env.STAGE_CAMPAIGN_STAGES_PATH
    ? path.resolve(process.env.STAGE_CAMPAIGN_STAGES_PATH)
    : DEFAULT_STAGES_PATH;
}

function loadStages() {
  const file = stagesPath();
  const raw = fs.readFileSync(file, "utf8");
  const stages = JSON.parse(raw);
  if (!Array.isArray(stages)) {
    throw new Error(`${path.basename(file)} must be a JSON array`);
  }
  return stages;
}

function findStage(stages, stageId) {
  const id = Number(stageId);
  const stage = stages.find((s) => Number(s.id) === id);
  if (!stage) {
    throw new Error(`Unknown stage id: ${stageId}`);
  }
  return stage;
}

function listSourceFiles(sourceRoot) {
  const results = [];
  if (!fs.existsSync(sourceRoot)) return results;

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (/\.(h|cpp|cs|ini)$/i.test(entry.name)) {
        results.push(full);
      }
    }
  }
  walk(sourceRoot);
  return results;
}

function readAllSourceText(sourceRoot) {
  const files = listSourceFiles(sourceRoot);
  const chunks = [];
  for (const file of files) {
    try {
      chunks.push(fs.readFileSync(file, "utf8"));
    } catch {
      /* ignore unreadable */
    }
  }
  return chunks.join("\n");
}

function signatureMatches(text, pattern) {
  const raw = String(pattern || "");
  if (!raw) return true;
  try {
    const re = new RegExp(raw, "i");
    return re.test(text);
  } catch {
    return text.includes(raw);
  }
}

/** Strip C++ line/block comments so forbidden scans ignore prose. */
function stripCppComments(text) {
  return String(text || "")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ");
}

function scanForbidden(text, patterns) {
  const hits = [];
  const codeOnly = stripCppComments(text);
  for (const pattern of patterns || []) {
    if (signatureMatches(codeOnly, pattern)) {
      hits.push(pattern);
    }
  }
  return hits;
}

function verifyStage(stageId, options = {}) {
  const projectRoot = options.projectRoot || resolveProjectRoot();
  const sourceModule = options.sourceModule || detectSourceModule(projectRoot);
  const stages = options.stages || loadStages();
  const stage = findStage(stages, stageId);
  const sourceRoot = path.join(projectRoot, "Source", sourceModule);

  const missingFiles = [];
  for (const rel of stage.requiredFiles || []) {
    const normalized = rel.replace(/\\/g, "/");
    const full = path.join(projectRoot, normalized);
    if (!fs.existsSync(full)) {
      missingFiles.push(normalized);
    }
  }

  const sourceText = readAllSourceText(sourceRoot);
  const configPaths = (stage.requiredFiles || []).filter((p) => /Config\//i.test(p));
  let extraText = "";
  for (const rel of configPaths) {
    const full = path.join(projectRoot, rel.replace(/\\/g, "/"));
    if (fs.existsSync(full)) {
      extraText += fs.readFileSync(full, "utf8") + "\n";
    }
  }
  const scanText = sourceText + "\n" + extraText;

  const missingSignatures = [];
  for (const sig of stage.codeSignatures || []) {
    if (!signatureMatches(scanText, sig)) {
      missingSignatures.push(sig);
    }
  }

  const forbiddenHits = scanForbidden(scanText, stage.forbiddenPatterns);
  const notes = [];
  if (forbiddenHits.length) {
    notes.push(`forbiddenPatterns matched: ${forbiddenHits.join(", ")}`);
  }
  if (!fs.existsSync(sourceRoot)) {
    notes.push(`source root missing: ${sourceRoot}`);
  }

  const ok = missingFiles.length === 0
    && missingSignatures.length === 0
    && forbiddenHits.length === 0;

  return {
    ok,
    stageId: Number(stage.id),
    stageName: stage.name,
    projectRoot,
    sourceModule,
    missingFiles,
    missingSignatures,
    forbiddenHits,
    notes,
  };
}

function listExistingSourceFiles(projectRoot, sourceModule) {
  const root = projectRoot || resolveProjectRoot();
  const mod = sourceModule || detectSourceModule(root);
  const sourceRoot = path.join(root, "Source", mod);
  return listSourceFiles(sourceRoot).map((full) => {
    const rel = path.relative(root, full).replace(/\\/g, "/");
    return rel;
  });
}

function parseCliStage(argv) {
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--stage" && argv[i + 1]) {
      return Number(argv[i + 1]);
    }
  }
  return null;
}

function main() {
  const stageId = parseCliStage(process.argv.slice(2));
  if (!Number.isFinite(stageId)) {
    console.error("Usage: node scripts/stage_campaign_verify.js --stage <id>");
    console.error("Env: STAGE_CAMPAIGN_PROJECT_ROOT or E2E_WORKSPACE (required)");
    console.error("     STAGE_CAMPAIGN_SOURCE_MODULE (optional if only one Source module)");
    process.exitCode = 1;
    return;
  }
  const result = verifyStage(stageId);
  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 2;
}

if (require.main === module) {
  main();
}

module.exports = {
  loadStages,
  verifyStage,
  listExistingSourceFiles,
  resolveProjectRoot,
  detectSourceModule,
  stripCppComments,
  scanForbidden,
  signatureMatches,
  STAGES_PATH: DEFAULT_STAGES_PATH,
};
