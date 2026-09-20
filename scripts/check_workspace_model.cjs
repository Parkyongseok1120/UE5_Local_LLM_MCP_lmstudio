#!/usr/bin/env node
"use strict";
// Explicit read-only real-model probe; not a CI substitute for engine execution.
const fs = require("node:fs"), path = require("node:path");
const { createRequire } = require("node:module");
const sdk = createRequire(path.resolve(__dirname, "../lmstudio-context-compactor-plugin/package.json"))("@lmstudio/sdk");
const { createRuntime } = require("../lmstudio-unity-mcp/src/server");
const { execFileSync } = require("node:child_process");
function verifyGitEvidence(project, records) {
  const record = records.find(r => r.name === "git_changed_files" && r.result.comparison === "range");
  if (!record) return { passed: false, reason: "No range evidence was returned" };
  const value = record.result;
  const git = (...args) => execFileSync("git", ["--literal-pathspecs", "--no-pager", "-c", "core.fsmonitor=false", "-C", project, ...args],
    { encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024, windowsHide: true });
  const head = git("rev-parse", "HEAD").trim(), base = git("rev-parse", "HEAD^1").trim();
  if (value.base !== base || value.head !== head) return { passed: false, reason: "Comparison differs from HEAD first parent to HEAD" };
  const prefix = git("rev-parse", "--show-prefix").trim();
  const fields = git("diff", "--no-ext-diff", "--no-textconv", "--find-renames=50%", "--name-status", "-z", base, head, "--", ".").split("\0");
  const rows = [];
  for (let i = 0; i < fields.length && fields[i];) {
    const code = fields[i++], first = fields[i++], name = /^[RC]/.test(code) ? fields[i++] : first;
    rows.push({ code: code[0], path: name.startsWith(prefix) ? name.slice(prefix.length) : null });
  }
  const codes = { added: "A", modified: "M", deleted: "D", renamed: "R", copied: "C", type_changed: "T" };
  const matched = value.items.every(item => rows.some(row => row.path === item.path && row.code === codes[item.status]));
  return { passed: matched && rows.length === value.total, base, head, total: rows.length,
    returnedItemsMatched: matched ? value.items.length : 0, scope: "Tool evidence only; final prose and semantic review require separate inspection" };
}
async function main() {
  const args = process.argv.slice(2);
  const value = flag => { const i = args.indexOf(flag); return i < 0 ? "" : args[i + 1]; };
  const project = value("--project"), output = value("--output"), identifier = value("--model");
  if (!path.isAbsolute(project) || !output || !identifier) throw Error("Supply --project <absolute Unity root> --model <loaded identifier> --output <report.json>");
  const client = new sdk.LMStudioClient(), model = await client.llm.model(identifier);
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: project, ALLOW_WRITE: "0", ALLOW_COMMANDS: "0" });
  const records = [], messages = [], started = Date.now();
  const tools = runtime.tools.filter(t => t.name.startsWith("git_") || t.name === "workspace_status").map(t => sdk.rawFunctionTool({
    name: t.name, description: t.description, parametersJsonSchema: t.inputSchema,
    implementation: async args => { const result = await runtime.call(t.name, args); records.push({ name: t.name, args, result });
      console.log(JSON.stringify({ tool: t.name, status: result.status, errorCode: result.errorCode })); return result; },
  }));
  const history = sdk.Chat.from([
    { role: "system", content: "Verify repository evidence using the supplied read-only tools. Never describe a worktree diff as a commit comparison. Answer concisely in Korean." },
    { role: "user", content: "연결된 프로젝트에서 HEAD 바로 이전 커밋과 HEAD 사이 변경 파일을 확인해. 변경 파일 5개까지만 이름과 상태를 정리하고, 비교한 실제 커밋 ID를 알려줘. 작업 트리 변경은 이번 검토 대상이 아니야." },
  ]);
  let error;
  try {
    await model.act(history, tools, { temperature: 0, maxTokens: 3000, signal: AbortSignal.timeout(240000),
      onMessage: message => { if (message.getRole() === "assistant") messages.push(message.getText()
        .split("__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__").at(-1)); },
    });
  } catch (e) { error = String(e); }
  let oracle;
  try { oracle = verifyGitEvidence(project, records); } catch (e) { oracle = { passed: false, error: String(e) }; }
  const report = { identifier, elapsedMs: Date.now() - started, records, messages, error,
    completed: !error, oracle };
  fs.writeFileSync(output, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ completed: !error, toolCalls: records.length, elapsedMs: report.elapsedMs, error, answer: messages.at(-1) }));
  if (error || !oracle.passed) process.exitCode = 1;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
