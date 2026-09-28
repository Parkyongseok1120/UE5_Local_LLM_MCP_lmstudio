"use strict";

// Markdown is the content SSOT. The generated module is checked in so lms dev
// and installed bundles need neither runtime filesystem access nor repo paths.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const TOPICS = ["core", "design", "debugging", "code-style", "lifecycle", "multiplayer"];
const SIGNALS = ["diagnostic_present", "diagnostic_recurred", "observed_source_changed",
  "source_body_unavailable", "compilation_pending", "operation_outcome_unknown"];
const ID = /^[a-z][a-z0-9-]*$/;
const hash = value => crypto.createHash("sha256").update(value).digest("hex");

// Markers are metadata, never part of the legacy document body. Fenced examples
// remain literal text; malformed markers outside a fence fail at build time.
function splitSections(source) {
  const body = [], sections = new Map();
  let active = null, lines = [], fence = null;
  for (const line of source.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").split("\n")) {
    const fenceMatch = /^ {0,3}(\x60{3,}|~{3,})/.exec(line);
    if (fenceMatch) {
      const token = fenceMatch[1];
      if (!fence) fence = { char: token[0], length: token.length };
      else if (token[0] === fence.char && token.length >= fence.length
        && /^ {0,3}(?:\x60+|~+)\s*$/.test(line)) fence = null;
    }
    const marker = !fence && /^<!-- (\/?)guidance-section: ([a-z][a-z0-9-]*) -->$/.exec(line);
    if (marker) {
      const [, closing, id] = marker;
      if (!closing) {
        if (active || sections.has(id)) throw new Error("Duplicate or nested guidance section: " + id);
        active = id; lines = [];
      } else {
        if (active !== id || !lines.join("\n").trim()) throw new Error("Invalid guidance section end: " + id);
        sections.set(id, lines.join("\n").trim()); active = null;
      }
      continue;
    }
    if (!fence && /^\s*<!--\s*\/?guidance-section\b/.test(line)) throw new Error("Malformed guidance marker");
    body.push(line);
    if (active) lines.push(line);
  }
  if (active) throw new Error("Unterminated guidance section: " + active);
  return { text: body.join("\n").trim(), sections };
}

function validateApplicability(value = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some(key => !["engineVersions", "packages"].includes(key))) throw new Error("Invalid applicability");
  const versions = entries => Array.isArray(entries) && entries.length > 0 && entries.length <= 8
    && entries.every(v => typeof v === "string" && /^\d+(?:\.\d+){0,2}$/.test(v));
  if (value.engineVersions !== undefined && !versions(value.engineVersions)) throw new Error("Invalid engine versions");
  if (value.packages !== undefined && (!Array.isArray(value.packages) || !value.packages.length
    || value.packages.length > 8 || value.packages.some(p => !p || Object.keys(p).some(k => !["id", "versions"].includes(k))
      || !/^[a-z][a-z0-9.-]+$/.test(p.id) || !versions(p.versions)))) throw new Error("Invalid package applicability");
  return value;
}

function renderBundle(root) {
  const catalog = JSON.parse(fs.readFileSync(path.join(root, "catalog.json"), "utf8"));
  if (![1, 2].includes(catalog.schemaVersion) || !Array.isArray(catalog.documents)) {
    throw new Error("Unsupported design guidance catalogue");
  }
  const seen = new Set();
  const sectionBundle = [];
  const documents = catalog.documents.map(entry => {
    if (!/^[a-z][a-z-]*$/.test(entry.id) || seen.has(entry.id)
      || entry.file !== `${entry.id}.md` || !["common", "unity", "unreal"].includes(entry.engine)) {
      throw new Error("Invalid or duplicate design guidance entry");
    }
    seen.add(entry.id);
    const { text, sections } = splitSections(fs.readFileSync(path.join(root, entry.file), "utf8"));
    if (!text || text.length > 16000) throw new Error(`Invalid guidance size: ${entry.id}`);
    if (catalog.schemaVersion === 2) {
      if (!Array.isArray(entry.sections) || !entry.sections.length) throw new Error("Missing section catalogue: " + entry.id);
      const used = new Set();
      for (const section of entry.sections) {
        const engine = section.engine || entry.engine;
        if (!ID.test(section.id) || used.has(section.id) || !sections.has(section.id)
          || !Array.isArray(section.topics) || !section.topics.length || section.topics.some(t => !TOPICS.includes(t))
          || !Array.isArray(section.signals) || section.signals.some(s => !SIGNALS.includes(s))
          || !Array.isArray(section.requires) || section.requires.some(r => !/^[a-z][a-z0-9-]*\/[a-z][a-z0-9-]*$/.test(r))
          || !["common", "unity", "unreal"].includes(engine) || (entry.engine !== "common" && entry.engine !== engine)
          || !Number.isSafeInteger(section.priority) || section.priority < 0 || section.priority > 1000) {
          throw new Error("Invalid guidance section: " + entry.id + "/" + section.id);
        }
        used.add(section.id);
        const data = { id: entry.id + "/" + section.id, documentId: entry.id, engine,
          source: "docs/model-guidance/" + entry.file + "#" + section.id,
          topics: [...new Set(section.topics)], signals: [...new Set(section.signals)],
          priority: section.priority, requires: [...new Set(section.requires)],
          applicability: validateApplicability(section.applicability), text: sections.get(section.id) };
        sectionBundle.push({ ...data, revision: hash(JSON.stringify(data)) });
      }
      if (used.size !== sections.size) throw new Error("Uncatalogued guidance section: " + entry.id);
    } else if (sections.size) throw new Error("Section markers require catalogue v2");
    return { id: entry.id, engine: entry.engine, source: `docs/model-guidance/${entry.file}`,
      revision: crypto.createHash("sha256").update(text).digest("hex"), text };
  });
  const byId = new Map(sectionBundle.map(section => [section.id, section]));
  const visiting = new Set(), visited = new Set();
  function visit(section) {
    if (visiting.has(section.id)) throw new Error("Cyclic guidance dependency: " + section.id);
    if (visited.has(section.id)) return;
    visiting.add(section.id);
    for (const id of section.requires) {
      const dependency = byId.get(id);
      if (!dependency || (dependency.engine !== "common" && dependency.engine !== section.engine)
        || section.topics.some(topic => !dependency.topics.includes(topic) && !dependency.topics.includes("core"))) {
        throw new Error("Incompatible guidance dependency: " + id);
      }
      visit(dependency);
    }
    visiting.delete(section.id); visited.add(section.id);
  }
  sectionBundle.forEach(visit);
  return "// Generated by scripts/build-design-guidance.cjs. Edit docs/model-guidance/*.md instead.\n"
    + "export const bundledDesignGuidance = " + JSON.stringify(documents, null, 2) + " as const;\n"
    + "export const bundledGuidanceSections = " + JSON.stringify(sectionBundle, null, 2) + " as const;\n";
}

function main() {
  const pluginRoot = path.resolve(__dirname, "..");
  const output = path.join(pluginRoot, "src/generated-design-guidance.ts");
  const content = renderBundle(path.resolve(pluginRoot, "../docs/model-guidance"));
  if (process.argv.includes("--check")) {
    if (!fs.existsSync(output) || fs.readFileSync(output, "utf8").replace(/\r\n/g, "\n") !== content) {
      throw new Error("Design guidance bundle is stale; run npm run guidance:build");
    }
    return;
  }
  fs.writeFileSync(output, content, "utf8");
}

if (require.main === module) main();
module.exports = { renderBundle, splitSections };
