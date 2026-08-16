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
  const componentRoot = options.componentRoot ? path.resolve(options.componentRoot) : path.resolve(__dirname, "..");
  const repositoryRoot = options.repositoryRoot ? path.resolve(options.repositoryRoot) : path.resolve(componentRoot, "..");
  const moduleRoot = path.resolve(options.moduleRoot || path.join(__dirname, ".."));
  return [...new Set([
    String(options.specPath || "").trim(),
    String(process.env.CONTROL_PROTOCOL_SPEC || "").trim(),
    path.join(repositoryRoot, "config", "control_protocol_spec.json"),
    path.join(componentRoot, "..", "config", "control_protocol_spec.json"),
    path.join(componentRoot, "control-protocol-spec.json"),
    path.join(moduleRoot, "..", "config", "control_protocol_spec.json"),
  ].filter(Boolean).map((item) => path.resolve(item)))];
}

function embeddedSpec(options = {}) {
  const moduleRoot = path.resolve(options.moduleRoot || path.join(__dirname, ".."));
  const candidates = [...new Set([
    String(options.manifestPath || "").trim(),
    String(process.env.CONTROL_RUNTIME_MANIFEST || "").trim(),
    options.componentRoot ? path.join(options.componentRoot, "control-runtime.json") : "",
    path.join(moduleRoot, "control-runtime.json"),
  ].filter(Boolean).map((item) => path.resolve(item)))];
  for (const manifestPath of candidates) {
    if (!fs.existsSync(manifestPath)) continue;
    try {
      const value = JSON.parse(fs.readFileSync(manifestPath, "utf8"))?.protocolSpec;
      if (value && typeof value === "object") return value;
    } catch {
      // A corrupt candidate cannot shadow the next explicit packaged source.
    }
  }
  return null;
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
