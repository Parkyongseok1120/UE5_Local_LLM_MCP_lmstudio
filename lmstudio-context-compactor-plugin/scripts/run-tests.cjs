"use strict";
const fs = require("node:fs"), path = require("node:path"), { spawnSync } = require("node:child_process");
// All product tests in this directory participate; no second hand-written list.
const root = path.resolve(__dirname, "..");
const tests = fs.readdirSync(path.join(root, "test")).filter(name => /\.test\.(?:cjs|js)$/.test(name))
  .sort().map(name => path.join("test", name));
if (!tests.length) throw new Error("No compactor tests found");
if (process.argv.includes("--list")) { process.stdout.write(JSON.stringify(tests)); process.exit(0); }
const result = spawnSync(process.execPath, ["--test", ...tests], { cwd: root, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
