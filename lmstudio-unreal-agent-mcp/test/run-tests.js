"use strict";

const { readdirSync } = require("fs");
const { spawnSync } = require("child_process");
const { join } = require("path");

const testDir = __dirname;
const files = readdirSync(testDir)
  .filter((name) => name.endsWith(".test.js"))
  .map((name) => join(testDir, name));

if (files.length === 0) {
  console.error("No *.test.js files found in", testDir);
  process.exit(1);
}

const result = spawnSync(process.execPath, ["--test", ...files], { stdio: "inherit" });
process.exit(result.status ?? 1);
