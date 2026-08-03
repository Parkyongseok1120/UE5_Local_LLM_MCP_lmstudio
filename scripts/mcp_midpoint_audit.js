"use strict";
const fs = require("fs");
const path = require("path");

const scriptsDir = __dirname;
const debugLog = path.join(__dirname, "..", "debug-821b0f.log");
const logs = fs
  .readdirSync(scriptsDir)
  .filter((f) => /^local_ai_stage.*\.out\.log$/i.test(f))
  .sort();

function parseLog(file) {
  const text = fs.readFileSync(path.join(scriptsDir, file), "utf8");
  const tools = [];
  for (const m of text.matchAll(
    /tool\s+(read_file(?:_range)?|write_file|replace_in_file|list_directory|search_files|get_workspace_info|get_active_project)\s+(\S+)?\s*(\d+)?ms\s+mutOk=(true|false)/g,
  )) {
    tools.push({
      name: m[1],
      path: m[2] || "",
      ms: Number(m[3] || 0),
      mutOk: m[4] === "true",
    });
  }
  const mutMatch = [...text.matchAll(/"mutationCount"\s*:\s*(\d+)/g)];
  const mutationCount = mutMatch.length
    ? Number(mutMatch[mutMatch.length - 1][1])
    : 0;
  const mutations = [];
  for (const m of text.matchAll(
    /\{\s*"name":\s*"(write_file|replace_in_file)",\s*"path":\s*"([^"]+)"\s*\}/g,
  )) {
    mutations.push({ name: m[1], path: m[2] });
  }
  const activeProjects = [
    ...text.matchAll(/"activeProject"\s*:\s*"([^"]+)"/g),
  ].map((m) => m[1]);
  const workspaceHits = [
    ...text.matchAll(/Documents\\\\Git\\\\([^\\"\s]+)/g),
  ].map((m) => m[1]);
  const errors = {
    http400: (text.match(/\b400\b|No user query found|applyPromptTemplate/gi) || [])
      .length,
    boundedPatch: (text.match(/BOUNDED_PATCH_REQUIRED|patch is too large/gi) || [])
      .length,
    stagnation: (text.match(/stagnation|likelyDeadlock|evidence stagnation/gi) || [])
      .length,
    generateError: (text.match(/generate error|maxPredictedTokensReached/gi) || [])
      .length,
    trim: (text.match(/\[trim\] reason=user_preserving_tail/g) || []).length,
    trimNoUser: (text.match(/hasUser=false/g) || []).length,
  };
  const mutOkTrue = tools.filter((t) => t.mutOk).length;
  const mutOkFalseWrites = tools.filter(
    (t) =>
      (t.name === "write_file" || t.name === "replace_in_file") && !t.mutOk,
  ).length;
  const reads = tools.filter((t) => t.name.startsWith("read_file")).length;
  const writes = tools.filter(
    (t) => t.name === "write_file" || t.name === "replace_in_file",
  ).length;
  return {
    file,
    mutationCount,
    mutations,
    toolCounts: {
      reads,
      writes,
      mutOkTrue,
      mutOkFalseWrites,
      totalToolLines: tools.length,
    },
    activeProjects: [...new Set(activeProjects)],
    workspaceHits: [...new Set(workspaceHits)],
    errors,
    lastPreview: (() => {
      const m = text.match(/"finalPreview":\s*"([^"]{0,180})/);
      return m ? m[1] : "";
    })(),
  };
}

const rows = logs.map(parseLog);
const stageBuckets = {};
for (const r of rows) {
  const sm = r.file.match(/local_ai_stage(\d+|4_runtime)/i);
  const key = sm ? sm[1] : "other";
  if (!stageBuckets[key]) stageBuckets[key] = [];
  stageBuckets[key].push(r);
}

const summary = {
  logCount: rows.length,
  stageBuckets: Object.fromEntries(
    Object.entries(stageBuckets).map(([k, arr]) => [
      k,
      {
        sessions: arr.length,
        totalMutations: arr.reduce((s, x) => s + x.mutationCount, 0),
        zeroMutationSessions: arr
          .filter((x) => x.mutationCount === 0)
          .map((x) => x.file),
        mutatedPaths: [
          ...new Set(arr.flatMap((x) => x.mutations.map((m) => m.path))),
        ],
        activeProjects: [
          ...new Set(arr.flatMap((x) => x.activeProjects)),
        ],
        errorTotals: arr.reduce(
          (acc, x) => {
            for (const [ek, ev] of Object.entries(x.errors)) {
              acc[ek] = (acc[ek] || 0) + ev;
            }
            return acc;
          },
          {},
        ),
      },
    ]),
  ),
  rows,
};

fs.writeFileSync(
  path.join(scriptsDir, "mcp_midpoint_audit.json"),
  JSON.stringify(summary, null, 2),
);
fs.appendFileSync(
  debugLog,
  JSON.stringify({
    sessionId: "821b0f",
    runId: "mcp-midpoint-audit",
    hypothesisId: "H-mcp",
    location: "mcp_midpoint_audit.js",
    message: "parsed local AI MCP session logs",
    data: {
      logCount: summary.logCount,
      stages: Object.keys(summary.stageBuckets),
      zeroMut: Object.fromEntries(
        Object.entries(summary.stageBuckets).map(([k, v]) => [
          k,
          v.zeroMutationSessions,
        ]),
      ),
      errorTotals: Object.fromEntries(
        Object.entries(summary.stageBuckets).map(([k, v]) => [k, v.errorTotals]),
      ),
    },
    timestamp: Date.now(),
  }) + "\n",
);
console.log(JSON.stringify(summary.stageBuckets, null, 2));
