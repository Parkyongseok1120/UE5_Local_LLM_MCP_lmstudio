import fs from "node:fs";
import path from "node:path";
import type { ChatMessage, Tool, ToolCallRequest } from "@lmstudio/sdk";

export type ProjectEngineSetting = "auto" | "unity" | "unreal" | "mixed";
export type ResolvedProjectEngine = "unity" | "unreal" | "mixed" | "unknown";

export type ScopedTool = Tool & {
  pluginIdentifier?: string;
  parametersJsonSchema?: { properties?: Record<string, unknown> };
};

export type ToolScope = {
  configuredEngine: ProjectEngineSetting;
  engine: ResolvedProjectEngine;
  projectIdentity: string;
  source: "config" | "workspace" | "message" | "available_tools" | "ambiguous";
  availableUnityTools: number;
  availableUnrealTools: number;
};

function normalizedPluginIdentifier(tool: Pick<ScopedTool, "pluginIdentifier">): string {
  return String(tool.pluginIdentifier || "").trim().toLowerCase();
}

export function toolEngine(tool: Pick<ScopedTool, "pluginIdentifier">): "unity" | "unreal" | "common" {
  const identifier = normalizedPluginIdentifier(tool);
  if (identifier === "mcp/unity-tools" || identifier.endsWith("/unity-tools")) return "unity";
  if (identifier === "mcp/unreal-agent" || identifier.endsWith("/unreal-agent")
    || identifier === "mcp/unreal-rag" || identifier.endsWith("/unreal-rag")) return "unreal";
  return "common";
}

function directoryProjectScope(directory: string): { engine: ResolvedProjectEngine; projectIdentity: string } {
  const unityMarkers = ["Assets", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"];
  const unity = unityMarkers.every((marker) => fs.existsSync(path.join(directory, marker)));
  let unrealProjects: Array<string> = [];
  try {
    unrealProjects = fs.readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".uproject"))
      .map((entry) => path.join(directory, entry.name));
  } catch {
    // The working directory may disappear or become unreadable between turns.
  }
  if (unity && unrealProjects.length > 0) return { engine: "mixed", projectIdentity: "" };
  if (unity) return { engine: "unity", projectIdentity: path.resolve(directory) };
  if (unrealProjects.length === 1) return { engine: "unreal", projectIdentity: path.resolve(unrealProjects[0]) };
  if (unrealProjects.length > 1) return { engine: "unreal", projectIdentity: "" };
  return { engine: "unknown", projectIdentity: "" };
}

function explicitProjectScope(value: string): { engine: ResolvedProjectEngine; projectIdentity: string } {
  const candidate = String(value || "").trim();
  if (!candidate || !path.isAbsolute(candidate)) return { engine: "unknown", projectIdentity: "" };
  try {
    const resolved = path.resolve(candidate);
    if (resolved.toLowerCase().endsWith(".uproject") && fs.statSync(resolved).isFile()) {
      return { engine: "unreal", projectIdentity: resolved };
    }
    if (!fs.statSync(resolved).isDirectory()) return { engine: "unknown", projectIdentity: "" };
    return detectWorkspaceProject(resolved);
  } catch {
    return { engine: "unknown", projectIdentity: "" };
  }
}

function existingPathFromCandidate(value: string): string {
  let candidate = value.trim().replace(/[),.;:!?]+$/u, "").trim();
  while (candidate) {
    if (path.isAbsolute(candidate) && fs.existsSync(candidate)) return candidate;
    const shortened = candidate.replace(/\s+\S+$/u, "").trim();
    if (shortened === candidate) break;
    candidate = shortened;
  }
  return "";
}

