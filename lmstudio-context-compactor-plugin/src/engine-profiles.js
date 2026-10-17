"use strict";

// Data only. CommonJS keeps the deterministic JS core runnable on Node 20
// without a TypeScript loader; TypeScript consumers use this same table.
const ENGINE_PROFILES = Object.freeze({
  unreal: Object.freeze({
    descriptorExtension: ".uproject",
    projectMarkers: [],
    providers: ["mcp/unreal-agent", "mcp/unreal-rag"],
    fileProvider: "mcp/unreal-agent",
    fileTools: ["read_file", "read_file_range", "read_symbol", "write_file", "replace_in_file", "apply_edit_bundle", "delete_file"],
    changeEvidenceTools: ["write_file", "replace_in_file", "apply_edit_bundle", "read_file", "read_file_range", "read_symbol"],
    reportReadTools: ["read_file", "read_file_range"],
    networkingDocument: "unreal-networking",
  }),
  unity: Object.freeze({
    descriptorExtension: "",
    projectMarkers: ["Assets", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"],
    providers: ["mcp/unity-tools"],
    fileProvider: "mcp/unity-tools",
    fileTools: ["read_file", "patch_file", "create_file", "structured_data_patch"],
    changeEvidenceTools: ["patch_file", "create_file", "structured_data_patch", "read_file"],
    reportReadTools: ["read_file"],
    networkingDocument: "unity-networking",
  }),
});
const ENGINE_PROVIDERS = Object.freeze(Object.values(ENGINE_PROFILES).flatMap(profile => profile.providers));
module.exports = { ENGINE_PROFILES, ENGINE_PROVIDERS };
