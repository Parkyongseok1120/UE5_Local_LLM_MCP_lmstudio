"use strict";
/** Clean rebuild O_MockEditor then exit with build status. */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { spawnSync } = require("child_process");

const buildBat = "C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Build\\BatchFiles\\Build.bat";
const project = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\O_Mock.uproject";
const ubtLog = path.join(os.homedir(), "AppData", "Local", "UnrealBuildTool", "Log.txt");
const outLog = path.join(__dirname, "omock_stage7_final_clean_build.log");
const debugLog = path.join(__dirname, "..", "debug-821b0f.log");

function log(message, data) {
  fs.appendFileSync(
    debugLog,
    JSON.stringify({
      sessionId: "821b0f",
      runId: "clean-build",
      hypothesisId: "H-build",
      location: "run_omock_clean_build.js",
      message,
      data,
      timestamp: Date.now(),
    }) + "\n",
  );
  console.log(message, JSON.stringify(data));
}

try {
  fs.unlinkSync(ubtLog);
} catch {
  /* ignore */
}

const cmdline =
  `"${buildBat}" O_MockEditor Win64 Development -Project=${project} -WaitMutex -FromMsBuild -Clean`;
log("spawn_start", { cmdline });
const result = spawnSync(cmdline, {
  cwd: "C:\\Users\\sster\\Documents\\Git\\O-Mock",
  encoding: "utf8",
  shell: true,
  maxBuffer: 80 * 1024 * 1024,
  timeout: 900000,
});
let combined = `${result.stdout || ""}\n${result.stderr || ""}`;
try {
  combined += `\n----- UBT -----\n${fs.readFileSync(ubtLog, "utf8").slice(-200000)}`;
} catch {
  /* ignore */
}
fs.writeFileSync(outLog, combined, "utf8");
const succeeded = /Result:\s*Succeeded/i.test(combined);
const payload = {
  status: result.status,
  succeeded,
  outLen: combined.length,
};
log("clean_build_done", payload);
console.log(JSON.stringify(payload, null, 2));
process.exit(succeeded && result.status === 0 ? 0 : 1);
