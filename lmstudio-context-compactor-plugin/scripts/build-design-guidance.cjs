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
const CONTRACT_MARKER = /^<!-- (\/?)contract-summary: ([a-z][a-z0-9-]*) -->$/;
const hash = value => crypto.createHash("sha256").update(value).digest("hex");

// Markers are metadata, never part of the legacy document body. Fenced examples
// remain literal text; malformed markers outside a fence fail at build time.
function splitSections(source) {
  const body = [], sections = new Map(), contractIds = new Map();
  let active = null, lines = [], fence = null, inContract = false;
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
    // Contract summary markers locate generated text; the text stays, the markers do not.
    const contractMarker = !fence && CONTRACT_MARKER.exec(line);
    if (contractMarker) { inContract = !contractMarker[1]; continue; }
    if (!fence && /^\s*<!--\s*\/?contract-summary\b/.test(line)) throw new Error("Malformed contract summary marker");
    body.push(line);
    if (active) lines.push(line);
    // Generated summary lines name the contract ids this section delivers.
    const contractLine = inContract && active && /^- \x60([a-z][a-z0-9.-]*)\x60 /.exec(line);
    if (contractLine) contractIds.set(active, [...(contractIds.get(active) || []), contractLine[1]]);
  }
  if (active) throw new Error("Unterminated guidance section: " + active);
  return { text: body.join("\n").trim(), sections, contractIds };
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

// A section that renders engine facts is only as applicable as the versions
// those facts were validated on. The catalogue may not drift from the contracts.
function verifyContractApplicability(sectionBundle, contractSets) {
  const owner = new Map();
  for (const data of contractSets) for (const contract of data.contracts) owner.set(contract.id, { data, contract });
  for (const section of sectionBundle) {
    for (const id of section.contractIds || []) {
      const entry = owner.get(id);
      if (!entry) throw new Error(`Section ${section.id} lists unknown contract ${id}`);
      if (entry.contract.kind !== "engine_fact") continue;
      const declared = section.applicability.engineVersions || [];
      const validated = entry.data.validatedEngineVersions;
      if (declared.length !== validated.length || declared.some(version => !validated.includes(version))) {
        throw new Error(`Section ${section.id} engineVersions [${declared}] differ from ${entry.data.engine} contract validatedEngineVersions [${validated}]`);
      }
    }
  }
}

function renderBundle(root, contractSets = []) {
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
    const { text, sections, contractIds } = splitSections(fs.readFileSync(path.join(root, entry.file), "utf8"));
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
          applicability: validateApplicability(section.applicability), text: sections.get(section.id),
          ...(contractIds.has(section.id) ? { contractIds: contractIds.get(section.id) } : {}) };
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
  if (contractSets.length) verifyContractApplicability(sectionBundle, contractSets);
  return "// Generated by scripts/build-design-guidance.cjs. Edit docs/model-guidance/*.md instead.\n"
    + "export const bundledDesignGuidance = " + JSON.stringify(documents, null, 2) + " as const;\n"
    + "export const bundledGuidanceSections = " + JSON.stringify(sectionBundle, null, 2) + " as const;\n"
    + "export const bundledEngineClaimFacts = " + JSON.stringify(engineClaimFacts(contractSets), null, 2) + " as const;\n"
    + "export const bundledArchitectureCatalog = " + JSON.stringify(renderPatternCatalog(root), null, 2) + " as const;\n";
}

function canonical(value) {
  if(Array.isArray(value))return value.map(canonical);
  if(value && typeof value==="object")return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return typeof value==="string"?value.replace(/\r\n?/g,"\n"):value;
}
function renderPatternCatalog(root) {
  const catalog=JSON.parse(fs.readFileSync(path.join(root,"catalog.json"),"utf8"));
  const seen=new Set(),sections=new Map();
  const patterns=(catalog.patterns||[]).map(entry=>{
    if(!/^ue\.[a-z][a-z-]*$/.test(entry.patternId) || seen.has(entry.patternId)
      || entry.file!=="unreal-patterns.md" || !ID.test(entry.section) || typeof entry.title!=="string"
      || !Array.isArray(entry.facets) || !entry.facets.every(f=>/^[a-z_]+$/.test(f))
      || !Array.isArray(entry.sources) || !entry.sources.length || entry.sources.some(s=>!/^https:\/\/dev\.epicgames\.com\//.test(s.url)
        || s.verifiedFor!=="general_concept"))throw new Error("Invalid architecture pattern: "+entry.patternId);
    seen.add(entry.patternId);
    if(!sections.has(entry.file))sections.set(entry.file,splitSections(fs.readFileSync(path.join(root,entry.file),"utf8")).sections);
    const text=sections.get(entry.file).get(entry.section);
    if(!text || text.length>4000)throw new Error("Missing/oversized pattern section: "+entry.patternId);
    for(const field of ["적용:","비적용:","소유권:","연결과 수명:","비용과 검증:"])if(!text.includes(field))throw new Error("Missing pattern field "+field);
    const body={patternId:entry.patternId,sectionId:"unreal-patterns/"+entry.section,title:entry.title,text,
      applicability:{concept:"unreal",validatedApiVersions:[]},selection:{facets:[...new Set(entry.facets)].sort()},
      sources:[...entry.sources].sort((a,b)=>a.url.localeCompare(b.url,"en"))};
    return {...body,contentHash:hash(JSON.stringify(canonical(body)))};
  }).sort((a,b)=>a.patternId.localeCompare(b.patternId,"en"));
  const catalogHash=hash(JSON.stringify(canonical({schemaVersion:1,patterns:patterns.map(({patternId,contentHash})=>({patternId,contentHash}))})));
  const baseline=patterns.length?sections.get("unreal-patterns.md").get("baseline"):"";
  if(patterns.length && !baseline)throw new Error("Missing architecture baseline");
  return {schemaVersion:1,catalogHash,baseline,patterns};
}

