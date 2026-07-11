"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { discoverProjects } = require("../src/unreal-detect");

test("discoverProjects excludes local_holdout_fixtures under repo data", async () => {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const configPath = path.join(os.tmpdir(), `unreal-detect-${process.pid}.json`);
  fs.writeFileSync(
    configPath,
    JSON.stringify({ projectSearchRoots: [repoRoot] }, null, 2),
    "utf8"
  );
  try {
    const result = await discoverProjects(repoRoot, configPath, { maxDepth: 6 });
    const fixtureHits = result.projects.filter((project) =>
      String(project.projectPath || "").replace(/\\/g, "/").includes("local_holdout_fixtures")
    );
    assert.strictEqual(fixtureHits.length, 0, `fixture projects found: ${fixtureHits.map((p) => p.projectPath).join(", ")}`);
  } finally {
    try {
      fs.unlinkSync(configPath);
    } catch {
      // ignore
    }
  }
});
