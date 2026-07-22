"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { promisify } = require("util");

const execFile = promisify(cp.execFile);

const IGNORE_DIRS = new Set([
  ".git",
  ".vs",
  ".idea",
  "Binaries",
  "DerivedDataCache",
  "Intermediate",
  "Saved",
  "node_modules",
  ".gradle",
  ".cache",
  ".pytest_cache",
  ".pytest_tmp",
  "wrapper_runs",
  "data",
  "local_holdout_fixtures",
  "text_snapshot",
  "editor_export_jobs"
]);

const DEFAULT_EPIC_ROOT = path.join("C:", "Program Files", "Epic Games");

function sharedConfigPath() {
  if (process.env.SHARED_UNREAL_CONFIG) {
    return path.resolve(process.env.SHARED_UNREAL_CONFIG);
  }
  return path.join(os.homedir(), ".lmstudio", "config", "unreal-workspace.json");
}

function loadSharedConfig() {
  return loadConfig(sharedConfigPath());
}

function saveSharedConfig(config) {
  const target = sharedConfigPath();
  const merged = { ...loadSharedConfig(), ...config, updatedAt: new Date().toISOString() };
  saveConfig(target, merged);
  return merged;
}

function loadMergedConfig(configPath) {
  const local = loadConfig(configPath);
  const shared = loadSharedConfig();
  return {
    ...local,
    ...shared,
    projectSearchRoots: shared.projectSearchRoots?.length
      ? shared.projectSearchRoots
      : local.projectSearchRoots,
    activeProject: shared.activeProject ?? local.activeProject ?? null
  };
}

