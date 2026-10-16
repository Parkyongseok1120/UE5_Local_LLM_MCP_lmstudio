"use strict";

// The compactor's observation lists and read-result defaults are generated
// from config/stable_tool_manifest.json; a stale generated file fails here.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { renderContract } = require("../scripts/build-tool-contract.cjs");
const { UNREAL_OBSERVATION_TOOLS, UNREAL_RAG_OBSERVATION_TOOLS } = require("../dist/tool-capability-registry");

const root = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.resolve(root, "../config/stable_tool_manifest.json"), "utf8").replace(/^﻿/, ""));

test("generated tool contract matches the manifest SSOT", () => {
  const generated = fs.readFileSync(path.join(root, "src/generated-tool-contract.ts"), "utf8").replace(/\r\n/g, "\n");
  assert.equal(generated, renderContract(manifest));
});

test("observation tools are exactly the manifest's read and ephemeral tools", () => {
  const observed = effects => Object.keys(effects).filter(name => ["read", "ephemeral"].includes(effects[name])).sort();
  assert.deepEqual([...UNREAL_OBSERVATION_TOOLS].sort(), observed(manifest.agentToolEffects));
  assert.deepEqual([...UNREAL_RAG_OBSERVATION_TOOLS].sort(), observed(manifest.ragToolEffects));
  assert.equal(UNREAL_OBSERVATION_TOOLS.has("write_file"), false);
  assert.equal(UNREAL_RAG_OBSERVATION_TOOLS.has("unreal_rag_refresh"), false);
});
