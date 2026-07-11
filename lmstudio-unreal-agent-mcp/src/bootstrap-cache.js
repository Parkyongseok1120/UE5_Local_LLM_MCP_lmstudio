"use strict";

const BOOTSTRAP_CACHE_TTL_MS = Number(process.env.BOOTSTRAP_CACHE_TTL_MS || 3600 * 1000);

function evaluateBootstrapCache(cache, activeProject) {
  const required = ["unreal_get_active_project", "unreal_rag_health", "get_workspace_info"];
  if (!activeProject) {
    return { valid: false, canSkipSteps: false, missingSteps: required };
  }
  if (!cache) {
    return { valid: false, canSkipSteps: false, missingSteps: required };
  }
  const ageMs = Date.now() - Number(cache.cachedAt || 0);
  const ttlMs = Number(cache.ttlSec || 3600) * 1000;
  if (BOOTSTRAP_CACHE_TTL_MS > 0 && ageMs > ttlMs) {
    return { valid: false, canSkipSteps: false, missingSteps: required, stale: true };
  }
  const steps = new Set(cache.stepsCompleted || []);
  const missingSteps = required.filter((step) => !steps.has(step));
  const projectMatches = !activeProject || !cache.projectPath || cache.projectPath === activeProject;
  const canSkipSteps = missingSteps.length === 0 && projectMatches && cache.ragHealthOk !== false;
  return {
    valid: canSkipSteps,
    canSkipSteps,
    missingSteps,
    projectPath: cache.projectPath || null,
    cachedAt: cache.cachedAt || null,
  };
}

function mergeBootstrapCache(existing, patch) {
  const projectChanged = Boolean(
    patch.projectPath && existing?.projectPath && patch.projectPath !== existing.projectPath
  );
  const workspaceChanged = Boolean(
    patch.workspaceHash && existing?.workspaceHash && patch.workspaceHash !== existing.workspaceHash
  );
  const ageMs = existing?.cachedAt ? Date.now() - Number(existing.cachedAt) : 0;
  const ttlMs = Number(existing?.ttlSec || 3600) * 1000;
  const expired = Boolean(existing && BOOTSTRAP_CACHE_TTL_MS > 0 && ageMs > ttlMs);
  const reset = projectChanged || workspaceChanged || expired || patch.forceRefresh === true;
  return {
    projectPath: patch.projectPath || existing?.projectPath || "",
    ragHealthOk: reset
      ? (patch.ragHealthOk ?? false)
      : (patch.ragHealthOk ?? existing?.ragHealthOk ?? false),
    workspaceHash: patch.workspaceHash || existing?.workspaceHash || "",
    stepsCompleted: reset
      ? (patch.stepsCompleted || [])
      : Array.from(new Set([...(existing?.stepsCompleted || []), ...(patch.stepsCompleted || [])])),
    cachedAt: reset ? Date.now() : (existing?.cachedAt || Date.now()),
    ttlSec: patch.ttlSec || existing?.ttlSec || Math.floor(BOOTSTRAP_CACHE_TTL_MS / 1000) || 3600,
    stale: expired && !reset,
  };
}

module.exports = {
  BOOTSTRAP_CACHE_TTL_MS,
  evaluateBootstrapCache,
  mergeBootstrapCache,
};
