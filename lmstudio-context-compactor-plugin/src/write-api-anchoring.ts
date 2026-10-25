import type { ChatMessage } from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";
import { returnedSourceLines } from "./quote-anchoring";

// Across recorded write sessions the model never read engine source before
// editing, and 51 of 57 builds failed on headers and engine members it had
// guessed. This compares the engine names an edit adds with the source this
// execution actually returned, so an unverified name is checked before a
// multi-minute build reports only the first of them. Advisory: nothing here
// blocks, rewrites an edit, or decides that a name is wrong.

const MUTATION_TOOLS = new Set(["replace_in_file", "apply_edit_bundle", "write_file"]);
const LOOKUP_TOOLS = new Set(["unreal_symbol_lookup", "unreal_rag_search", "search_files", "list_directory"]);
// Core value and math types every module uses; flagging them would bury the
// names that actually break builds (wrong headers, members of the wrong class).
const CORE_TYPES = new Set(["FMath", "FString", "FName", "FText", "FVector", "FVector2D", "FRotator", "FQuat", "FTransform",
  "FColor", "FLinearColor", "FPlatformTime", "FMemory", "EAxis", "ESearchCase", "TEXT"]);
const MAX_REPORTED = 6;
const INCLUDE = /^\s*#\s*include\s*"([^"]+)"/gm;
// Unreal type prefixes followed by an upper-case letter: AActor::, UWorld::, FKey::, EKeyEvent::.
const QUALIFIED = /\b([AUFEIT][A-Z]\w+)::(~?[A-Za-z_]\w*)/g;
const DECLARED = /\b(?:class|struct|enum(?:\s+class)?|using|typedef)\s+(?:[A-Z_]+_API\s+)?([A-Za-z_]\w*)/g;
const DEFINITION = /^[\w<>*&:,\s]*?\b([AUFEIT][A-Z]\w+)::~?[A-Za-z_]\w*\s*\([^;{]*\)?\s*(?:const\s*)?(?:override\s*)?\{?\s*$/gm;

export type UnverifiedApi = { kind: "include" | "member"; name: string; path: string };

function decoded(content: unknown): Record<string, any> | null {
  const value = toolMemory.decodeToolResultRecord(content).value;
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : null;
}

/** Lines an edit adds: hunk lines absent from its before text, or a new file's content. */
function addedText(name: string, args: Record<string, any>, value: Record<string, any>): Array<{ path: string; text: string }> {
  if (name === "write_file") return typeof args.content === "string" ? [{ path: String(value.path || args.path || ""), text: args.content }] : [];
  const files = Array.isArray(value.files) ? value.files : [value];
  return files.flatMap((file: any) => {
    const hunks = Array.isArray(file?.changeEvidence?.hunks) ? file.changeEvidence.hunks : [];
    return hunks.map((hunk: any) => {
      const before = new Set(String(hunk?.before || "").split(/\r?\n/).map((line: string) => line.trim()));
      const lines = String(hunk?.after || "").split(/\r?\n/).filter((line: string) => !before.has(line.trim()));
      return { path: String(file.path || value.path || args.path || ""), text: lines.join("\n") };
    });
  });
}

/** Engine names that this execution's edits add and that no returned source shows. */
export function unverifiedWriteApis(messages: ChatMessage[]): UnverifiedApi[] {
  const requests = new Map<string, { name: string; args: Record<string, any> }>();
  const added: Array<{ path: string; text: string }> = [];
  const lookups: string[] = [];
  for (const message of messages) {
    for (const request of message.getToolCallRequests())
      requests.set(String(request.id || ""), { name: request.name, args: (request.arguments || {}) as Record<string, any> });
    for (const result of message.getToolCallResults()) {
      const request = requests.get(String(result.toolCallId || ""));
      const value = request && decoded((result as { content?: unknown }).content);
      if (!request || !value || value.ok === false) continue;
      if (MUTATION_TOOLS.has(request.name)) added.push(...addedText(request.name, request.args, value));
      else if (LOOKUP_TOOLS.has(request.name)) lookups.push(JSON.stringify(value));
    }
  }
  return unverifiedAddedApis(messages, added, lookups);
}

function unverifiedAddedApis(messages: ChatMessage[], added: Array<{path: string; text: string}>, lookups: string[], strictMembers = false): UnverifiedApi[] {
  if (!added.length) return [];
  // Files this execution creates or edits are project files, not engine names.
  const editedFiles = added.map(({ path }) => path.replace(/\\/g, "/").split("/").pop() || "").filter(Boolean);
  const files = returnedSourceLines(messages);
  const project = files.filter(file => !/^engine:\/\//i.test(file.path)).map(file => [...file.lines.values()].join("\n")).join("\n");
  const engine = files.filter(file => /^engine:\/\//i.test(file.path)).map(file => `${file.path}\n${[...file.lines.values()].join("\n")}`).join("\n");
  const seen = `${project}\n${engine}\n${lookups.join("\n")}`;
  const declared = new Set<string>();
  for (const { text } of added) {
    for (const match of text.matchAll(DECLARED)) declared.add(match[1]);
    // A member defined here (a signature line without a semicolon) belongs to a project class.
    for (const match of text.matchAll(DEFINITION)) declared.add(match[1]);
  }
  const editedStems = editedFiles.map(name => name.replace(/\.[^.]+$/, ""));
  for (const match of project.matchAll(DECLARED)) declared.add(match[1]);
  const known = (token: string) => new RegExp(`(^|[^\\w])${token.replace(/[$~]/g, "\\$&")}($|[^\\w])`).test(seen);
  const found = new Map<string, UnverifiedApi>();
  for (const { path, text } of added) {
    for (const match of text.matchAll(INCLUDE)) {
      const header = match[1], base = header.split("/").pop()!;
      // A header the project already includes, or one whose file a lookup or read returned, is known.
      if (/\.generated\.h$/i.test(header) || project.includes(`"${header}"`) || seen.includes(`/${header}`) || seen.includes(`"${header}"`) || editedFiles.includes(base)
        || editedStems.includes(base.replace(/\.[^.]+$/, ""))) continue;
      found.set(`include:${header}`, { kind: "include", name: `#include "${header}"`, path });
    }
    for (const match of text.matchAll(QUALIFIED)) {
      const [, type, member] = match;
      if (declared.has(type) || CORE_TYPES.has(type) || member.endsWith("_Implementation") || member.endsWith("_Validate")) continue;
      const qualified = `${type}::${member}`;
      // Suggested code has no compiler result. Independent occurrences of a
      // type and a same-named member on another type cannot establish its owner.
      if (strictMembers ? known(qualified) : known(type) && known(member)) continue;
      found.set(`member:${type}::${member}`, { kind: "member", name: `${type}::${member}`, path });
    }
  }
  return [...found.values()];
}

/** Runtime fact for the next input, or null when every added engine name was seen. */
export function writeApiFact(unverified: UnverifiedApi[]): string | null {
  if (!unverified.length) return null;
  const listed = unverified.slice(0, MAX_REPORTED).map(api => `- ${api.name} (${api.path || "edited file"})`);
  const more = unverified.length > MAX_REPORTED ? [`- ${unverified.length - MAX_REPORTED} more not listed`] : [];
  return [
    `Runtime evidence observation: edits in this execution add ${unverified.length} engine name(s) that no engine source, symbol lookup or project source returned in this execution shows.`,
    ...listed, ...more,
    "This compares names only; it is not a verdict that they are wrong. Check each declaration with unreal_symbol_lookup "
      + "(sourceMode=engine_local, view=signature) or the engine header before building: a failed build reports the first error, not all of them.",
  ].join("\n");
}

/** Proposed snippets are not edits and inherit no successful-build claim.
 * Reuse API name checks, but require an owner-qualified declaration/reference. */
export function suggestedCodeNotice(messages: ChatMessage[], answer: string, engine: string): string | null {
  if (engine !== "unreal") return null;
  const added: Array<{path: string; text: string}> = [];
  for (const match of answer.matchAll(/```(?:cpp|c\+\+|cxx|h)\s*\n([\s\S]*?)```/giu)) {
    const before = answer.slice(Math.max(0, match.index! - 600), match.index);
    if (!/(?:제안|수정|대안|대체|권장|propos|suggest|fix|replace|recommend)/iu.test(before)) continue;
    let code = match[1];
    // Check explicitly typed receivers too. Unknown/auto receivers remain
    // uncompiled, never promoted to a checked engine API by this heuristic.
    for (const declaration of code.matchAll(/\b([AUF][A-Z]\w*)\s*[*&]\s*(\w+)/gu)) {
      const [, type, receiver] = declaration;
      for (const call of code.matchAll(new RegExp(`\\b${receiver}\\s*->\\s*(\\w+)\\s*\\(`, "gu")))
        code += `\n${type}::${call[1]}`;
    }
    added.push({path:`suggestion ${added.length + 1}`,text:code});
  }
  if (!added.length) return null;
  const apis = unverifiedAddedApis(messages, added, [], true);
  return ["", "", `[제안 코드 검증 범위] C++ 예시 ${added.length}개는 별도 컴파일하지 않은 제안입니다. 선언 이름의 대조는 타입·수신 객체·단위·수명 및 실제 동작 검증을 대신하지 않습니다.`,
    ...apis.slice(0, MAX_REPORTED).map(api => `- ${api.path}: ${api.name} — 반환된 소스에서 이 소유 타입의 선언을 확인하지 못했습니다.`)].join("\n");
}
