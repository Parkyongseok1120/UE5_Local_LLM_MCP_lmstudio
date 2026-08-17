"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { controlProtocolIdentity } = require("./control-protocol-spec");

const PROTOCOL_VERSION = 2;

function controlProtocolError(code, message) {
  const error = new Error(`${code}: ${message}`);
  error.code = code;
  return error;
}
const PROTOCOL_IDENTITY_FIELDS = Object.freeze([
  "transitionPolicyHash",
  "errorCatalogHash",
  "authorizationSchemaHash",
  "controlSchemaHash",
]);

function packagedGitCommit(componentRoot) {
  try {
    const manifest = JSON.parse(
      fs.readFileSync(path.join(componentRoot, "control-runtime.json"), "utf8")
    );
    return String(manifest?.components?.compactor?.gitCommit || "").trim().slice(0, 80);
  } catch {
    return "";
  }
}

function walkFiles(directory, suffixes) {
  if (!fs.existsSync(directory)) return [];
  const output = [];
  const pending = [directory];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(full);
      else if (entry.isFile() && suffixes.some((suffix) => entry.name.endsWith(suffix))) {
        output.push(full);
      }
    }
  }
  return output;
}

function componentIdentity(componentRoot, options = {}) {
  const base = path.resolve(componentRoot);
  const files = [
    path.join(base, "package.json"),
    path.join(base, "manifest.json"),
    ...walkFiles(path.join(base, "src"), [".js", ".ts"]),
  ].filter((file) => fs.existsSync(file)).sort((left, right) => {
    const leftPath = path.relative(base, left).replace(/\\/g, "/");
    const rightPath = path.relative(base, right).replace(/\\/g, "/");
    return leftPath < rightPath ? -1 : leftPath > rightPath ? 1 : 0;
  });
  if (!files.length) throw new Error(`no compactor runtime files found under ${base}`);
  const digest = crypto.createHash("sha256");
  for (const file of files) {
    digest.update(path.relative(base, file).replace(/\\/g, "/"), "utf8");
    digest.update("\0");
    digest.update(fs.readFileSync(file));
    digest.update("\0");
  }
  let componentVersion = "unknown";
  try { componentVersion = String(JSON.parse(fs.readFileSync(path.join(base, "package.json"), "utf8")).version || "unknown"); }
  catch {}
  let gitCommit = String(process.env.CONTROL_RUNTIME_GIT_COMMIT || "").trim().slice(0, 80);
  if (!gitCommit) {
    const result = spawnSync("git", ["rev-parse", "HEAD"], {
      cwd: base,
      encoding: "utf8",
      timeout: 3000,
      windowsHide: true,
    });
    if (result.status === 0) gitCommit = String(result.stdout || "").trim().slice(0, 80);
  }
  // Installed plugins normally have no .git directory. The installer writes
  // an immutable runtime manifest next to the shipped source, which is the
  // package-time commit identity in that case. Source drift still fails on
  // buildHash before this value is trusted.
  if (!gitCommit) gitCommit = packagedGitCommit(base);
  const protocol = controlProtocolIdentity({
    ...options,
    repositoryRoot: path.resolve(base, ".."),
    componentRoot: base,
  });
  if (protocol.protocolVersion !== PROTOCOL_VERSION) {
    throw new Error("CONTROL_RUNTIME_VERSION_MISMATCH: protocol spec version differs from runtime");
  }
  return {
    component: "compactor",
    buildHash: digest.digest("hex"),
    gitCommit,
    componentVersion,
    protocolVersion: PROTOCOL_VERSION,
    ...Object.fromEntries(PROTOCOL_IDENTITY_FIELDS.map((field) => [field, protocol[field]])),
  };
}

function verifyRuntimeComponent(options = {}) {
  const componentRoot = path.resolve(options.componentRoot || path.join(__dirname, ".."));
  const manifestPath = path.resolve(
    options.manifestPath
      || process.env.CONTROL_RUNTIME_MANIFEST
      || path.join(componentRoot, "control-runtime.json")
  );
  const required = options.required === true
    || /^(?:1|true|yes|on)$/i.test(String(process.env.CONTROL_RUNTIME_REQUIRED || ""));
  const running = componentIdentity(componentRoot, { manifestPath });
  let expectedGitCommit = String(
    options.expectedGitCommit || process.env.CONTROL_RUNTIME_EXPECTED_GIT_COMMIT || ""
  ).trim().slice(0, 80);
  const provenance = (verified, expected = null) => {
    const installedGitCommit = String(expected?.gitCommit || running.gitCommit || "");
    const sourceHeadMatched = expectedGitCommit
      ? installedGitCommit === expectedGitCommit
      : null;
    return {
      bundleIntegrityVerified: Boolean(verified),
      installedGitCommit,
      expectedGitCommit,
      sourceHeadMatched,
      runtimeStale: sourceHeadMatched === false,
      runtimeVerified: Boolean(verified && sourceHeadMatched !== false),
    };
  };
  if (!fs.existsSync(manifestPath)) {
    if (required) throw new Error("CONTROL_RUNTIME_VERSION_MISMATCH: manifest is required");
    return { ok: true, verified: false, reason: "manifest_not_configured", running, ...provenance(false) };
  }
  let expected;
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    if (!expectedGitCommit) {
      expectedGitCommit = String(manifest?.expectedSourceGitCommit || "").trim().slice(0, 80);
    }
    expected = manifest?.components?.compactor;
  }
  catch (error) {
    throw new Error(`CONTROL_RUNTIME_VERSION_MISMATCH: manifest unavailable (${error.message || error})`);
  }
  if (!expected || typeof expected !== "object") {
    throw new Error("CONTROL_RUNTIME_VERSION_MISMATCH: compactor identity is missing");
  }
  const mismatches = [
    "buildHash",
    "componentVersion",
    "protocolVersion",
    "gitCommit",
    ...PROTOCOL_IDENTITY_FIELDS,
  ]
    .filter((key) => String(expected[key] || "") !== String(running[key] || ""));
  if (mismatches.length) {
    throw new Error(`CONTROL_RUNTIME_VERSION_MISMATCH: compactor differs in ${mismatches.join(", ")}`);
  }
  const status = provenance(true, expected);
  if (status.runtimeStale) {
    throw controlProtocolError(
      "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH",
      `installed ${status.installedGitCommit || "unknown"} does not match expected ${status.expectedGitCommit}`
    );
  }
  return { ok: true, verified: true, manifestPath, expected, running, ...status };
}

module.exports = {
  PROTOCOL_VERSION,
  PROTOCOL_IDENTITY_FIELDS,
  componentIdentity,
  verifyRuntimeComponent,
};
