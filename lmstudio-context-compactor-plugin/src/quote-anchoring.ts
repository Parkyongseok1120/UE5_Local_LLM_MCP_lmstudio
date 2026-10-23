import type { ChatMessage } from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";

// Compares the code an evidence packet says it observed with the source text
// this execution actually returned. The packet validator checks structure
// only; the returned bodies are still in the visible transcript, so a call,
// handler or specifier that no returned body contains is a mismatch we can
// name. Advisory: nothing here blocks, rewrites a result, or verifies a fact.

const SOURCE_READ_TOOLS = new Set(["read_file_range", "read_file", "read_symbol"]);
const VALIDATE_TOOL = "evidence_first_validate";
const LINE_SLACK = 2;
// A short cite inside a body may name its enclosing function, declared above it.
const ENCLOSING_LOOKBACK = 30;
const DECLARATION_TOKEN = /^(?:OnRep_\w+|COND_\w+|ReplicatedUsing|NetMulticast|WithValidation|Unreliable|Reliable|Replicated|Server|Client)$/;
const EXACT_CITE_MAX_SPAN = 3;
const MAX_REPORTED = 4;
const MAX_LINE_CHARS = 160;
const NETWORK_SPECIFIERS = /\b(ReplicatedUsing|NetMulticast|WithValidation|Unreliable|Reliable|Replicated|Server|Client)\s*=?/g;

type SourceFile = { path: string; lines: Map<number, string> };
export type QuoteMismatch = { claimPath: string; location: string; missingTokens: string[]; returnedLines: string[];
  kind: "not_in_returned_source" | "not_at_cited_lines" };

