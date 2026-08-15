"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const HASH_SECTIONS = Object.freeze({
  transitionPolicyHash: "transitionPolicy",
  errorCatalogHash: "errorCatalog",
  authorizationSchemaHash: "authorizationSchema",
  controlSchemaHash: "controlSchema",
});

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalize(value[key])])
  );
}

function sectionHash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}

function specCandidates(options = {}) {
  const repositoryRoot = options.repositoryRoot ? path.resolve(options.repositoryRoot) : "";
  const componentRoot = options.componentRoot ? path.resolve(options.componentRoot) : "";
  return [...new Set([
    String(options.specPath || "").trim(),
    String(process.env.CONTROL_PROTOCOL_SPEC || "").trim(),
    repositoryRoot ? path.join(repositoryRoot, "config", "control_protocol_spec.json") : "",
    componentRoot ? path.join(componentRoot, "..", "config", "control_protocol_spec.json") : "",
    componentRoot ? path.join(componentRoot, "control-protocol-spec.json") : "",
    path.resolve(__dirname, "../../config/control_protocol_spec.json"),
  ].filter(Boolean).map((item) => path.resolve(item)))];
}

function embeddedSpec(options = {}) {
  const manifestPath = String(
    options.manifestPath || process.env.CONTROL_RUNTIME_MANIFEST || ""
  ).trim();
  if (!manifestPath || !fs.existsSync(manifestPath)) return null;
  try {
    const value = JSON.parse(fs.readFileSync(manifestPath, "utf8"))?.protocolSpec;
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

function validateSpec(spec) {
  if (!spec || typeof spec !== "object") throw new Error("control protocol spec must be an object");
  if (!Number.isInteger(spec.schemaVersion) || spec.schemaVersion < 1) {
    throw new Error("control protocol schemaVersion is missing");
  }
  if (!Number.isInteger(spec.protocolVersion) || spec.protocolVersion < 1) {
    throw new Error("control protocol protocolVersion is missing");
  }
  for (const section of Object.values(HASH_SECTIONS)) {
    if (!spec[section] || typeof spec[section] !== "object") {
      throw new Error(`control protocol ${section} is missing`);
    }
  }
  return spec;
}

function loadControlProtocolSpec(options = {}) {
  for (const candidate of specCandidates(options)) {
    if (!fs.existsSync(candidate)) continue;
    return validateSpec(JSON.parse(fs.readFileSync(candidate, "utf8")));
  }
  const embedded = embeddedSpec(options);
  if (embedded) return validateSpec(embedded);
  throw new Error("CONTROL_PROTOCOL_SPEC_UNAVAILABLE: config/control_protocol_spec.json is missing");
}

function controlProtocolIdentity(options = {}) {
  const spec = loadControlProtocolSpec(options);
  const output = { protocolVersion: Number(spec.protocolVersion) };
  for (const [field, section] of Object.entries(HASH_SECTIONS)) {
    output[field] = sectionHash(spec[section]);
  }
  return output;
}

module.exports = {
  HASH_SECTIONS,
  canonicalize,
  sectionHash,
  loadControlProtocolSpec,
  controlProtocolIdentity,
};
