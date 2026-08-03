"use strict";
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const editor =
  "C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe";
const project = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\O_Mock.uproject";
const filter = process.env.AUTO_TEST_FILTER || "Gomoku.Stage4.";
const tag = process.env.AUTO_TEST_TAG || filter.replace(/[^A-Za-z0-9_.-]+/g, "_");
const outLog = path.join(__dirname, `omock_automation_${tag}.out.log`);
const reportPath = path.join(__dirname, `omock_automation_${tag}.report.json`);
const debugLog = path.join(__dirname, "..", "debug-821b0f.log");

const cmdline =
  `"${editor}" "${project}" -unattended -nop4 -nosound -NullRHI -nosplash ` +
  `-ExecCmds="Automation RunTests ${filter}; Quit" ` +
  `-log`;

function dlog(message, data) {
  fs.appendFileSync(
    debugLog,
    JSON.stringify({
      sessionId: "821b0f",
      runId: "automation-local-ai",
      hypothesisId: "H-auto",
      location: "run_omock_automation.js",
      message,
      data,
      timestamp: Date.now(),
    }) + "\n",
  );
}

dlog("spawn_start", { cmdline: cmdline.slice(0, 200) });
const result = spawnSync(cmdline, {
  cwd: "C:\\Users\\sster\\Documents\\Git\\O-Mock",
  encoding: "utf8",
  shell: true,
  maxBuffer: 80 * 1024 * 1024,
  timeout: 600000,
});
const combined = `${result.stdout || ""}\n${result.stderr || ""}`;
fs.writeFileSync(outLog, combined, "utf8");

// Also scrape Saved/Logs if present
const savedLogDir = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\Saved\\Logs";
let savedTail = "";
try {
  const logs = fs
    .readdirSync(savedLogDir)
    .filter((f) => f.endsWith(".log"))
    .map((f) => ({ f, t: fs.statSync(path.join(savedLogDir, f)).mtimeMs }))
    .sort((a, b) => b.t - a.t);
  if (logs[0]) {
    savedTail = fs.readFileSync(path.join(savedLogDir, logs[0].f), "utf8").slice(-200000);
    fs.appendFileSync(outLog, `\n----- Saved/${logs[0].f} -----\n` + savedTail);
  }
} catch (e) {
  /* ignore */
}

const text = combined + "\n" + savedTail;
const prefix = filter.replace(/\.$/, "");
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const pathRe = new RegExp(
  `Result=\\{(Success|Fail)\\}[^\\n]*Path=\\{(${escapeRe(prefix)}[^}]*)\\}`,
  "g",
);
const byName = {};
for (const m of text.matchAll(pathRe)) {
  byName[m[2]] = m[1];
}
const list = Object.entries(byName).map(([name, result]) => ({ name, result }));
const pass = list.filter((x) => x.result === "Success").length;
const fail = list.filter((x) => x.result !== "Success").length;
const expect = Number(process.env.AUTO_TEST_EXPECT || "0");

const report = {
  filter,
  status: result.status,
  error: result.error ? String(result.error) : null,
  found: list.length,
  pass,
  fail,
  expect,
  list,
  sampleLines: text
    .split(/\r?\n/)
    .filter((l) => l.includes(prefix) || /AutomationController|Result=\{/.test(l))
    .slice(-80),
};
fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
dlog("automation_done", {
  filter,
  status: report.status,
  found: report.found,
  pass: report.pass,
  fail: report.fail,
  names: list.map((x) => x.name + "=" + x.result),
});
console.log(JSON.stringify(report, null, 2));
const ok =
  fail === 0 &&
  pass > 0 &&
  (expect <= 0 || (pass === expect && list.length === expect));
process.exit(ok ? 0 : 2);
