"use strict";

const fs = require("fs");
const path = require("path");

const convPath = process.argv[2]
  || path.join(process.env.USERPROFILE || "", ".lmstudio", "conversations", "1785699306693.conversation.json");
const eventsPath = process.argv[3]
  || path.join(
    process.env.USERPROFILE || "",
    ".lmstudio",
    "unreal-context-compactor",
    "sessions",
    "d3690b48083daba70b8d57d1c83d5674",
    "events.jsonl",
  );

function contentText(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map(contentText).join("\n");
  if (c && typeof c === "object") {
    if (typeof c.text === "string") return c.text;
    if (c.content !== undefined) return contentText(c.content);
  }
  return "";
}

const j = JSON.parse(fs.readFileSync(convPath, "utf8"));
const users = [];
const toolNames = [];

function scan(obj) {
  if (!obj || typeof obj !== "object") return;
  if (typeof obj.name === "string" && (obj.arguments !== undefined || obj.type === "toolCall" || obj.type === "toolCallRequest")) {
    toolNames.push(obj.name);
  }
  if (Array.isArray(obj)) {
    obj.forEach(scan);
    return;
  }
  for (const key of Object.keys(obj)) scan(obj[key]);
}

for (const top of j.messages || []) {
  const sel = top.currentlySelected ?? 0;
  const ver = (top.versions || [])[sel] || (top.versions || [])[0] || top;
  if (ver.role === "user") {
    users.push(contentText(ver.content || ver).slice(0, 240));
  }
}
scan(j.messages);

const counts = {};
for (const name of toolNames) counts[name] = (counts[name] || 0) + 1;

const events = fs.readFileSync(eventsPath, "utf8").trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
const comps = events.filter((e) => e.type === "compaction_decision");
const tinyPosts = comps.filter((e) => Number(e.postInputTokens) > 0 && Number(e.postInputTokens) < 2000);
const goalChanges = comps.filter((e) => e.goalChangeCompact === true);
const zeroTail = comps.filter((e) => e.zeroRetainedTurns === true || e.retainedTurns === 0);
const withMeta = comps.filter((e) => e.answeringMeta === true);

const out = {
  conversationName: j.name,
  plugins: j.plugins,
  lastUsedModelId: j.lastUsedModel?.identifier || j.lastUsedModel?.indexedModelIdentifier || null,
  userCount: users.length,
  users,
  toolCounts: counts,
  listDirectoryMentions: (JSON.stringify(j).match(/list_directory/g) || []).length,
  compactionCount: comps.length,
  tinyPostCompactionCount: tinyPosts.length,
  sampleTinyPosts: tinyPosts.slice(-5).map((e) => ({
    at: e.at,
    postInputTokens: e.postInputTokens,
    retainedTurns: e.retainedTurns,
    goalChangeCompact: e.goalChangeCompact,
    zeroRetainedTurns: e.zeroRetainedTurns,
    answeringMeta: e.answeringMeta,
    currentTurnCap: e.currentTurnCap,
  })),
  goalChangeCount: goalChanges.length,
  zeroTailCount: zeroTail.length,
  answeringMetaCount: withMeta.length,
  lastCompaction: comps[comps.length - 1] || null,
};

const logPath = path.join(__dirname, "..", "debug-49b048.log");
fs.appendFileSync(
  logPath,
  `${JSON.stringify({
    sessionId: "49b048",
    runId: "live-session-audit",
    hypothesisId: "H10",
    location: "debug_analyze_conversation.js",
    message: "audited latest LM Studio conversation + compactor events",
    data: out,
    timestamp: Date.now(),
  })}\n`,
);

console.log(JSON.stringify(out, null, 2));
