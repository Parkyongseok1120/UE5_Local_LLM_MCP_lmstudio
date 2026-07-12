"use strict";

const assert = require("assert");
const test = require("node:test");
const { projectSwitchGuidance } = require("../src/tool-exposure");

test("projectSwitchGuidance points stable profile to unreal-rag canonical tool", () => {
  const guidance = projectSwitchGuidance([
    "read_file",
    "write_file",
    "build_unreal_project",
  ]);
  assert.strictEqual(guidance.requiredNextTool.server, "unreal-rag");
  assert.strictEqual(guidance.requiredNextTool.name, "unreal_set_active_project");
  assert.deepStrictEqual(guidance.suggestedToolCalls, [{ tool: "unreal_set_active_project", args: {} }]);
});

test("projectSwitchGuidance uses set_active_project in extended profile", () => {
  const previous = process.env.MCP_EXTENDED_TOOLS;
  process.env.MCP_EXTENDED_TOOLS = "1";
  try {
    const guidance = projectSwitchGuidance([
      "read_file",
      "set_active_project",
      "build_unreal_project",
    ]);
    assert.strictEqual(guidance.requiredNextTool, undefined);
    assert.deepStrictEqual(guidance.suggestedToolCalls, [{ tool: "set_active_project", args: {} }]);
  } finally {
    if (previous === undefined) {
      delete process.env.MCP_EXTENDED_TOOLS;
    } else {
      process.env.MCP_EXTENDED_TOOLS = previous;
    }
  }
});
