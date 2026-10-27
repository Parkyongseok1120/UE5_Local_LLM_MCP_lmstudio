"use strict";
// Pure manifest queries shared by the TS harness and the CommonJS memory core.
// Object authority is local to this loaded module, never serialized.
/** @type {Record<string, Record<string, any>>} */
const TOOL_CAPABILITIES = require("./generated-tool-capability-data.js").TOOL_CAPABILITIES;
const localObservationTools = new WeakSet();
/** @param {any} tool @param {{name:string, arguments?:Record<string,any>}} request */
function capabilityFor(tool, request = {name: tool.name, arguments: {}}) {
  if (request.name !== tool.name) return null;
  const provider = String(tool.pluginIdentifier || "").trim().toLowerCase();
  let entry = TOOL_CAPABILITIES[provider]?.[tool.name];
  if (localObservationTools.has(tool)) entry = Object.values(TOOL_CAPABILITIES).map(p=>p[tool.name]).find(c=>c?.localOnly) || entry;
  if (!entry || entry.localOnly && !localObservationTools.has(tool)) return null;
  return entry.actions ? entry.actions[String(request.arguments?.action || "")] || null : entry;
}
function operationCapabilities(tool) {
  const entry = TOOL_CAPABILITIES[String(tool.pluginIdentifier || "").trim().toLowerCase()]?.[tool.name];
  return entry?.actions ? Object.entries(entry.actions).map(([action, c])=>({action,...c})) : [capabilityFor(tool)].filter(Boolean);
}
function namesForRole(role, provider) {
  const providers = provider ? [TOOL_CAPABILITIES[provider] || {}] : Object.values(TOOL_CAPABILITIES);
  return [...new Set(providers.flatMap(p=>Object.entries(p).filter(([,c])=>(c.actions?Object.values(c.actions):[c]).some(op=>op.roles?.includes(role))).map(([n])=>n)))];
}
function hasRole(name, role, provider) { return namesForRole(role, provider).includes(name); }
function needsReadReservation(tool, request) { return ["source_body","search_hits","archive_page"].includes(capabilityFor(tool,request)?.resultKind); }
module.exports={capabilityFor,operationCapabilities,namesForRole,hasRole,needsReadReservation,localObservationTools};