function loadConfig(configPath) {
  try {
    const raw = fs.readFileSync(configPath, "utf8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function saveConfig(configPath, config) {
  const directory = path.dirname(configPath);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}

function getActiveProject(configPath) {
  const config = loadMergedConfig(configPath);
  return config.activeProject || null;
}

function ragRootPath() {
  if (process.env.UNREAL58_ROOT) {
    return path.resolve(process.env.UNREAL58_ROOT);
  }
  return path.join(os.homedir(), ".lmstudio", "Unreal58-RAG");
}

async function invokeProjectController(argv) {
  const script = path.join(ragRootPath(), "scripts", "project_controller.py");
  const python = process.env.PYTHON_EXE || (process.platform === "win32" ? "python" : "python3");
  try {
    const { stdout } = await execFile(python, [script, ...argv], {
      timeout: Number(process.env.PROJECT_CONTROLLER_TIMEOUT_MS || 600000),
      maxBuffer: 8 * 1024 * 1024
    });
    return JSON.parse(stdout);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

async function setActiveProject(workspaceRoot, configPath, options = {}) {
  if (options.clear === true || options.projectPath === null) {
    const controller = await invokeProjectController(["--clear"]);
    if (controller.ok) {
      saveConfig(configPath, { ...loadConfig(configPath), activeProject: null });
    }
    return controller.ok ? controller : {
      ok: true,
      activeProject: null,
      message: "Active project cleared. Auto-detection will use hint or single-project heuristics again."
    };
  }

  const projectPath = String(options.projectPath || "").trim();
  if (projectPath) {
    const resolved = path.resolve(projectPath);
    if (!(await exists(resolved))) {
      return { ok: false, error: `Project not found: ${resolved}` };
    }
    if (!resolved.toLowerCase().endsWith(".uproject")) {
      return { ok: false, error: "activeProject must be a .uproject file path." };
    }
    const argv = ["--switch", resolved];
    if (options.prepare === true) {
      argv.push("--prepare");
    }
    if (options.force === true) {
      argv.push("--force-prepare");
    }
    const controller = await invokeProjectController(argv);
    if (!controller.ok) {
      return controller;
    }
    saveConfig(configPath, { ...loadConfig(configPath), activeProject: resolved });
    const info = await readUProject(resolved);
    const targets = await findTargetNames(info.projectDir, info.projectName);
    return {
      ...controller,
      activeProject: resolved,
      projectName: info.projectName,
      preferredTarget: targets.preferredTarget,
      readiness: controller.readiness || null
    };
  }

  const hint = String(options.hint || "").trim();
  if (!hint) {
    return { ok: false, error: "Provide projectPath, hint, or clear=true." };
  }

  const selection = await resolveProjectSelection(workspaceRoot, configPath, { hint });
  if (!selection.selected) {
    return {
      ok: false,
      error: selection.error || `No project matched hint: ${hint}`,
      suggestions: selection.suggestions || null
    };
  }

  const argv = ["--switch", selection.selected.projectPath];
  if (options.prepare === true) {
    argv.push("--prepare");
  }
  const controller = await invokeProjectController(argv);
  if (!controller.ok) {
    return controller;
  }
  saveConfig(configPath, { ...loadConfig(configPath), activeProject: selection.selected.projectPath });
  return {
    ...controller,
    ok: true,
    activeProject: selection.selected.projectPath,
    projectName: selection.selected.projectName,
    preferredTarget: selection.selected.preferredTarget,
    selectionReason: selection.selectionReason,
    message: `Active project set to ${selection.selected.projectFile}`,
    readiness: controller.readiness || null
  };
}

async function listUnrealProjects(workspaceRoot, configPath, options = {}) {
  const discovery = await discoverProjects(workspaceRoot, configPath, options);
  const activeProject = getActiveProject(configPath);
  return {
    activeProject,
    searchRoots: discovery.roots,
    projects: discovery.projects.map((project) => ({
      projectFile: project.projectFile,
      projectPath: project.projectPath,
      projectName: project.projectName,
      preferredTarget: project.preferredTarget,
      allTargets: project.allTargets,
      engineAssociation: project.engineAssociation,
      modifiedAt: project.modifiedAt,
      isActive: activeProject
        ? project.projectPath.toLowerCase() === String(activeProject).toLowerCase()
        : false
    }))
  };
}

function uniquePaths(paths) {
  const seen = new Set();
  const out = [];
  for (const p of paths) {
    if (!p || typeof p !== "string") continue;
    const resolved = path.resolve(p);
    const key = resolved.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(resolved);
  }
  return out;
}

function resolveSearchRoots(workspaceRoot, configPath) {
  const config = loadMergedConfig(configPath);
  const fromEnv = String(process.env.PROJECT_SEARCH_ROOTS || "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  const fromConfig = Array.isArray(config.projectSearchRoots) ? config.projectSearchRoots : [];
  const explicitRoots = [...fromEnv, ...fromConfig];
  const fallbackRoots = explicitRoots.length === 0
    ? [
      path.join(os.homedir(), "Documents", "Git"),
      path.join(os.homedir(), "Documents", "Unreal Projects")
    ]
    : [];
  const roots = uniquePaths([
    workspaceRoot,
    process.env.ACTIVE_PROJECT ? path.dirname(path.resolve(process.env.ACTIVE_PROJECT)) : "",
    ...explicitRoots,
    ...fallbackRoots
  ]);
  return { config, roots };
}

async function exists(p) {
  try {
    await fsp.access(p);
    return true;
  } catch {
    return false;
  }
}

async function statSafe(p) {
  try {
    return await fsp.stat(p);
  } catch {
    return null;
  }
}

function projectNameFromPath(uprojectPath) {
  return path.basename(uprojectPath, ".uproject");
}

function engineFolderFromAssociation(value) {
  if (!value) return null;
  const text = String(value).trim();
  if (/^\d+\.\d+$/.test(text)) {
    return `UE_${text}`;
  }
  if (/^UE_/i.test(text)) {
    return text;
  }
  return null;
}

function engineBuildToolCandidates(engineRoot, hostPlatform = process.platform) {
  const batchRoot = path.join(engineRoot, "Engine", "Build", "BatchFiles");
  const ubtRoot = path.join(engineRoot, "Engine", "Binaries", "DotNET", "UnrealBuildTool");
  if (hostPlatform === "win32") {
    return [
      { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
      { path: path.join(batchRoot, "Build.bat"), kind: "build_bat" },
      { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    ];
  }
  const hostFolder = hostPlatform === "darwin" ? "Mac" : "Linux";
  return [
    { path: path.join(batchRoot, hostFolder, "Build.sh"), kind: "build_sh" },
    { path: path.join(batchRoot, "Build.sh"), kind: "build_sh" },
    { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
  ];
}

async function resolveEngineBuildTool(engineRoot, hostPlatform = process.platform) {
  for (const candidate of engineBuildToolCandidates(engineRoot, hostPlatform)) {
    if (await exists(candidate.path)) return candidate;
  }
  return null;
}

function defaultEngineLocations(
  hostPlatform = process.platform,
  env = process.env,
  homeDirectory = os.homedir()
) {
  if (hostPlatform === "win32") {
    return uniquePaths([
      env.ProgramFiles ? path.join(env.ProgramFiles, "Epic Games") : "",
      env["ProgramFiles(x86)"] ? path.join(env["ProgramFiles(x86)"], "Epic Games") : "",
      DEFAULT_EPIC_ROOT,
    ]);
  }
  if (hostPlatform === "darwin") {
    return ["/Users/Shared/Epic Games", "/Applications/Epic Games"];
  }
  return uniquePaths([
    path.join(homeDirectory, "UnrealEngine"),
    path.join(homeDirectory, "Epic Games"),
    "/opt/UnrealEngine",
    "/opt/Epic Games",
  ]);
}

async function findEngineInstalls(options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const explicitEngineRoot = options.explicitEngineRoot ?? env.UNREAL_ENGINE_ROOT ?? "";
  const locations = options.roots || defaultEngineLocations(hostPlatform, env, options.homeDirectory);
  const installs = [];
  const seen = new Set();

  const addInstall = async (candidateRoot, source) => {
    if (!candidateRoot) return;
    const root = path.resolve(candidateRoot);
    const key = hostPlatform === "win32" ? root.toLowerCase() : root;
    if (seen.has(key)) return;
    const buildTool = await resolveEngineBuildTool(root, hostPlatform);
    if (!buildTool) return;
    seen.add(key);
    installs.push({
      engineRoot: root,
      folderName: path.basename(root),
      buildTool: buildTool.path,
      buildToolKind: buildTool.kind,
      buildBat: buildTool.path,
      source,
    });
  };

  await addInstall(explicitEngineRoot, "environment");
  for (const location of locations) {
    if (!(await exists(location))) continue;
    await addInstall(location, "common-location");
    const entries = await fsp.readdir(location, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || !/^UE_/i.test(entry.name)) continue;
      await addInstall(path.join(location, entry.name), "discovered");
    }
  }

  installs.sort((a, b) => a.folderName.localeCompare(b.folderName));
  return installs;
}

async function resolveEngineRoot(engineAssociation, config, explicitEngineRoot, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const requestedFolder = engineFolderFromAssociation(engineAssociation);

  if (explicitEngineRoot) {
    const resolved = path.resolve(explicitEngineRoot);
    const buildTool = await resolveEngineBuildTool(resolved, hostPlatform);
    if (buildTool) {
      return {
        engineRoot: resolved,
        buildTool: buildTool.path,
        buildToolKind: buildTool.kind,
        buildBat: buildTool.path,
        source: "argument",
        requestedEngineAssociation: engineAssociation,
        warning: null
      };
    }
  }

  const environmentEngineRoot = String(env.UNREAL_ENGINE_ROOT || "").trim();
  if (environmentEngineRoot) {
    const resolved = path.resolve(environmentEngineRoot);
    const buildTool = await resolveEngineBuildTool(resolved, hostPlatform);
    if (buildTool) {
      return {
        engineRoot: resolved,
        buildTool: buildTool.path,
        buildToolKind: buildTool.kind,
        buildBat: buildTool.path,
        source: "environment",
        requestedEngineAssociation: engineAssociation,
        warning: requestedFolder && path.basename(resolved).toLowerCase() !== requestedFolder.toLowerCase()
          ? `Using UNREAL_ENGINE_ROOT for EngineAssociation ${engineAssociation}.`
          : null,
      };
    }
  }

  const installs = await findEngineInstalls({ ...options, hostPlatform, env });

  if (requestedFolder) {
    const exact = installs.find((item) => item.folderName.toLowerCase() === requestedFolder.toLowerCase());
    if (exact) {
      return {
        engineRoot: exact.engineRoot,
        buildTool: exact.buildTool,
        buildToolKind: exact.buildToolKind,
        buildBat: exact.buildBat,
        source: "EngineAssociation",
        requestedEngineAssociation: engineAssociation,
        warning: null
      };
    }
  }

  if (config.defaultEngineRoot) {
    const resolved = path.resolve(config.defaultEngineRoot);
    const buildTool = await resolveEngineBuildTool(resolved, hostPlatform);
    if (buildTool) {
      return {
        engineRoot: resolved,
        buildTool: buildTool.path,
        buildToolKind: buildTool.kind,
        buildBat: buildTool.path,
        source: "config.defaultEngineRoot",
        requestedEngineAssociation: engineAssociation,
        warning: requestedFolder
          ? `EngineAssociation ${engineAssociation} not installed; using config.defaultEngineRoot.`
          : null
      };
    }
  }

  const fallback = installs[installs.length - 1];
  if (fallback) {
    return {
      engineRoot: fallback.engineRoot,
      buildTool: fallback.buildTool,
      buildToolKind: fallback.buildToolKind,
      buildBat: fallback.buildBat,
      source: fallback.source === "environment" ? "environment" : "latest-installed",
      requestedEngineAssociation: engineAssociation,
      warning: requestedFolder
        ? `EngineAssociation ${engineAssociation} not installed; using ${fallback.folderName}.`
        : null
    };
  }

  return null;
}

async function readUProject(uprojectPath) {
  const raw = await fsp.readFile(uprojectPath, "utf8");
  const data = JSON.parse(raw);
  const projectName = projectNameFromPath(uprojectPath);
  const modules = Array.isArray(data.Modules) ? data.Modules.map((m) => m.Name).filter(Boolean) : [];
  return {
    projectPath: path.resolve(uprojectPath),
    projectDir: path.dirname(path.resolve(uprojectPath)),
    projectFile: path.basename(uprojectPath),
    projectName,
    engineAssociation: data.EngineAssociation || null,
    modules
  };
}

async function findTargetNames(projectDir, projectName) {
  const sourceDir = path.join(projectDir, "Source");
  const targets = new Set();
  if (await exists(sourceDir)) {
    const entries = await fsp.readdir(sourceDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const match = entry.name.match(/^(.+)\.Target\.cs$/i);
      if (match) targets.add(match[1]);
    }
  }

  const editorTarget = `${projectName}Editor`;
  const gameTarget = projectName;
  const preferred = targets.has(editorTarget)
    ? editorTarget
    : [...targets].find((name) => /editor$/i.test(name))
      || [...targets][0]
      || editorTarget;

  return {
    preferredTarget: preferred,
    allTargets: [...targets].sort(),
    inferredGameTarget: targets.has(gameTarget) ? gameTarget : null
  };
}

function shouldIgnoreDirName(name) {
  const lower = String(name || "").toLowerCase();
  return IGNORE_DIRS.has(name) || IGNORE_DIRS.has(lower) || lower.startsWith("pytest-") || lower.startsWith("pytest-of-");
}

function isFixtureProjectPath(projectPath) {
  const normalized = String(projectPath || "").replace(/\\/g, "/").toLowerCase();
  return (
    normalized.includes("/local_holdout_fixtures/")
    || normalized.includes("/data/local_holdout_fixtures/")
    || normalized.includes("/text_snapshot/")
    || /\/data\/[^/]+\/holdout/i.test(normalized)
  );
}

async function walkForUProjects(root, maxDepth, depth = 0, results = []) {
  if (depth > maxDepth || results.length >= 200) return results;
  const st = await statSafe(root);
  if (!st || !st.isDirectory()) return results;

  const base = path.basename(root);
  if (depth > 0 && shouldIgnoreDirName(base)) return results;

  let entries = [];
  try {
    entries = await fsp.readdir(root, { withFileTypes: true });
  } catch {
    return results;
  }
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.isFile() && entry.name.toLowerCase().endsWith(".uproject")) {
      results.push(full);
      continue;
    }
    if (entry.isDirectory()) {
      if (shouldIgnoreDirName(entry.name)) continue;
      await walkForUProjects(full, maxDepth, depth + 1, results);
    }
  }
  return results;
}

function scoreProjectMatch(candidate, hint, workspaceRoot) {
  const lowerHint = String(hint || "").trim().toLowerCase();
  let score = 0;
  const projectDir = candidate.projectDir.toLowerCase();
  const projectFile = candidate.projectFile.toLowerCase();
  const projectName = candidate.projectName.toLowerCase();

  if (workspaceRoot && projectDir.startsWith(path.resolve(workspaceRoot).toLowerCase())) {
    score += 20;
  }

  if (process.env.ACTIVE_PROJECT) {
    const active = path.resolve(process.env.ACTIVE_PROJECT).toLowerCase();
    if (candidate.projectPath.toLowerCase() === active) score += 100;
  }

  if (lowerHint) {
    if (projectName === lowerHint) score += 80;
    if (projectFile === lowerHint || projectFile === `${lowerHint}.uproject`) score += 70;
    if (projectName.includes(lowerHint)) score += 40;
    if (projectDir.includes(lowerHint)) score += 30;
  } else if (path.basename(candidate.projectDir).toLowerCase() === projectName) {
    score += 10;
  }

  return score;
}

async function discoverProjects(workspaceRoot, configPath, options = {}) {
  const { config, roots } = resolveSearchRoots(workspaceRoot, configPath);
  const maxDepth = Number(options.maxDepth || process.env.PROJECT_SEARCH_MAX_DEPTH || 4);
  const found = new Map();

  for (const root of roots) {
    if (!(await exists(root))) continue;
    const matches = await walkForUProjects(root, maxDepth);
    for (const uprojectPath of matches) {
      found.set(path.resolve(uprojectPath), uprojectPath);
    }
  }

  const projects = [];
  for (const uprojectPath of found.values()) {
    if (isFixtureProjectPath(uprojectPath)) {
      continue;
    }
    try {
      const info = await readUProject(uprojectPath);
      const targets = await findTargetNames(info.projectDir, info.projectName);
      const st = await statSafe(info.projectPath);
      projects.push({
        ...info,
        ...targets,
        modifiedAt: st ? st.mtime.toISOString() : null,
        score: 0
      });
    } catch {
      // skip invalid uproject
    }
  }

  const byName = new Map();
  for (const project of projects) {
    const key = String(project.projectName || "").toLowerCase();
    const existing = byName.get(key);
    if (!existing) {
      byName.set(key, project);
      continue;
    }
    const existingFixture = isFixtureProjectPath(existing.projectPath);
    const candidateFixture = isFixtureProjectPath(project.projectPath);
    if (existingFixture && !candidateFixture) {
      byName.set(key, project);
      continue;
    }
    if (!existingFixture && candidateFixture) {
      continue;
    }
    const existingTime = existing.modifiedAt || "";
    const candidateTime = project.modifiedAt || "";
    if (candidateTime.localeCompare(existingTime) > 0) {
      byName.set(key, project);
    }
  }

  const deduped = Array.from(byName.values());
  deduped.sort((a, b) => {
    const timeA = a.modifiedAt || "";
    const timeB = b.modifiedAt || "";
    return timeB.localeCompare(timeA);
  });

  return { config, roots, projects: deduped };
}

async function resolveProjectSelection(workspaceRoot, configPath, options = {}) {
  const explicitProject = String(options.project || "").trim();
  const hint = String(options.hint || "").trim();
  const { config, roots } = resolveSearchRoots(workspaceRoot, configPath);

  async function projectFromPath(projectPath, score = 1000) {
    const activePath = path.resolve(projectPath);
    const info = await readUProject(activePath);
    const targets = await findTargetNames(info.projectDir, info.projectName);
    const st = await statSafe(info.projectPath);
    return {
      ...info,
      ...targets,
      modifiedAt: st ? st.mtime.toISOString() : null,
      score,
    };
  }

  if (explicitProject && explicitProject.toLowerCase().endsWith(".uproject")) {
    const resolved = path.isAbsolute(explicitProject)
      ? path.resolve(explicitProject)
      : path.resolve(workspaceRoot, explicitProject);
    if (await exists(resolved)) {
      try {
        const selected = await projectFromPath(resolved, 3000);
        return {
          config,
          roots,
          projects: [selected],
          selected,
          selectionReason: "explicit.project",
        };
      } catch {
        // Fall through to discovery.
      }
    }
  }

  const discovery = await discoverProjects(workspaceRoot, configPath, options);
  const projects = discovery.projects;

  if (projects.length === 0) {
    if (config.activeProject) {
      const activePath = path.resolve(config.activeProject);
      if (await exists(activePath)) {
        try {
          const selected = await projectFromPath(activePath, 1000);
          return {
            config,
            roots,
            projects: [selected],
            selected,
            selectionReason: "config.activeProject",
          };
        } catch {
          // no project
        }
      }
    }
    return {
      config,
      roots,
      projects,
      selected: null,
      selectionReason: "none-found",
      error: "No .uproject files found under configured search roots."
    };
  }

  const scored = projects.map((project) => ({
    ...project,
    score: scoreProjectMatch(project, hint, workspaceRoot)
  }));

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (b.modifiedAt || "").localeCompare(a.modifiedAt || "");
  });

  const best = scored[0];
  if (hint && best.score > 0) {
    return {
      config,
      roots,
      projects: scored,
      selected: best,
      selectionReason: "hint",
    };
  }

  if (config.activeProject) {
    const activePath = path.resolve(config.activeProject);
    const activeMatch = scored.find((project) => path.resolve(project.projectPath) === activePath);
    if (activeMatch) {
      return {
        config,
        roots,
        projects: scored,
        selected: activeMatch,
        selectionReason: "config.activeProject",
      };
    }
    if (await exists(activePath)) {
      try {
        const selected = await projectFromPath(activePath, 1000);
        return {
          config,
          roots,
          projects: [selected, ...scored],
          selected,
          selectionReason: "config.activeProject",
        };
      } catch {
        // Fall through to best discovered project.
      }
    }
  }

  if (hint && best.score === 0) {
    return {
      config,
      roots,
      projects: scored,
      selected: null,
      selectionReason: "hint-not-matched",
      error: `No project matched hint "${hint}".`,
      suggestions: scored.slice(0, 10).map((p) => ({
        projectFile: p.projectFile,
        projectPath: p.projectPath,
        preferredTarget: p.preferredTarget
      }))
    };
  }

  if (!hint && scored.length > 1 && scored[0].score === scored[1].score && scored[0].score <= 10) {
    return {
      config,
      roots,
      projects: scored,
      selected: null,
      selectionReason: "ambiguous",
      error: "Multiple Unreal projects found. Pass hint or set config.activeProject.",
      suggestions: scored.slice(0, 10).map((p) => ({
        projectFile: p.projectFile,
        projectPath: p.projectPath,
        preferredTarget: p.preferredTarget,
        modifiedAt: p.modifiedAt
      }))
    };
  }

  return {
    config,
    roots,
    projects: scored,
    selected: best,
    selectionReason: hint ? "hint-match" : scored.length === 1 ? "single-project" : "best-score"
  };
}

async function resolveBuildPlan(workspaceRoot, configPath, args = {}) {
  const selection = await resolveProjectSelection(workspaceRoot, configPath, {
    hint: args.hint,
    project: args.project,
    maxDepth: args.searchMaxDepth
  });

  if (!selection.selected) {
    return {
      ok: false,
      ...selection
    };
  }

  const project = selection.selected;
  let projectPath = project.projectPath;

  if (args.project) {
    const rawProject = String(args.project);
    if (rawProject.toLowerCase().endsWith(".uproject")) {
      projectPath = path.isAbsolute(rawProject)
        ? path.resolve(rawProject)
        : path.resolve(project.projectDir, rawProject);
    }
  }

  const engine = await resolveEngineRoot(
    project.engineAssociation,
    selection.config,
    args.engineRoot
  );

  if (!engine) {
    return {
      ok: false,
      ...selection,
      error: "Could not resolve Unreal Engine installation. Set engineRoot or config.defaultEngineRoot."
    };
  }

  const target = String(args.target || project.preferredTarget || `${project.projectName}Editor`).trim();
  const platform = String(
    args.platform
    || selection.config.defaultPlatform
    || process.env.UNREAL_PLATFORM
    || defaultPlatform()
  ).trim();
  const configuration = String(
    args.configuration
    || selection.config.defaultConfiguration
    || process.env.UNREAL_CONFIGURATION
    || "Development"
  ).trim();

  return {
    ok: true,
    ...selection,
    build: {
      engineRoot: engine.engineRoot,
      buildTool: engine.buildTool,
      buildToolKind: engine.buildToolKind,
      buildBat: engine.buildBat,
      engineSource: engine.source,
      engineWarning: engine.warning,
      requestedEngineAssociation: engine.requestedEngineAssociation || project.engineAssociation,
      projectPath,
      projectFile: path.basename(projectPath),
      projectDir: path.dirname(projectPath),
      projectName: projectNameFromPath(projectPath),
      target,
      platform,
      configuration,
      allTargets: project.allTargets,
      engineAssociation: project.engineAssociation
    }
  };
}

function defaultPlatform(hostPlatform = process.platform) {
  if (hostPlatform === "win32") return "Win64";
  if (hostPlatform === "darwin") return "Mac";
  return "Linux";
}

function buildProjectBrowsePaths(activeProjectPath, workspaceRoot) {
  const resolvedProject = path.resolve(activeProjectPath);
  const projectDir = path.dirname(resolvedProject);
  const projectName = projectNameFromPath(resolvedProject);
  const workspace = path.resolve(workspaceRoot || process.cwd());
  let modules = [];
  try {
    const raw = fs.readFileSync(resolvedProject, "utf8");
    const data = JSON.parse(raw);
    modules = Array.isArray(data.Modules) ? data.Modules.map((m) => m.Name).filter(Boolean) : [];
  } catch {
    modules = [];
  }
  const sourceModules = [];
  const sourceRootDir = path.join(projectDir, "Source");
  if (fs.existsSync(sourceRootDir)) {
    for (const entry of fs.readdirSync(sourceRootDir, { withFileTypes: true })) {
      if (entry.isDirectory()) sourceModules.push(entry.name);
    }
  }
  const primaryModule = modules[0] || sourceModules[0] || projectName;
  let sourceRoot = path.join(projectDir, "Source", primaryModule);
  if (!fs.existsSync(sourceRoot) && sourceModules.length) {
    sourceRoot = path.join(projectDir, "Source", sourceModules[0]);
  }
  const contentRoot = path.join(projectDir, "Content");
  const exportDir = path.join(projectDir, "Saved", "LmStudioMetadataExports");
  let browseAvailable = false;
  try {
    browseAvailable = projectDir.toLowerCase().startsWith(workspace.toLowerCase());
  } catch {
    browseAvailable = false;
  }
  const rel = (target) => {
    try {
      const value = path.relative(workspace, target);
      if (!value || value.startsWith("..")) return "";
      return value.split(path.sep).join("/");
    } catch {
      return "";
    }
  };
  return {
    uprojectPath: resolvedProject,
    projectName,
    projectDir,
    modules,
    primaryModule,
    sourceRoot,
    sourceModules,
    contentRoot,
    exportDir,
    workspaceRoot: workspace,
    browseAvailable,
    sourceBrowsePath: browseAvailable ? rel(sourceRoot) : "",
    contentBrowsePath: browseAvailable ? rel(contentRoot) : "",
    browseNote: browseAvailable
      ? ""
      : "Project is outside WORKSPACE_ROOT; search_files/list_directory may be unavailable."
  };
}

module.exports = {
  IGNORE_DIRS,
  loadConfig,
  saveConfig,
  getActiveProject,
  setActiveProject,
  listUnrealProjects,
  resolveSearchRoots,
  defaultEngineLocations,
  engineBuildToolCandidates,
  resolveEngineBuildTool,
  findEngineInstalls,
  discoverProjects,
  resolveProjectSelection,
  resolveBuildPlan,
  defaultPlatform,
  projectNameFromPath,
  buildProjectBrowsePaths
};
