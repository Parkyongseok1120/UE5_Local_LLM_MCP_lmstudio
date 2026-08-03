"use strict";
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawnSync } = require("child_process");

const buildBat = "C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Build\\BatchFiles\\Build.bat";
const project = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\O_Mock.uproject";
const obj = "C:\\Users\\sster\\Documents\\Git\\O-Mock\\Intermediate\\Build\\Win64\\x64\\UnrealEditor\\Development\\O_Mock\\GomokuStage4Behavior.spec.cpp.obj";
try { fs.unlinkSync(obj); console.error("deleted obj", obj); } catch (e) { console.error("obj delete", e.message); }

const args = [
  "O_MockEditor",
  "Win64",
  "Development",
  `-Project=${project}`,
  "-WaitMutex",
  "-FromMsBuild",
];
const result = spawnSync(buildBat, args, {
  cwd: "C:\\Users\\sster\\Documents\\Git\\O-Mock",
  encoding: "utf8",
  shell: false,
  windowsVerbatimArguments: false,
  maxBuffer: 40 * 1024 * 1024,
});
let combined = `${result.stdout || ""}\n${result.stderr || ""}`;
const ubtLog = path.join(os.homedir(), "AppData", "Local", "UnrealBuildTool", "Log.txt");
let ubtText = "";
try { ubtText = fs.readFileSync(ubtLog, "utf8"); } catch { /* ignore */ }
if (ubtText) combined += `\n----- UBT Log.txt -----\n${ubtText.slice(-120000)}`;
const outLog = path.join(__dirname, "omock_stage4_build.log");
fs.writeFileSync(outLog, combined, "utf8");
const errors = [];
for (const line of combined.split(/\r?\n/)) {
  if (/error\s+[A-Z]?\d+|:\s*Error:|error C\d+|OtherCompilationError/i.test(line)
    && !/0 Error\(s\)|0 error/i.test(line)) {
    errors.push(line.trim().slice(0, 300));
  }
}
const succeeded = /Result:\s*Succeeded/i.test(combined) || /Building .* succeeded/i.test(combined);
console.log(JSON.stringify({
  status: result.status,
  succeeded,
  outLen: combined.length,
  errorCount: [...new Set(errors)].length,
  errors: [...new Set(errors)].slice(0, 12),
}, null, 2));
process.exit(result.status === 0 && succeeded ? 0 : 1);
