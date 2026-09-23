from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/tool-capability-registry.ts')
s=p.read_text(encoding='utf-8')
a=s.index('export const READ_ONLY_RECOVERY_TOOL_NAMES')
b=s.index('export function isUnregisteredGitReadIntent',a)
s=s[:a]+s[b:]
s=s.replace('export function isObservationOnlyToolCall(', 'function hasReadAuthority(')
s=s.replace('plugin === "mcp/unity-tools" || plugin.endsWith("/unity-tools")','plugin === "mcp/unity-tools"')
s=s.replace('plugin === "mcp/unreal-agent" || plugin.endsWith("/unreal-agent")\n    || plugin === "mcp/unreal-rag" || plugin.endsWith("/unreal-rag")','plugin === "mcp/unreal-agent" || plugin === "mcp/unreal-rag"')
s=s.replace('["status", "results", "release"]','["status", "results"]')
s+='''
export type ToolCapability = {
  provider: string; name: string; scope: "unity"|"unreal"|"common";
  effect: "read"|"approval_required"; approval: "none"|"host";
  recoveryEligible: boolean; resultClass: "observation"|"operation";
  continuation: "archive_offset"|"published_schema"|"none"; rehydratable: boolean;
};
export function capabilityScope(tool: {pluginIdentifier?:string}): "unity"|"unreal"|"common" {
  const provider=String(tool.pluginIdentifier||"").trim().toLowerCase();
  return provider==="mcp/unity-tools"?"unity"
    : ["mcp/unreal-agent","mcp/unreal-rag"].includes(provider)?"unreal":"common";
}
export function resolveCapability(tool:RemoteToolLike, request:Pick<ToolCallRequest,"name"|"arguments">):ToolCapability {
  const read=hasReadAuthority(tool,request);
  return {provider:tool.pluginIdentifier||"local",name:tool.name,scope:capabilityScope(tool),
    effect:read?"read":"approval_required",approval:read?"none":"host",recoveryEligible:read,
    resultClass:read?"observation":"operation",rehydratable:read,
    continuation:read?(tool.name==="evidence_first_read_context"?"archive_offset":"published_schema"):"none"};
}
export function isObservationOnlyToolCall(tool:RemoteToolLike,request:Pick<ToolCallRequest,"name"|"arguments">) {
  return resolveCapability(tool,request).effect==="read";
}
export class ToolCapabilityRegistry {
  readonly tools: Array<RemoteToolLike>;
  readonly collisions: Array<string>;
  constructor(tools:Array<RemoteToolLike>) {
    this.collisions=[...new Set(tools.filter(t=>tools.filter(other=>other.name===t.name).length>1).map(t=>t.name))];
    this.tools=tools.filter(t=>!this.collisions.includes(t.name));
  }
  resolve(name:string) { return this.tools.find(t=>t.name===name); }
  readProfile() { return this.tools.filter(t=>resolveCapability(t,{name:t.name,arguments:{}}).recoveryEligible); }
}
'''
p.write_text(s,encoding='utf-8')
p=Path('lmstudio-context-compactor-plugin/src/tool-scope.ts');s=p.read_text(encoding='utf-8');a=s.index('function normalizedPluginIdentifier');b=s.index('function directoryProjectScope',a)
s=s[:a]+'''export function toolEngine(tool: Pick<ScopedTool, "pluginIdentifier">): "unity" | "unreal" | "common" {
  return capabilityScope(tool);
}

'''+s[b:];s='import { capabilityScope } from "./tool-capability-registry";\n'+s;p.write_text(s,encoding='utf-8')
p=Path('lmstudio-context-compactor-plugin/src/recovery-coordinator.ts');s=p.read_text(encoding='utf-8').replace('READ_ONLY_RECOVERY_TOOL_NAMES','ToolCapabilityRegistry');s=s.replace('visibleTools.filter(tool => ToolCapabilityRegistry.has(tool.name)\n    && isObservationOnlyToolCall(tool, { name: tool.name, arguments: {} }))','new ToolCapabilityRegistry(visibleTools).readProfile()');p.write_text(s,encoding='utf-8')
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts');s=p.read_text(encoding='utf-8');s='import { ToolCapabilityRegistry } from "./tool-capability-registry";\n'+s;s=s.replace('const allModelTools = [...scopedRemoteTools, ...localTools] as Array<RemoteToolLike>;', 'const capabilityRegistry = new ToolCapabilityRegistry([...scopedRemoteTools, ...localTools] as Array<RemoteToolLike>);\n  const allModelTools = capabilityRegistry.tools;');p.write_text(s,encoding='utf-8')
