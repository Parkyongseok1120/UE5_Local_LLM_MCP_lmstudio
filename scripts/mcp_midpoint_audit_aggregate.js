"use strict";
const fs = require("node:fs");
const path = require("node:path");

const dir = path.join(__dirname);
const files = fs.readdirSync(dir).filter((f) => /^local_ai_stage.*\.out\.log$/.test(f));
const by = {};
const sourceScope = String(process.env.MCP_AUDIT_SOURCE_SCOPE || "Source/")
  .replace(/\\/g, "/")
  .replace(/^\/+|\/+$/g, "");

function readLog(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) {
    return buf.toString("utf16le");
  }
  if (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff) {
    return buf.slice(2).swap16().toString("utf16le");
  }
  return buf.toString("utf8");
}

for (const f of files) {
  const m = f.match(/local_ai_stage(\d+)/);
  const stage = m ? m[1] : "?";
  const text = readLog(path.join(dir, f));
  const mutMatches = [...text.matchAll(/"mutationCount"\s*:\s*(\d+)/g)].map((x) => Number(x[1]));
  const mutOkTrue = (text.match(/mutOk=true/g) || []).length;
  const mutOkFalse = (text.match(/mutOk=false/g) || []).length;
  const hasUserFalse = (text.match(/hasUser=false/g) || []).length;
  const hasUserTrue = (text.match(/hasUser=true/g) || []).length;
  const lm400 = (text.match(/invalid_request|maxPredictedTokensReached|status.?code.?400|\bLM.?400\b/gi) || [])
    .length;
  const bounded = (text.match(/BOUNDED_PATCH|evidence.?stagnation|tool.?call.?deadlock|stagnation/gi) || [])
    .length;
  const projects = [...text.matchAll(/"activeProject"\s*:\s*"([^"]+)"/g)].map((x) => x[1]);
  const mutPaths = [
    ...text.matchAll(/tool (write_file|replace_in_file|delete_file) (\S+) .*mutOk=true/g),
  ].map((x) => x[2]);
  const outside = mutPaths.filter((value) => {
    const normalized = String(value || "").replace(/\\/g, "/").replace(/^\/+/, "");
    return !(
      normalized === sourceScope
      || normalized.startsWith(`${sourceScope}/`)
      || normalized.includes(`/${sourceScope}/`)
    );
  });
  by[stage] = by[stage] || [];
  by[stage].push({
    file: f,
    mutationCount: mutMatches.length ? mutMatches[mutMatches.length - 1] : null,
    mutOkTrue,
    mutOkFalse,
    hasUserFalse,
    hasUserTrue,
    lm400,
    bounded,
    activeProject: projects[0] || null,
    mutPaths: [...new Set(mutPaths)],
    sourceScope,
    outsideSourceScope: outside,
  });
}

const out = path.join(__dirname, "mcp_midpoint_audit_aggregate.json");
fs.writeFileSync(out, JSON.stringify(by, null, 2));
console.log(out);
for (const [stage, rows] of Object.entries(by).sort()) {
  const sumMut = rows.reduce((a, r) => a + (r.mutationCount || 0), 0);
  const sumTrue = rows.reduce((a, r) => a + r.mutOkTrue, 0);
  console.log(
    `Stage ${stage}: sessions=${rows.length} mutationCountΣ=${sumMut} mutOkTrueΣ=${sumTrue} hasUserFalseΣ=${rows.reduce((a, r) => a + r.hasUserFalse, 0)} lm400Σ=${rows.reduce((a, r) => a + r.lm400, 0)} boundedΣ=${rows.reduce((a, r) => a + r.bounded, 0)}`,
  );
}
