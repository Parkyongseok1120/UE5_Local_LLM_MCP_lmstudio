"use strict";
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawnSync } = require("child_process");

const buildBat = "C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Build\\BatchFiles\\Build.bat";
const project = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\O_Mock.uproject";
const objDir = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\Intermediate\\Build\\Win64\\x64\\UnrealEditor\\Development\\O_Mock";
const obj = path.join(objDir, "GomokuStage4Behavior.spec.cpp.obj");
const ubtLog = path.join(os.homedir(), "AppData", "Local", "UnrealBuildTool", "Log.txt");
const outLog = path.join(__dirname, "omock_stage4_build.log");
const debugLog = path.join(__dirname, "..", "debug-821b0f.log");

function log(message, data) {
  const e = {
    sessionId: "821b0f",
    runId: "build-force",
    hypothesisId: "H-build",
    location: "run_omock_build.js",
    message,
    data,
    timestamp: Date.now(),
  };
  fs.appendFileSync(debugLog, JSON.stringify(e) + "\n");
  console.log(message, JSON.stringify(data));
}

try {
  fs.unlinkSync(obj);
  log("deleted_obj", { obj: true });
} catch (e) {
  log("deleted_obj", { obj: false, err: e.message });
}
try {
  fs.unlinkSync(ubtLog);
  log("cleared_ubt_log", { ok: true });
} catch (e) {
  log("cleared_ubt_log", { ok: false, err: e.message });
}

const cmdline =
  `"${buildBat}" O_MockEditor Win64 Development -Project=${project} -WaitMutex -FromMsBuild`;
log("spawn_start", { cmdline });
const result = spawnSync(cmdline, {
  cwd: "C:\\Users\\sster\\Documents\\Git\\O-Mock",
  encoding: "utf8",
  shell: true,
  maxBuffer: 40 * 1024 * 1024,
});
let combined = `${result.stdout || ""}\n${result.stderr || ""}`;
let ubtText = "";
try {
  ubtText = fs.readFileSync(ubtLog, "utf8");
} catch {
  /* ignore */
}
if (ubtText) combined += `\n----- UBT Log.txt -----\n${ubtText.slice(-150000)}`;
fs.writeFileSync(outLog, combined, "utf8");
const errors = [];
for (const line of combined.split(/\r?\n/)) {
  if (
    /error\s+[A-Z]?\d+|:\s*Error:|error C\d+|OtherCompilationError/i.test(line) &&
    !/0 Error\(s\)|0 error/i.test(line)
  ) {
    errors.push(line.trim().slice(0, 400));
  }
}
const succeeded = /Result:\s*Succeeded/i.test(combined);
const payload = {
  status: result.status,
  error: result.error ? String(result.error) : null,
  succeeded,
  outLen: combined.length,
  errorCount: [...new Set(errors)].length,
  errors: [...new Set(errors)].slice(0, 15),
  ubtHead: ubtText.slice(0, 120),
};
log("build_done", payload);
console.log(JSON.stringify(payload, null, 2));
process.exit(succeeded && result.status === 0 ? 0 : 1);
