#!/usr/bin/env node
"use strict";
// Development harness only; never imported into the MCP runtime.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");
const repo = path.resolve(__dirname, "..");
const editor = process.argv[2];
if (!editor || !fs.existsSync(editor)) throw new Error("Usage: node scripts/test_unity_bridge.js /absolute/path/to/Unity [work-parent]");
const editorVersion = process.env.UNITY_TEST_VERSION;
if (!editorVersion || !/^\d+\.\d+\.\d+[abfp]\d+$/.test(editorVersion)) throw new Error("Set UNITY_TEST_VERSION to the installed Editor version under test");
const root = fs.mkdtempSync(path.join(process.argv[3] || os.tmpdir(), "unity-bridge-test-"));
for (const directory of ["Assets/Smoke", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(root, directory), { recursive: true });
fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), `m_EditorVersion: ${editorVersion}\n`);
const dependencies = { "com.evidencefirst.unity-bridge": "file:" + path.join(repo, "unity-editor-bridge"), "com.unity.nuget.newtonsoft-json": "3.2.1", "com.unity.modules.jsonserialize": "1.0.0" };
if (process.env.UNITY_TEST_DEBUG === "1") Object.assign(dependencies, { "com.unity.inputsystem": process.env.UNITY_TEST_INPUT_VERSION || "1.19.0", "com.unity.ugui": process.env.UNITY_TEST_UI_VERSION || "2.0.0", "com.unity.modules.physics": "1.0.0", "com.unity.modules.ui": "1.0.0", "com.unity.modules.imgui": "1.0.0" });
if (process.env.UNITY_TEST_FRAMEWORK_VERSION) dependencies["com.unity.test-framework"] = process.env.UNITY_TEST_FRAMEWORK_VERSION;
fs.writeFileSync(path.join(root, "Packages/manifest.json"), JSON.stringify({ dependencies }, null, 2));
for (const entry of fs.readdirSync(path.join(repo, "unity-editor-bridge/Tests~"), { withFileTypes: true })) {
  if (entry.name === "Framework" && !process.env.UNITY_TEST_FRAMEWORK_VERSION) continue;
  if (entry.isDirectory() && process.env.UNITY_TEST_DEBUG !== "1") continue;
  fs.cpSync(path.join(repo, "unity-editor-bridge/Tests~", entry.name), path.join(root, "Assets/Smoke", entry.name), { recursive: true });
}
console.log(JSON.stringify({ project: root, log: path.join(root, "editor.log") }));
const child = spawn(editor, ["-batchmode", "-nographics", "-projectPath", root, "-executeMethod", "EvidenceFirst.Tests.BridgeSmoke.Run", "-logFile", path.join(root, "editor.log")], { stdio: "ignore", detached: true });
console.log(JSON.stringify({ editorPid: child.pid }));
const started = Date.now();
const timer = setInterval(() => {
  const report = path.join(root, "smoke-result.json");
  if (fs.existsSync(report)) { clearInterval(timer); console.log(fs.readFileSync(report, "utf8")); child.unref(); process.exitCode = JSON.parse(fs.readFileSync(report)).status === "passed" ? 0 : 1; }
  else if (Date.now() - started > 300000) { clearInterval(timer); console.error("Timed out: inspect editor.log; test Editor remains identifiable by PID above"); child.kill("SIGTERM"); process.exitCode = 1; }
}, 1000);
child.on("exit", code => { clearInterval(timer); if (!fs.existsSync(path.join(root, "smoke-result.json"))) { console.error(`Editor exited before smoke result: ${code}`); process.exitCode = 1; } });