// Engine facts that name report wording (claimMarkers) also ship as data, so a
// runtime claim check can hand the model the exact contract statement. The
// markers must compile as JavaScript patterns with the flags the check uses.
function engineClaimFacts(contractSets) {
  const facts = [];
  for (const data of contractSets) {
    for (const contract of data.contracts) {
      if (contract.claimMarkers === undefined) continue;
      if (contract.kind !== "engine_fact" || !Array.isArray(contract.claimMarkers) || !contract.claimMarkers.length)
        throw new Error("Invalid claim markers: " + contract.id);
      for (const marker of contract.claimMarkers) {
        try { new RegExp(marker, "iu"); } catch (error) { throw new Error(`Invalid claim marker in ${contract.id}: ${error.message}`); }
      }
      facts.push({ id: contract.id, engine: data.engine, contractSet: `${data.engine}_contracts@${data.revision}`,
        engineVersions: contract.applicability.engineVersions, statement: contract.statement,
        evidenceRefs: contract.evidenceRefs.map(ref => ({ path: ref.path, symbol: ref.symbol,
          ...(ref.lines ? { lines: ref.lines } : ref.line ? { line: ref.line } : {}) })),
        claimMarkers: contract.claimMarkers });
    }
  }
  return facts;
}

// config/unreal_contracts.json is the SSOT for engine facts and project
// policies (strictly validated by scripts/unreal_contracts.py). Guidance
// Markdown carries a generated copy between contract-summary markers so the
// same statement is never edited by hand in two places.
const ENGINE_DISPLAY = { unreal: "UE", unity: "Unity" };
// A reference checked on several versions cites the newest validated one's line.
function citedLine(ref, versions) {
  if (!ref.lines) return ref.line;
  const newest = [...versions].filter(v => ref.lines[v]).sort((a, b) =>
    b.split(".").map(Number).reduce((d, n, i) => d || n - Number(a.split(".")[i] || 0), 0))[0];
  return ref.lines[newest];
}

function contractSummaryLines(label, versions, entries, engine = "unreal") {
  // Only engine facts depend on the validated engine version. A block made of
  // project policies carries no version label (it would claim a false scope).
  const header = entries.some(contract => contract.kind === "engine_fact")
    ? `엔진 계약 ${label} (${ENGINE_DISPLAY[engine] || engine} ${versions.join(", ")} 소스 기준, 다른 버전은 해당 엔진 소스로 재확인):`
    : `프로젝트 정책 ${label}:`;
  const lines = [header];
  for (const contract of entries) {
    const [first, ...rest] = contract.evidenceRefs;
    const evidence = first ? ` 근거: ${first.path}:${citedLine(first, versions)}${rest.length ? ` 외 ${rest.length}` : ""}` : "";
    const kind = contract.kind === "engine_fact" ? "엔진 사실" : "프로젝트 정책";
    lines.push(`- \x60${contract.id}\x60 (${kind}) ${contract.statement}${evidence}`);
  }
  return lines;
}

