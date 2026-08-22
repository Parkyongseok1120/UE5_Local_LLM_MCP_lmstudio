"use strict";

const fs = require("fs");
const path = require("path");
const { cleanTestNames } = require("./automation-command-contract");
const {
  dependencyNamesFromBuildFile,
  isAutomationSourceFile,
  parseAutomationDeclarations,
} = require("./automation-source-parser");

const SKIP_DIRS = new Set([".git", "Binaries", "DerivedDataCache", "Intermediate", "Saved"]);
const SKIP_PLUGIN_DIRS = new Set(["content", "config", "resources"]);

function directoryExists(candidate) {
  try { return fs.statSync(candidate).isDirectory(); } catch { return false; }
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
      if (entry.name.toLowerCase() === "source") containers.push(child);
      else if (!SKIP_PLUGIN_DIRS.has(entry.name.toLowerCase())) pending.push(child);
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
      const dependencies = dependencyNamesFromBuildFile(buildFile);
      if (!dependencies.complete) complete = false;
      modules.push({
        name: entry.name,
        relativeRoot: path.relative(root, moduleRoot).replace(/\\/g, "/"),
        dependencies: dependencies.names,
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
    return { label, target, moduleRoot, mapped: Boolean(modulePath && directoryExists(modulePath)) };
  });
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
  const roots = scopeRoots.map((relative) => path.resolve(root, relative)).filter((candidate) => {
    const relative = path.relative(root, candidate);
    return relative !== ".." && !relative.startsWith(`..${path.sep}`)
      && !path.isAbsolute(relative) && fs.existsSync(candidate);
  });
  return {
    scopeBound,
    scopeTargets: targetRows.map((row) => row.target).filter(Boolean),
    scopeRoots,
    unmappedScopeTargets: targetRows.filter((row) => !row.mapped)
      .map((row) => row.target || row.label),
    roots,
    dependencyGraphComplete,
  };
}

function discoverAutomationTests(projectRoot, options = {}) {
  const maxFiles = Math.max(1, Math.min(5000, Number(options.maxFiles || 2000)));
  const scope = resolveAutomationScopeRoots(projectRoot, options.scopeTargets);
  const declarations = [];
  const declarationKeys = new Set();
  let inspectedFileCount = 0;
  let limitReachedWithUnvisitedEntries = false;
  let discoveryComplete = true;
  const pending = [...scope.roots];
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
      for (const name of parseAutomationDeclarations(text)) {
        const identity = name.toLowerCase();
        if (declarationKeys.has(identity)) continue;
        declarationKeys.add(identity);
        declarations.push({
          name,
          sourceFile: path.relative(path.resolve(projectRoot), absolutePath).replace(/\\/g, "/"),
          moduleRoot: moduleRootForTarget(projectRoot, absolutePath),
        });
      }
      if (inspectedFileCount >= maxFiles) {
        limitReachedWithUnvisitedEntries = entries.slice(entryIndex + 1).some((remaining) => (
          (remaining.isDirectory() && !SKIP_DIRS.has(remaining.name))
          || (remaining.isFile() && isAutomationSourceFile(remaining.name))
        ));
        break;
      }
    }
  }
  declarations.sort((left, right) => left.name.localeCompare(right.name, "en", { sensitivity: "base" })
    || left.sourceFile.localeCompare(right.sourceFile, "en", { sensitivity: "base" }));
  const names = declarations.map((item) => item.name);
  const rootsFound = [...new Set(names.map((name) => name.split(".")[0]).filter(Boolean))];
  const suggestedFilters = scope.scopeBound
    ? cleanTestNames(names)
    : rootsFound.sort((left, right) => left.localeCompare(right, "en", { sensitivity: "base" }));
  return {
    names,
    tests: declarations,
    count: names.length,
    inspectedFileCount,
    truncated: Boolean(pending.length || limitReachedWithUnvisitedEntries || !discoveryComplete
      || scope.unmappedScopeTargets.length > 0
      || (scope.scopeBound && !scope.dependencyGraphComplete)),
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

module.exports = {
  discoverAutomationTests,
  moduleRootForTarget,
  resolveAutomationScopeRoots,
};
