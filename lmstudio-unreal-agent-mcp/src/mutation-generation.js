"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { tryAcquirePathLock, releasePathLock } = require("./write-locks");

const LOCK_ATTEMPTS = 40;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mutationStatePath(projectRoot) {
  return path.join(path.resolve(projectRoot), ".agent", "state", "mutation.json");
}

function validationStatePath(projectRoot) {
  return path.join(path.resolve(projectRoot), ".agent", "state", "validation.json");
}

function defaultState() {
  return { mutationGeneration: 0, paths: {}, validatedGeneration: 0, updatedAt: new Date().toISOString() };
}

function mutationStateCorruptError(cause) {
  const err = new Error("MUTATION_STATE_CORRUPT");
  err.errorCode = "MUTATION_STATE_CORRUPT";
  err.cause = cause;
  return err;
}

async function readMutationState(projectRoot) {
  const file = mutationStatePath(projectRoot);
  if (!fs.existsSync(file)) {
    return defaultState();
  }
  try {
    return { ...defaultState(), ...JSON.parse(await fsp.readFile(file, "utf8")) };
  } catch (err) {
    throw mutationStateCorruptError(err);
  }
}

async function writeMutationState(projectRoot, state) {
  const file = mutationStatePath(projectRoot);
  await fsp.mkdir(path.dirname(file), { recursive: true });
  state.updatedAt = new Date().toISOString();
  atomicWriteText(file, JSON.stringify(state, null, 2));
}

async function withMutationLock(projectRoot, fn) {
  const stateFile = mutationStatePath(projectRoot);
  for (let attempt = 0; attempt < LOCK_ATTEMPTS; attempt += 1) {
    const lock = tryAcquirePathLock(stateFile, "mutation_generation", { heartbeat: true });
    if (lock.ok) {
      try {
        return await fn();
      } finally {
        releasePathLock(stateFile);
      }
    }
    await sleep(Math.min(50 * (attempt + 1), 500));
  }
  throw new Error("mutation generation lock busy");
}

async function recordMutation(projectRoot, relPath, content) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    state.mutationGeneration = int(state.mutationGeneration) + 1;
    state.paths[String(relPath).replace(/\\/g, "/")] = sha256Text(String(content ?? ""));
    await writeMutationState(projectRoot, state);
    return { mutationGeneration: state.mutationGeneration };
  });
}

function int(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : 0;
}

async function beginValidation(projectRoot) {
  const state = await readMutationState(projectRoot);
  return { startGeneration: int(state.mutationGeneration), state };
}

async function finishValidation(projectRoot, startGeneration) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const current = int(state.mutationGeneration);
    if (current !== int(startGeneration)) {
      return { validationStale: true, validatedGeneration: null, mutationGeneration: current };
    }
    state.validatedGeneration = current;
    await writeMutationState(projectRoot, state);
    return { validationStale: false, validatedGeneration: current, mutationGeneration: current };
  });
}

async function finishValidationAndClear(projectRoot, startGeneration) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const current = int(state.mutationGeneration);
    if (current !== int(startGeneration)) {
      return { validationStale: true, validatedGeneration: null, mutationGeneration: current };
    }
    state.validatedGeneration = current;
    await writeMutationState(projectRoot, state);
    const validationFile = validationStatePath(projectRoot);
    try {
      if (fs.existsSync(validationFile)) {
        fs.unlinkSync(validationFile);
      }
    } catch {
      // Best-effort cleanup under the same lock scope.
    }
    return { validationStale: false, validatedGeneration: current, mutationGeneration: current };
  });
}

async function recordDeletion(projectRoot, relPath) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    state.mutationGeneration = int(state.mutationGeneration) + 1;
    const normalized = String(relPath || "").replace(/\\/g, "/");
    if (normalized && state.paths) {
      delete state.paths[normalized];
    }
    await writeMutationState(projectRoot, state);
    return { mutationGeneration: state.mutationGeneration };
  });
}

async function beginBuild(projectRoot) {
  const state = await readMutationState(projectRoot);
  return {
    buildStartGeneration: int(state.mutationGeneration),
    validatedGeneration: int(state.validatedGeneration),
    mutationGeneration: int(state.mutationGeneration),
  };
}

async function finishBuild(projectRoot, buildStartGeneration) {
  const state = await readMutationState(projectRoot);
  const endGeneration = int(state.mutationGeneration);
  const stale = endGeneration !== int(buildStartGeneration);
  return {
    buildEndGeneration: endGeneration,
    buildStale: stale,
    mutationGeneration: endGeneration,
  };
}

module.exports = {
  mutationStatePath,
  readMutationState,
  recordMutation,
  recordDeletion,
  withMutationLock,
  beginValidation,
  finishValidation,
  finishValidationAndClear,
  beginBuild,
  finishBuild,
};
