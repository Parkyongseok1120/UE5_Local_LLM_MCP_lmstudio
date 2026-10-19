"use strict";
const fs = require("node:fs"), path = require("node:path");
const { tools, toolEffects } = require("../../lmstudio-unity-mcp/src/catalog");
const { workspaceToolDefinitions } = require("../../shared-tool-core/workspace");
function render() {
  const names = [], actions = {}, effects = new Set(["read", "mutation", "long_running"]);
  if (Object.keys(toolEffects).length !== tools.length) throw Error("Unity effect coverage drift");
  for (const tool of tools) {
    const effect = toolEffects[tool.name];
    if (typeof effect === "string") {
      if (!effects.has(effect)) throw Error(`Invalid effect: ${tool.name}`);
      if (effect === "read") names.push(tool.name);
    } else {
      const declared = tool.inputSchema.properties.action?.enum || [];
      if (!effect || declared.length !== Object.keys(effect).length || declared.some(action => !effects.has(effect[action])))
        throw Error(`Unity action effect coverage drift: ${tool.name}`);
      actions[tool.name] = declared.filter(action => effect[action] === "read");
    }
  }
  names.push(...workspaceToolDefinitions().map(tool => tool.name));
  return "// Generated from Unity catalog effect data. Do not edit.\n"
    + `export const UNITY_OBSERVATION_TOOL_NAMES: readonly string[] = ${JSON.stringify([...new Set(names)].sort())};\n`
    + `export const UNITY_READ_OPERATIONS: Readonly<Record<string, readonly string[]>> = ${JSON.stringify(actions)};\n`;
}
const output = path.resolve(__dirname, "../src/generated-unity-tool-contract.ts");
if (require.main === module) {
  const content = render();
  if (process.argv.includes("--check")) {
    if (!fs.existsSync(output) || fs.readFileSync(output, "utf8").replace(/\r\n/g, "\n") !== content) throw Error("Unity tool contract is stale");
  } else fs.writeFileSync(output, content);
}
module.exports = { render };
