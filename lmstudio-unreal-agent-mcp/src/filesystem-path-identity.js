"use strict";

const path = require("path");

function normalizePortablePath(value, options = {}) {
  let normalized = String(value || "").trim().normalize("NFC").replace(/\\/g, "/");
  if (options.stripProjectUri !== false) {
    normalized = normalized.replace(/^project:\/\//i, "");
  }
  while (normalized.startsWith("./")) normalized = normalized.slice(2);
  normalized = normalized.replace(/\/{2,}/g, "/");
  if (options.trimOuterSlashes === true) {
    normalized = normalized.replace(/^\/+|\/+$/g, "");
  } else if (normalized.length > 1) {
    normalized = normalized.replace(/\/+$/g, "");
  }
  return normalized;
}

function filesystemPathIdentity(value, hostPlatform = process.platform, options = {}) {
  const normalized = normalizePortablePath(value, options);
  return hostPlatform === "win32" ? normalized.toLowerCase() : normalized;
}

function absolutePathIdentity(value, hostPlatform = process.platform) {
  const resolved = path.resolve(String(value || "."));
  return filesystemPathIdentity(resolved, hostPlatform, { stripProjectUri: false });
}

function pathHasSuffixIdentity(candidate, suffix, hostPlatform = process.platform) {
  const candidateKey = filesystemPathIdentity(candidate, hostPlatform, {
    stripProjectUri: false,
  });
  const suffixKey = filesystemPathIdentity(suffix, hostPlatform, {
    trimOuterSlashes: true,
  });
  if (!candidateKey || !suffixKey) return false;
  return candidateKey === suffixKey || candidateKey.endsWith(`/${suffixKey}`);
}

module.exports = {
  absolutePathIdentity,
  filesystemPathIdentity,
  normalizePortablePath,
  pathHasSuffixIdentity,
};
