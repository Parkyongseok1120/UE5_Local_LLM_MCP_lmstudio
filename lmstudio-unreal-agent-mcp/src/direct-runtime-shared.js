"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { failure } = require("./direct-response.js");

const { stableStatIdentity, statOrNull, isBinary } = require("../../shared-tool-core/direct-file-snapshot");

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







function relativeSlash(root, target) {
  return path.relative(root, target).replace(/\\/g, "/") || ".";
}

function errorFromException(error) {
  const message = String(error?.message || error || "Unknown tool error");
  if (error?.code === "REQUEST_CANCELLED") return failure("REQUEST_CANCELLED", message, {
    details: error.details || { cancelled: true }, retryAllowed: false, retryMode: "none",
  });
  // Path-policy errors carry their own stable code and observed details.
  if (typeof error?.directErrorCode === "string" && error.directErrorCode) {
    return failure(error.directErrorCode, message, {
      retryAllowed: true,
      retryMode: "different_arguments",
      details: error.details && typeof error.details === "object" ? error.details : {},
      ...(error.suggestion ? { suggestion: error.suggestion } : {}),
    });
  }
  // Capability handlers already classify schema/argument failures with an
  // explicit error code. Preserve that signal instead of guessing from the
  // wording (for example, "base/head require comparison=range" does not
  // contain the word "required"). The message heuristic remains a fallback
  // for legacy handlers that throw plain Errors.
  const explicitCode = String(error?.code || "").trim().toLowerCase();
  const validationLike = explicitCode === "invalid_argument"
    || explicitCode === "invalid_arguments"
    || /required|invalid|must|outside|escapes|not a|unsupported|expected/i.test(message);
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
