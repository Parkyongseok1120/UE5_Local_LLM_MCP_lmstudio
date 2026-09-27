import { bundledDesignGuidance } from "./generated-design-guidance";
import type { ToolScope } from "./tool-scope";

export type DesignGuidanceMode = "off" | "core" | "design" | "debugging" | "code-style" | "lifecycle" | "multiplayer";
export type GuidanceDocument = {
  readonly id: string;
  readonly engine: "common" | "unity" | "unreal";
  readonly source: string;
  readonly revision: string;
  readonly text: string;
};
export type GuidanceCandidate = { ids: string[]; instruction: string };

export function designGuidanceMode(value: unknown): DesignGuidanceMode {
  return value === "core" || value === "design" || value === "debugging"
    || value === "code-style" || value === "lifecycle" || value === "multiplayer" ? value : "off";
}

/** Pure selection: no project reads, inferred game rules, tool decisions or
 * process-wide state. The existing scope owner supplies engine evidence. */
export function selectDesignGuidance(mode: DesignGuidanceMode, scope: Pick<ToolScope, "engine" | "source">,
  documents: readonly GuidanceDocument[] = bundledDesignGuidance): GuidanceDocument[] {
  if (mode === "off") return [];
  const engines = scope.source === "available_tools" || scope.source === "ambiguous" ? []
    : scope.engine === "mixed" ? ["unity", "unreal"]
    : scope.engine === "unity" || scope.engine === "unreal" ? [scope.engine] : [];
  const ids = ["core"];
  if (mode !== "core") {
    if (mode !== "lifecycle") ids.push(mode);
    if (mode !== "code-style") {
      ids.push(...engines.map(engine => mode === "multiplayer" ? `${engine}-networking` : engine));
    }
  }
  const allowedEngines = new Set(["common", ...engines]);
  return [...new Set(ids)].flatMap(id => {
    const document = documents.find(item => item.id === id && allowedEngines.has(item.engine));
    return document ? [document] : [];
  });
}

/** Lower-priority whole documents may be omitted; never truncate a contract.
 * These instructions are only assembled into an ephemeral model input. */
export function designGuidanceCandidates(documents: readonly GuidanceDocument[]): GuidanceCandidate[] {
  const unique = [...new Map(documents.map(document => [document.id, document])).values()];
  const candidates: GuidanceCandidate[] = [];
  for (let length = unique.length; length > 0; length--) {
    const selected = unique.slice(0, length);
    candidates.push({ ids: selected.map(document => document.id), instruction: [
      "[Optional design reference]",
      "Bundled reference material, not project observations or proof of completed work. Apply only where relevant to the user's request and actual engine/package version. It adds no tool permissions or mandatory validation steps.",
      ...selected.map(document => `[reference id=${document.id} revision=${document.revision} source=${document.source}]\n${document.text}`),
      "[End optional design reference]",
    ].join("\n\n") });
  }
  return candidates;
}
