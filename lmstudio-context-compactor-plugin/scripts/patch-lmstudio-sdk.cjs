"use strict";
// Compatibility fix for @lmstudio/sdk 1.5.0 internalAct: handlePredictionEnd
// resolves predictionPromise without marking its channel finished. Aborting
// after returned tool results then sends cancel to that already closed channel.
// Keep the upstream implementation intact except for its missing lifecycle bit.
const fs = require("node:fs"), path = require("node:path");
const root = path.join(__dirname, "../node_modules/@lmstudio/sdk");
const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
if (version !== "1.5.0") throw new Error(`SDK lifecycle patch must be reviewed for version ${version}`);
const before = "handlePredictionEnd: endPacket => {\n                const predictionResult = makePredictionResult({";
const after = "handlePredictionEnd: endPacket => {\n                finished = true; // context-compactor: completed prediction channel\n                const predictionResult = makePredictionResult({";
for (const name of ["index.cjs", "index.mjs"]) {
  const file = path.join(root, "dist", name), source = fs.readFileSync(file, "utf8");
  if (source.includes(after)) continue;
  if (source.split(before).length !== 2) throw new Error(`SDK lifecycle patch target changed in ${name}`);
  fs.writeFileSync(file, source.replace(before, after));
}
