"use strict";

// Data only. CommonJS keeps the deterministic JS core runnable on Node 20
// without a TypeScript loader; TypeScript consumers use this same table.
const { namesForRole } = require("./tool-capability-core.js");
const ENGINE_PROFILES = Object.freeze({
  unreal: Object.freeze({
    descriptorExtension: ".uproject",
    projectMarkers: [],
    providers: ["mcp/unreal-agent", "mcp/unreal-rag"],
    fileProvider: "mcp/unreal-agent",
    fileTools: namesForRole("file_evidence", "mcp/unreal-agent"),
    changeEvidenceTools: namesForRole("change_evidence", "mcp/unreal-agent"),
    reportReadTools: namesForRole("report_read", "mcp/unreal-agent"),
    networkingDocument: "unreal-networking",
  }),
  unity: Object.freeze({
    descriptorExtension: "",
    projectMarkers: ["Assets", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"],
    providers: ["mcp/unity-tools"],
    fileProvider: "mcp/unity-tools",
    fileTools: namesForRole("file_evidence", "mcp/unity-tools"),
    changeEvidenceTools: namesForRole("change_evidence", "mcp/unity-tools"),
    reportReadTools: namesForRole("report_read", "mcp/unity-tools"),
    networkingDocument: "unity-networking",
  }),
});
const ENGINE_PROVIDERS = Object.freeze(Object.values(ENGINE_PROFILES).flatMap(profile => profile.providers));
module.exports = { ENGINE_PROFILES, ENGINE_PROVIDERS };
