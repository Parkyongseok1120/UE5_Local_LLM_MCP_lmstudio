"use strict";

const assert = require("assert");
const test = require("node:test");
const { resolveBuildTarget } = require("../src/unreal-build-plan");

test("Editor alias resolves to the selected project's canonical editor target", () => {
  assert.strictEqual(resolveBuildTarget("Editor", {
    projectName: "PortableGame",
    preferredTarget: "PortableGameEditor",
    allTargets: ["PortableGame", "PortableGameEditor"],
  }), "PortableGameEditor");
});

test("Editor alias uses the only discovered custom editor target", () => {
  assert.strictEqual(resolveBuildTarget("editor", {
    projectName: "PortableGame",
    preferredTarget: "PortableGame",
    allTargets: ["PortableGame", "StudioToolsEditor"],
  }), "StudioToolsEditor");
});

test("explicit build targets remain unchanged", () => {
  assert.strictEqual(resolveBuildTarget("ServerTarget", {
    projectName: "PortableGame",
    preferredTarget: "PortableGameEditor",
    allTargets: ["PortableGameEditor", "ServerTarget"],
  }), "ServerTarget");
});
