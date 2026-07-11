"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { tryAcquirePathLock, releasePathLock } = require("./write-locks");

function mutationStatePath(projectRoot) {
  return path.join(path.resolve(projectRoot), ".agent", "state", "mutation.json");
}

function defaultState() {
  return { mutationGeneration: 0, paths: {}, updatedAt: new Date().toISOString() };
}

async function readMutationState(projectRoot) {
  const file = mutationStatePath(projectRoot);
  if (!fs.existsSync(file)) {
    return defaultState();
  }
  try {
    return { ...defaultState(), ...JSON.parse(await fsp.readFile(file, "utf8")) };
  } catch {
    return defaultState();
  }
}

async function writeMutationState(projectRoot, state) {
  const file = mutationStatePath(projectRoot);
  await fsp.mkdir(path.dirname(file), { recursive: true });
  state.updatedAt = new Date().toISOString();
  atomicWriteText(file, JSON.stringify(state, null, 2));
}

async function recordMutation(projectRoot, relPath, content) {
  const abs = path.isAbsolute(relPath) ? relPath : path.join(projectRoot, relPath);
  const lock = tryAcquirePathLock(abs, "mutation_generation");
  if (!lock.ok) {
    throw new Error("mutation generation lock busy");
  }
  try {
    const state = await readMutationState(projectRoot);
    state.mutationGeneration = int(state.mutationGeneration) + 1;
    state.paths[String(relPath).replace(/\\/g, "/")] = sha256Text(String(content ?? ""));
    await writeMutationState(projectRoot, state);
    return { mutationGeneration: state.mutationGeneration };
  } finally {
    releasePathLock(abs);
  }
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
  const state = await readMutationState(projectRoot);
  const current = int(state.mutationGeneration);
  if (current !== int(startGeneration)) {
    return { validationStale: true, validatedGeneration: null, mutationGeneration: current };
  }
  return { validationStale: false, validatedGeneration: current, mutationGeneration: current };
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
  beginValidation,
  finishValidation,
  beginBuild,
  finishBuild,
};