const normalizePath = (value: string) => value.replace(/^project:\/\//, "").replace(/\\/g, "/").replace(/^\.?\//, "").toLowerCase();

function decoded(content: unknown): Record<string, any> | null {
  const value = toolMemory.decodeToolResultRecord(content).value;
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : null;
}

/** Latest returned text of each source line, by file, in transcript order. */
export function returnedSourceLines(messages: ChatMessage[]): SourceFile[] {
  const names = new Map<string, string>();
  const files = new Map<string, SourceFile>();
  for (const message of messages) {
    for (const request of message.getToolCallRequests()) names.set(String(request.id || ""), request.name);
    for (const result of message.getToolCallResults()) {
      if (!SOURCE_READ_TOOLS.has(names.get(String(result.toolCallId || "")) || "")) continue;
      const value = decoded((result as { content?: unknown }).content);
      if (!value || value.ok === false || typeof value.content !== "string") continue;
      const path = String(value.projectRelativePath || value.path || "");
      if (!path) continue;
      const file = files.get(normalizePath(path)) || { path, lines: new Map<number, string>() };
      const numbered = value.lineNumbers === true;
      const start = Number.isInteger(value.startLine) ? value.startLine : 1;
      value.content.split(/\r?\n/).forEach((text: string, index: number) => {
        const match = numbered ? /^(\d+)\t(.*)$/.exec(text) : null;
        if (numbered && !match) return;
        file.lines.set(match ? Number(match[1]) : start + index, match ? match[2] : text);
      });
      files.set(normalizePath(path), file);
    }
  }
  return [...files.values()];
}

/** Code-shaped names an observation states as present: qualified members,
 * calls, OnRep handlers, and network specifiers written as code. */
export function observedCodeTokens(observation: string): string[] {
  const tokens = new Set<string>();
  for (const match of observation.matchAll(/\b[A-Za-z_]\w*::(~?[A-Za-z_]\w*)/g)) tokens.add(match[1]);
  for (const match of observation.matchAll(/\b([A-Za-z_]\w{2,})\(/g)) tokens.add(match[1]);
  for (const match of observation.matchAll(/\bOnRep_\w+/g)) tokens.add(match[0]);
  // Specifiers count only inside a macro argument list or as `Name=`, so prose
  // such as "two replicated properties" is not read as a quote.
  for (const macro of observation.matchAll(/\bU(?:PROPERTY|FUNCTION)\(([^)]*)\)/g))
    for (const match of macro[1].matchAll(NETWORK_SPECIFIERS)) tokens.add(match[1]);
  for (const match of observation.matchAll(/\b(ReplicatedUsing|COND_\w+)\b/g)) tokens.add(match[1]);
  for (const name of ["UPROPERTY", "UFUNCTION"]) tokens.delete(name);
  return [...tokens];
}

const hasToken = (text: string, token: string) => new RegExp(`(^|[^\\w])${token.replace(/[$]/g, "\\$")}($|[^\\w])`).test(text);

function locate(location: string, files: SourceFile[]) {
  const match = /^(.*?):(\d+)(?:-(\d+))?$/.exec(location.trim());
  if (!match) return null;
  const wanted = normalizePath(match[1]);
  const candidates = files.filter(file => {
    const path = normalizePath(file.path);
    return path === wanted || path.endsWith(`/${wanted}`) || wanted.endsWith(`/${path}`);
  });
  if (candidates.length !== 1) return null;
  const start = Number(match[2]), end = Math.max(start, Number(match[3] || match[2]));
  return { file: candidates[0], start, end };
}

/** Mismatches between one packet's project-source evidence and returned text. */
export function packetQuoteMismatches(packet: unknown, files: SourceFile[]): QuoteMismatch[] {
  const claims = (packet as { claims?: unknown })?.claims;
  if (!Array.isArray(claims)) return [];
  const mismatches: QuoteMismatch[] = [];
  claims.forEach((claim, claimIndex) => {
    const evidence = (claim as { evidence?: unknown })?.evidence;
    if (!Array.isArray(evidence)) return;
    evidence.forEach((item, evidenceIndex) => {
      if (item?.kind !== "project_source" || typeof item.location !== "string" || typeof item.observation !== "string") return;
      const tokens = observedCodeTokens(item.observation);
      const target = tokens.length ? locate(item.location, files) : null;
      if (!target) return;
      const cited: string[] = [];
      for (let line = target.start; line <= target.end; line++) {
        const text = target.file.lines.get(line);
        if (text !== undefined && text.trim()) cited.push(`${line}: ${text.trim().slice(0, MAX_LINE_CHARS)}`);
      }
      if (!cited.length) return;
      const fileText = [...target.file.lines.values()].join("\n");
      const absent = tokens.filter(token => !files.some(file => hasToken([...file.lines.values()].join("\n"), token)));
      const near = (from: number) => {
        const window: string[] = [];
        for (let line = from; line <= target.end + LINE_SLACK; line++) {
          const text = target.file.lines.get(line);
          if (text !== undefined) window.push(text);
        }
        return window.join("\n");
      };
      // Specifiers and handlers belong to the cited declaration; a call or
      // member name may also be the function the cited lines sit in.
      const cites = (token: string) => hasToken(near(target.start
        - (DECLARATION_TOKEN.test(token) ? LINE_SLACK : ENCLOSING_LOOKBACK)), token);
      const notCited = target.end - target.start < EXACT_CITE_MAX_SPAN
        ? tokens.filter(token => !absent.includes(token) && hasToken(fileText, token) && !cites(token)) : [];
      const claimPath = `claims[${claimIndex}].evidence[${evidenceIndex}]`;
      if (absent.length) mismatches.push({ claimPath, location: item.location, missingTokens: absent,
        returnedLines: cited.slice(0, 2), kind: "not_in_returned_source" });
      else if (notCited.length) mismatches.push({ claimPath, location: item.location, missingTokens: notCited,
        returnedLines: cited.slice(0, 2), kind: "not_at_cited_lines" });
    });
  });
  return mismatches;
}

/** Packets this round sent to the validator, in order. */
export function validatePackets(messages: ChatMessage[]): unknown[] {
  return messages.flatMap(message => message.getToolCallRequests())
    .filter(request => request.name === VALIDATE_TOOL)
    .map(request => (request.arguments as { packet?: unknown } | undefined)?.packet)
    .filter(packet => packet !== undefined);
}

function describe(mismatch: QuoteMismatch): string {
  const what = mismatch.kind === "not_in_returned_source"
    ? `${mismatch.missingTokens.join(", ")} appears in no source text returned in this execution`
    : `${mismatch.missingTokens.join(", ")} is not on the cited lines`;
  return `- ${mismatch.claimPath} ${mismatch.location}: ${what}; returned text there: ${mismatch.returnedLines.join(" | ")}`;
}

/** Runtime fact for the next input, or null when the packet's code names match. */
export function quoteCheckFact(mismatches: QuoteMismatch[]): string | null {
  if (!mismatches.length) return null;
  const listed = mismatches.slice(0, MAX_REPORTED).map(describe);
  const more = mismatches.length > MAX_REPORTED ? [`- ${mismatches.length - MAX_REPORTED} more not listed`] : [];
  return [
    `Runtime evidence observation: the last evidence packet quotes ${mismatches.length} code name(s) that the source returned in this execution does not show.`,
    ...listed, ...more,
    "This compares names only; it is not a fact check. Re-read the cited range and correct the observation, location "
      + "or claim from the returned text before reporting it; do not keep a call, handler or specifier the source does not show.",
  ].join("\n");
}

/** Footer for the delivered answer when the last packet's mismatches remain.
 * The answer text itself is never rewritten. */
export function quoteAnchoringNotice(mismatches: QuoteMismatch[]): string | null {
  if (!mismatches.length) return null;
  const listed = mismatches.slice(0, MAX_REPORTED).map(mismatch => `- ${mismatch.location}: `
    + `${mismatch.missingTokens.join(", ")} → 반환된 원문: ${mismatch.returnedLines.join(" | ")}`);
  const more = mismatches.length > MAX_REPORTED ? [`- 외 ${mismatches.length - MAX_REPORTED}건`] : [];
  return ["", "", `[인용 대조] 마지막 근거 패킷에서 이번 실행이 반환한 소스와 맞지 않는 코드 이름 ${mismatches.length}건이 남아 있습니다. `
    + "이름만 비교한 결과이며 사실 검증이 아닙니다. 해당 지적은 원문을 다시 확인한 뒤 판단해야 합니다.", ...listed, ...more].join("\n");
}