function pathsInText(text: string): Array<string> {
  const values: Array<string> = [];
  const patterns = [
    /(?:^|[\s`"'(=])([A-Za-z]:\\[^\r\n`"<>|?*]+)/gmu,
    /(?:^|[\s`"'(=])(\/[^\r\n`"<>|?*]+)/gmu,
  ];
  for (const pattern of patterns) {
    for (const match of String(text || "").matchAll(pattern)) {
      const existing = existingPathFromCandidate(match[1]);
      if (existing) values.push(existing);
    }
  }
  return values;
}

export function detectMentionedProject(messages: Array<ChatMessage>): {
  engine: ResolvedProjectEngine;
  projectIdentity: string;
} {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message.isUserMessage()) continue;
    const candidates = pathsInText(message.getText());
    for (let candidateIndex = candidates.length - 1; candidateIndex >= 0; candidateIndex -= 1) {
      const detected = explicitProjectScope(candidates[candidateIndex]);
      if (detected.engine === "unity" || detected.engine === "unreal") return detected;
    }
  }
  return { engine: "unknown", projectIdentity: "" };
}

export function detectWorkspaceProject(workingDirectory: string): {
  engine: ResolvedProjectEngine;
  projectIdentity: string;
} {
  if (!workingDirectory) return { engine: "unknown", projectIdentity: "" };
  let current = path.resolve(workingDirectory);
  for (let depth = 0; depth < 8; depth += 1) {
    const detected = directoryProjectScope(current);
    if (detected.engine !== "unknown") return detected;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return { engine: "unknown", projectIdentity: "" };
}

export function resolveToolScope(
  configuredEngine: ProjectEngineSetting,
  configuredProjectIdentity: string,
  workingDirectory: string,
  tools: Array<ScopedTool>,
  mentionedProject: { engine: ResolvedProjectEngine; projectIdentity: string } = {
    engine: "unknown", projectIdentity: "",
  },
): ToolScope {
  const availableUnityTools = tools.filter((tool) => toolEngine(tool) === "unity").length;
  const availableUnrealTools = tools.filter((tool) => toolEngine(tool) === "unreal").length;
  const configuredIdentity = String(configuredProjectIdentity || "").trim();
  if (configuredEngine !== "auto") {
    const mentionedIdentity = mentionedProject.engine === configuredEngine
      ? mentionedProject.projectIdentity : "";
    return {
      configuredEngine,
      engine: configuredEngine,
      projectIdentity: configuredIdentity || mentionedIdentity,
      source: "config",
      availableUnityTools,
      availableUnrealTools,
    };
  }

  const configuredProject = explicitProjectScope(configuredIdentity);
  if (configuredProject.engine === "unity" || configuredProject.engine === "unreal") {
    return {
      configuredEngine,
      engine: configuredProject.engine,
      projectIdentity: configuredProject.projectIdentity,
      source: "config",
      availableUnityTools,
      availableUnrealTools,
    };
  }

  if (mentionedProject.engine === "unity" || mentionedProject.engine === "unreal") {
    return {
      configuredEngine,
      engine: mentionedProject.engine,
      projectIdentity: mentionedProject.projectIdentity,
      source: "message",
      availableUnityTools,
      availableUnrealTools,
    };
  }
  const workspace = detectWorkspaceProject(workingDirectory);
  if (workspace.engine === "unity" || workspace.engine === "unreal") {
    return {
      configuredEngine,
      engine: workspace.engine,
      projectIdentity: configuredIdentity || workspace.projectIdentity,
      source: "workspace",
      availableUnityTools,
      availableUnrealTools,
    };
  }
  if (availableUnityTools > 0 && availableUnrealTools === 0) {
    return {
      configuredEngine,
      engine: "unity",
      projectIdentity: configuredIdentity,
      source: "available_tools",
      availableUnityTools,
      availableUnrealTools,
    };
  }
  if (availableUnrealTools > 0 && availableUnityTools === 0) {
    return {
      configuredEngine,
      engine: "unreal",
      projectIdentity: configuredIdentity,
      source: "available_tools",
      availableUnityTools,
      availableUnrealTools,
    };
  }
  return {
    configuredEngine,
    engine: "unknown",
    projectIdentity: configuredIdentity,
    source: "ambiguous",
    availableUnityTools,
    availableUnrealTools,
  };
}

export function filterToolsForScope(tools: Array<ScopedTool>, scope: ToolScope): Array<ScopedTool> {
  return tools.filter((tool) => {
    const engine = toolEngine(tool);
    if (engine === "common") return true;
    if (scope.engine === "mixed") return true;
    return engine === scope.engine;
  });
}

export function toolSupportsProjectArgument(tool: ScopedTool): boolean {
  return Boolean(tool.parametersJsonSchema?.properties
    && Object.prototype.hasOwnProperty.call(tool.parametersJsonSchema.properties, "project"));
}

export function bindProjectArguments(
  tool: ScopedTool,
  request: ToolCallRequest,
  projectIdentity: string,
): Record<string, unknown> | null {
  const identity = String(projectIdentity || "").trim();
  if (!identity || !["unity", "unreal"].includes(toolEngine(tool))) return null;
  const args = { ...(request.arguments || {}) } as Record<string, unknown>;
  if (toolSupportsProjectArgument(tool)) {
    if (args.project === identity) return null;
    return { ...args, project: identity };
  }
  if (request.name === "set_active_project") {
    delete args.hint;
    delete args.clear;
    return { ...args, projectPath: identity };
  }
  return null;
}

export function renderToolScopeInstruction(scope: ToolScope): string {
  if (scope.engine === "unknown" && scope.availableUnityTools > 0 && scope.availableUnrealTools > 0) {
    return [
      "[Deterministic project tool scope]",
      "The active engine could not be proven from the chat setting, a verified project path in the conversation, or workspace markers.",
      "Unity and Unreal project tools are withheld for this turn. Set Project engine, mention an existing exact project path, or open the exact project workspace before using project tools.",
      "Do not substitute one engine's tools for the other engine.",
    ].join("\n");
  }
  const identity = scope.projectIdentity || "not bound";
  return [
    "[Deterministic project tool scope]",
    `engine=${scope.engine}; projectIdentity=${identity}; source=${scope.source}`,
    "The visible project tools have already been filtered to this scope. Use only those tools and keep any explicit project argument equal to projectIdentity.",
    "Do not search for or switch to a different project unless the user changes the scope setting.",
  ].join("\n");
}
