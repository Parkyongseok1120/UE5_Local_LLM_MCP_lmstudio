"use strict";

const fs = require("fs");
const path = require("path");

function isWindowsHostPlatform(hostPlatform = process.platform) {
  return ["win32", "windows", "nt"].includes(
    String(hostPlatform || "").toLowerCase()
  );
}

function asciiWindowsFold(value) {
  return String(value || "").replace(/[A-Z]/g, (character) => character.toLowerCase());
}

function normalizePortablePath(value, options = {}) {
  // Filesystems are allowed to contain canonically equivalent Unicode names
  // as distinct entries. Keep the spelling supplied by the filesystem instead
  // of applying NFC/NFD normalization to an identity key.
  let normalized = String(value || "").trim().replace(/\\/g, "/");
  while (normalized.startsWith("./")) normalized = normalized.slice(2);
  if (options.stripProjectUri !== false) {
    normalized = normalized.replace(/^project:\/\//i, "");
  }
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
  return isWindowsHostPlatform(hostPlatform) ? asciiWindowsFold(normalized) : normalized;
}

function absolutePathIdentity(value, hostPlatform = process.platform) {
  return canonicalAbsolutePathIdentity(String(value || "."), hostPlatform);
}

function resolveCanonicalAbsolutePath(value, options = {}) {
  // Do not normalize or trim the lookup spelling before exists/realpath. On a
  // POSIX filesystem NFC and NFD (and even leading/trailing spaces) may name
  // different entries.
  const raw = value === null || value === undefined ? "" : String(value);
  if (!raw) return "";
  const basePath = String(options.basePath || process.cwd());
  let resolved = path.resolve(path.isAbsolute(raw) ? raw : path.join(basePath, raw));
  if (options.realpath !== false) {
    try {
      if (fs.existsSync(resolved)) {
        resolved = fs.realpathSync.native
          ? fs.realpathSync.native(resolved)
          : fs.realpathSync(resolved);
      }
    } catch {
      // A missing, inaccessible, or concurrently removed path keeps its
      // lexical absolute identity so project matching remains fail-closed.
    }
  }
  return String(resolved);
}

function canonicalAbsolutePathIdentity(
  value,
  hostPlatform = process.platform,
  options = {}
) {
  const resolved = resolveCanonicalAbsolutePath(value, options);
  if (!resolved) return "";
  if (!isWindowsHostPlatform(hostPlatform)) return resolved;
  return asciiWindowsFold(resolved.replace(/\\/g, "/"));
}

function absolutePathIsWithin(candidate, root, hostPlatform = process.platform) {
  const candidateIdentity = canonicalAbsolutePathIdentity(candidate, hostPlatform);
  const rootIdentity = canonicalAbsolutePathIdentity(root, hostPlatform);
  if (!candidateIdentity || !rootIdentity || candidateIdentity === rootIdentity) return false;
  const separator = isWindowsHostPlatform(hostPlatform) ? "/" : path.sep;
  const prefix = rootIdentity.endsWith(separator)
    ? rootIdentity
    : `${rootIdentity}${separator}`;
  return candidateIdentity.startsWith(prefix);
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
  absolutePathIsWithin,
  asciiWindowsFold,
  canonicalAbsolutePathIdentity,
  filesystemPathIdentity,
  isWindowsHostPlatform,
  normalizePortablePath,
  pathHasSuffixIdentity,
  resolveCanonicalAbsolutePath,
};
