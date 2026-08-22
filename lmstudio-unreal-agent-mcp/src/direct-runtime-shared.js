"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { failure } = require("./direct-response.js");

const fsp = fs.promises;

function envFlag(env, name, fallback = false) {
  const value = env[name];
  if (value === undefined || value === null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function clamp(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function nowStamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function cleanArgs(args) {
  return args && typeof args === "object" && !Array.isArray(args) ? args : {};
}

function statSignature(stat, prefix = "") {
  return stat ? `${prefix}${stat.dev || 0}:${stat.ino || 0}:${stat.size}:${Math.trunc(stat.mtimeMs)}` : `${prefix}missing`;
}

function stableStatIdentity(stat) {
  return stat
    ? `${stat.dev || 0}:${stat.ino || 0}:${stat.size}:${Math.trunc(stat.mtimeMs)}:${Math.trunc(stat.ctimeMs)}`
    : "missing";
}

async function statOrNull(target) {
  try {
    return await fsp.stat(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function isBinary(buffer) {
  const sample = buffer.subarray(0, Math.min(buffer.length, 8192));
  return sample.includes(0);
}

function relativeSlash(root, target) {
  return path.relative(root, target).replace(/\\/g, "/") || ".";
}

function errorFromException(error) {
  const message = String(error?.message || error || "Unknown tool error");
  const validationLike = /required|invalid|must|outside|escapes|not a|unsupported|expected/i.test(message);
  return failure(
    validationLike ? "INVALID_ARGUMENT" : "INTERNAL_ERROR",
    message,
    { retryAllowed: validationLike, retryMode: validationLike ? "different_arguments" : "none" },
  );
}

function normalizeProjectRoot(activeProject, explicit = "") {
  const raw = String(explicit || "").trim();
  if (raw) {
    const resolved = path.resolve(raw);
    return resolved.toLowerCase().endsWith(".uproject") ? path.dirname(resolved) : resolved;
  }
  return activeProject ? path.dirname(path.resolve(activeProject)) : "";
}

module.exports = {
  clamp,
  cleanArgs,
  envFlag,
  errorFromException,
  isBinary,
  normalizeProjectRoot,
  nowStamp,
  relativeSlash,
  stableStatIdentity,
  statOrNull,
  statSignature,
};