function syncContractSummaries(docRoot, data) {
  if (data?.schemaVersion !== 1 || !Number.isSafeInteger(data.revision) || !Array.isArray(data.contracts)
    || !Array.isArray(data.validatedEngineVersions) || typeof data.engine !== "string" || !ID.test(data.engine)) {
    throw new Error("Unsupported engine contract data");
  }
  const label = `${data.engine}_contracts@${data.revision}`;
  const targets = new Map();
  for (const contract of data.contracts) {
    if (typeof contract.id !== "string" || typeof contract.statement !== "string"
      || !Array.isArray(contract.guidanceRefs) || !Array.isArray(contract.evidenceRefs)) throw new Error("Invalid contract: " + contract.id);
    for (const ref of contract.guidanceRefs) {
      const match = /^([a-z][a-z0-9-]*\.md)#([a-z][a-z0-9-]*)$/.exec(ref);
      if (!match || !fs.existsSync(path.join(docRoot, match[1]))) continue; // RAG documents live elsewhere
      const key = match[1] + "#" + match[2];
      targets.set(key, [...(targets.get(key) || []), contract]);
    }
  }
  const updated = new Map(), placed = new Set();
  for (const file of new Set([...targets.keys()].map(key => key.split("#")[0]))) {
    const source = fs.readFileSync(path.join(docRoot, file), "utf8");
    const eol = source.includes("\r\n") ? "\r\n" : "\n";
    const out = [];
    let active = null, fence = null;
    for (const line of source.replace(/\r\n/g, "\n").split("\n")) {
      const fenceMatch = !active && /^ {0,3}(\x60{3,}|~{3,})/.exec(line);
      if (fenceMatch) {
        const token = fenceMatch[1];
        if (!fence) fence = { char: token[0], length: token.length };
        else if (token[0] === fence.char && token.length >= fence.length) fence = null;
      }
      const marker = !fence && CONTRACT_MARKER.exec(line);
      if (marker && !marker[1]) {
        const key = file + "#" + marker[2];
        if (active || placed.has(key) || !targets.has(key)) throw new Error("Unexpected contract summary block: " + key);
        active = key; placed.add(key);
        out.push(line, ...contractSummaryLines(label, data.validatedEngineVersions, targets.get(key), data.engine));
      } else if (marker) {
        if (active !== file + "#" + marker[2]) throw new Error("Invalid contract summary end: " + marker[2]);
        active = null; out.push(line);
      } else if (!active) out.push(line);
    }
    if (active) throw new Error("Unterminated contract summary block: " + active);
    updated.set(file, out.join(eol));
  }
  const missing = [...targets.keys()].filter(key => !placed.has(key));
  if (missing.length) throw new Error("Contract guidance block missing: " + missing.join(", "));
  return updated;
}

// Every config/<engine>_contracts.json is a contract set; zero files is valid.
function loadContractSets(configRoot) {
  const sets = fs.readdirSync(configRoot).filter(name => /^[a-z][a-z0-9-]*_contracts\.json$/.test(name)).sort()
    .map(name => ({ name, data: JSON.parse(fs.readFileSync(path.join(configRoot, name), "utf8")) }));
  const engines = new Set();
  for (const { name, data } of sets) {
    if (typeof data.engine !== "string" || name !== `${data.engine}_contracts.json` || engines.has(data.engine)) {
      throw new Error(`Contract file ${name} must declare its own engine exactly once`);
    }
    engines.add(data.engine);
  }
  return sets.map(entry => entry.data);
}

function main() {
  const pluginRoot = path.resolve(__dirname, "..");
  const output = path.join(pluginRoot, "src/generated-design-guidance.ts");
  const docRoot = path.resolve(pluginRoot, "../docs/model-guidance");
  const check = process.argv.includes("--check");
  const contractSets = loadContractSets(path.resolve(pluginRoot, "../config"));
  for (const contracts of contractSets) {
    for (const [file, text] of syncContractSummaries(docRoot, contracts)) {
      const target = path.join(docRoot, file);
      if (fs.readFileSync(target, "utf8") === text) continue;
      if (check) throw new Error(`Contract summary in ${file} is stale; run npm run guidance:build`);
      fs.writeFileSync(target, text, "utf8");
    }
  }
  const content = renderBundle(docRoot, contractSets);
  const patternOutput=path.join(docRoot,"generated-pattern-catalog.json");
  const patternText=JSON.stringify(renderPatternCatalog(docRoot),null,2)+"\n";
  if (check) {
    if(!fs.existsSync(patternOutput) || fs.readFileSync(patternOutput,"utf8").replace(/\r\n/g,"\n")!==patternText)
      throw new Error("Architecture pattern export is stale; run npm run guidance:build");
    if (!fs.existsSync(output) || fs.readFileSync(output, "utf8").replace(/\r\n/g, "\n") !== content) {
      throw new Error("Design guidance bundle is stale; run npm run guidance:build");
    }
    return;
  }
  fs.writeFileSync(output, content, "utf8");
  fs.writeFileSync(patternOutput,patternText,"utf8");
}

if (require.main === module) main();
module.exports = { canonical, renderPatternCatalog, loadContractSets, renderBundle, splitSections, syncContractSummaries };
