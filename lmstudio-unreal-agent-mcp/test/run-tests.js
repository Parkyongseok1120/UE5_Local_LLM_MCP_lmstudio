"use strict";

const fs = require("fs");
const os = require("os");
const { spawnSync } = require("child_process");
const { join } = require("path");

const testDir = __dirname;
const files = fs.readdirSync(testDir)
  .filter((name) => name.endsWith(".test.js"))
  .map((name) => join(testDir, name));

if (files.length === 0) {
  console.error("No *.test.js files found in", testDir);
  process.exit(1);
}

const isolatedRoot = fs.mkdtempSync(join(os.tmpdir(), "unreal-agent-tests-"));
const env = {
  ...process.env,
  AGENT_STATE_ROOT: join(isolatedRoot, "state", "unreal-agent"),
  SHARED_UNREAL_CONFIG: join(isolatedRoot, "config", "unreal-workspace.json"),
};
let status = 1;
try {
  const result = spawnSync(process.execPath, ["--test", ...files], { stdio: "inherit", env });
  status = result.status ?? 1;
} finally {
  fs.rmSync(isolatedRoot, { recursive: true, force: true });
}
process.exit(status);
