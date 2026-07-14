"use strict";

const fs = require("fs");
const path = require("path");

const MANIFEST_PATH = path.resolve(__dirname, "../../config/stable_tool_manifest.json");

const AGENT_EXTENDED_PROFILE_TOOLS = new Set([
  "set_active_project",
  "detect_unreal_project",
  "list_unreal_projects",
  "open_active_project_picker",
  "run_command",
  "refactor_impact_scan",
  "refactor_plan_validate",
  "propose_file_deletions",
  "delete_file",
]);

let manifestCache = null;

function envFlag(name) {
  return ["1", "true", "yes", "on"].includes(String(process.env[name] || "").trim().toLowerCase());
}

function loadStableManifest() {
  if (manifestCache) {
    return manifestCache;
  }
  manifestCache = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  return manifestCache;
}

function callableAgentToolNames(allRegistered) {
  const manifest = loadStableManifest();
  const essential = new Set(manifest.agentEssential || []);
  const hidden = new Set(manifest.agentHiddenUntilControlPlane || []);
  const registered = new Set(allRegistered);
  const visible = new Set(registered);
  if (!envFlag("ALLOW_CONTROL_PLANE_TOOLS")) {
    for (const name of hidden) {
      visible.delete(name);
    }
  }
  if (envFlag("MCP_EXTENDED_TOOLS")) {
    return visible;
  }
  const allowed = new Set(essential);
  if (envFlag("ALLOW_CONTROL_PLANE_TOOLS")) {
    for (const name of hidden) {
      allowed.add(name);
    }
  }
  return new Set([...visible].filter((name) => allowed.has(name)));
}

function toolNotCallablePayload(toolName) {
  const message = `Tool '${toolName}' is not callable in the current MCP exposure profile.`;
  return {
    ok: false,
    errorCode: "TOOL_NOT_CALLABLE",
    error: message,
    phase: "failed",
    userMessage: message,
    agentInstruction: "Use tools/list to see callable tools for this MCP profile.",
    retryable: false,
  };
}

function projectSwitchGuidance(allRegisteredToolNames) {
  const allowed = callableAgentToolNames(allRegisteredToolNames);
  if (allowed.has("set_active_project")) {
    return {
      suggestedToolCalls: [{ tool: "set_active_project", args: {} }],
      agentInstruction: "Call set_active_project with a valid .uproject path."
    };
  }
  const manifest = loadStableManifest();
  const canonical = manifest.projectSwitchCanonical || "unreal_set_active_project";
  return {
    requiredNextTool: { server: "unreal-rag", name: canonical },
    suggestedToolCalls: [{ tool: canonical, args: {} }],
    agentInstruction: `Call ${canonical} on unreal-rag with a valid .uproject path.`
  };
}

module.exports = {
  callableAgentToolNames,
  toolNotCallablePayload,
  loadStableManifest,
  projectSwitchGuidance,
};
