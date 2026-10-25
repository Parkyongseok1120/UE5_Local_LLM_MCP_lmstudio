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
const INLINE_LOCATION = /((?:project:\/\/|[A-Za-z]:[\\/])?[\w.\\/-]+):(\d+)(?:\s*-\s*(\d+))?\b|\b[Ll]ines?\s+(\d+)(?:\s*-\s*(\d+))?\b|(\d+)(?:\s*-\s*(\d+))?\s*(?:번째\s*)?(?:행|줄)/gu;
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

/** The first line number of a result's content when its position is
 * verifiable: an explicit startLine, or a byte window that starts at the
 * file's first byte. A mid-file byte window (read_file offsetBytes > 0, the
 * long-line recovery path) has no line position, so it registers none. */
function contentStartLine(value: Record<string, any>): number | null {
  if (Number.isInteger(value.startLine) && value.startLine > 0) return value.startLine;
  return value.offsetBytes === 0 ? 1 : null;
}

/** Latest returned text of each source line, by file, in transcript order.
 * Only lines whose position the result proves are registered. */
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
      const numbered = value.lineNumbers === true;
      const start = numbered ? null : contentStartLine(value);
      if (!numbered && start === null) continue;
      const file = files.get(normalizePath(path)) || { path, lines: new Map<number, string>() };
      const lines = value.content.split(/\r?\n/);
      // Before EOF the last split item is unproven: either a cut line or the
      // empty item after a final newline, whose next line was not read yet.
      if (!numbered && value.offsetBytes === 0 && (value.hasMore === true || value.truncated === true)) lines.pop();
      lines.forEach((text: string, index: number) => {
        const match = numbered ? /^(\d+)\t(.*)$/.exec(text) : null;
        if (numbered && !match) return;
        file.lines.set(match ? Number(match[1]) : start! + index, match ? match[2] : text);
      });
      files.set(normalizePath(path), file);
    }
  }
  return [...files.values()];
}

// A clause that denies, compares or proposes a name does not claim the source
// shows it ("never calls Cleanup()", "Cleanup()을 호출하지 않는다", "should call").
// Markers that precede their object exclude only the names after them; markers
// whose object may stand on either side, and Korean endings that follow their
// object, exclude the whole clause. When a clause's meaning is unclear this
// leaves its names unchecked rather than reporting an unverified mismatch.
const OBJECT_FOLLOWS = /\b(?:never|without|instead\s+of|rather\s+than|unlike|nor|neither|lacks?|lacking|omits?|fails?\s+to|should|must|needs?\s+to|ought\s+to|would|could|recommend\w*|consider)\b/iu;
const WHOLE_CLAUSE = /\b(?:not|no|none|missing|absent|removed|unused)\b|n't\b|없|않|못|누락|빠져|빠진|대신|아니|부재|미호출|해야|필요|권장|(?:^|\s)안\s/iu;
const CLAUSE_WORDS = /\b(?:but|while|whereas|however|although|though|yet|except)\b|지만|그러나|반면|는데/giu;

// Commas in lists keep their governing denial/recommendation, including Korean
// endings after the final item. Split only a clear new affirmative assertion;
// ambiguous comma continuations remain unchecked with the rest of the clause.
function commaStartsAssertion(text: string): boolean {
  const next = text.split(/[;!?\n]/u, 1)[0];
  if (WHOLE_CLAUSE.test(next) || OBJECT_FOLLOWS.test(next)) return false;
  return /^\s*(?:(?:and|also)\s+)?[A-Za-z_]\w*(?:::\w+)*(?:\([^()\n]*\))?\s+(?:calls?|invokes?|returns?|sets?|uses?|contains?|defines?|declares?|is|are|has)\b/iu.test(next)
    || /^\s*[A-Za-z_]\w*(?:::\w+)*(?:\([^()\n]*\))?(?:은|는|이|가)\s+.*(?:호출한다|호출합니다|정의한다|반환한다)/u.test(next);
}

/** Clause spans split at sentence/clause boundaries outside parentheses. */
function clauseSpans(text: string): Array<[number, number]> {
  const depth: number[] = [];
  const cuts = new Set<number>();
  let level = 0;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === "(") level++;
    depth.push(level);
    if (char === ")") level = Math.max(0, level - 1);
    if (depth[index] > 0) continue;
    if (/[;!?\n]/u.test(char) || (char === "," && commaStartsAssertion(text.slice(index + 1)))
      || (char === "." && (index + 1 >= text.length || /\s/u.test(text[index + 1])))) cuts.add(index);
  }
  for (const match of text.matchAll(CLAUSE_WORDS)) if (!depth[match.index!]) cuts.add(match.index!);
  const spans: Array<[number, number]> = [];
  let start = 0;
  for (const cut of [...cuts].sort((a, b) => a - b)) { spans.push([start, cut]); start = cut; }
  spans.push([start, text.length]);
  return spans;
}

/** Code-shaped names an observation states as present: qualified members,
 * calls, OnRep handlers, and network specifiers written as code. Names that
 * a clause denies, compares or proposes are not claims and are left out. */
export function observedCodeTokens(observation: string): string[] {
  const found: Array<{ name: string; at: number }> = [];
  for (const match of observation.matchAll(/\b[A-Za-z_]\w*::(~?[A-Za-z_]\w*)/g))
    found.push({ name: match[1], at: match.index! + match[0].length - match[1].length });
  for (const match of observation.matchAll(/\b([A-Za-z_]\w{2,})\(/g)) found.push({ name: match[1], at: match.index! });
  for (const match of observation.matchAll(/\bOnRep_\w+/g)) found.push({ name: match[0], at: match.index! });
  // Specifiers count only inside a macro argument list or as `Name=`, so prose
  // such as "two replicated properties" is not read as a quote.
  for (const macro of observation.matchAll(/\bU(?:PROPERTY|FUNCTION)\(([^)]*)\)/g))
    for (const match of macro[1].matchAll(NETWORK_SPECIFIERS))
      found.push({ name: match[1], at: macro.index! + macro[0].indexOf("(") + 1 + match.index! });
  for (const match of observation.matchAll(/\b(ReplicatedUsing|COND_\w+)\b/g)) found.push({ name: match[1], at: match.index! });
  const spans = clauseSpans(observation);
  const claimed = (at: number) => {
    const [start, end] = spans.find(([from, to]) => at >= from && at < to) || [0, observation.length];
    const clause = observation.slice(start, end);
    if (WHOLE_CLAUSE.test(clause)) return false;
    const marker = OBJECT_FOLLOWS.exec(clause);
    return !marker || at < start + marker.index;
  };
  const tokens = new Set(found.filter(token => claimed(token.at)).map(token => token.name));
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

type CitedSource = NonNullable<ReturnType<typeof locate>>;

/** Resolve only the inline locations in this clause. An extension shorthand
 * (cpp:315) names the uniquely returned sibling file; line-only cites name the
 * evidence file. Unknown additional ranges retain the conservative name-only
 * comparison for this clause, never for the rest of the observation. */
function clauseSources(text: string, target: CitedSource, files: SourceFile[]) {
  const sources = [target];
  let unverified = false;
  for (const match of text.matchAll(INLINE_LOCATION)) {
    const start = match[2] || match[4] || match[6], end = match[3] || match[5] || match[7] || start;
    let path = match[1] || target.file.path;
    if (/^\.?(?:h|hh|hpp|hxx|inl|c|cc|cpp|cxx|cs)$/iu.test(path)) {
      const basename = target.file.path.replace(/\\/g, "/").split("/").pop()!;
      path = basename.replace(/\.[^.]+$/, "") + "." + path.replace(/^\./, "");
    }
    const source = locate(`${path}:${start}-${end}`, files);
    if (source && [...source.file.lines.keys()].some(line => line >= source.start && line <= source.end)) sources.push(source);
    else unverified = true;
  }
  return { sources, unverified };
}

function sourceHasToken(source: CitedSource, token: string) {
  const from = source.start - (DECLARATION_TOKEN.test(token) ? LINE_SLACK : ENCLOSING_LOOKBACK);
  return hasToken([...source.file.lines].filter(([line]) => line >= from && line <= source.end + LINE_SLACK)
    .map(([, text]) => text).join("\n"), token);
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
      const cited = [...target.file.lines].filter(([line, text]) => line >= target.start && line <= target.end && text.trim())
        .sort(([a], [b]) => a - b).map(([line, text]) => `${line}: ${text.trim().slice(0, MAX_LINE_CHARS)}`);
      if (!cited.length) return;
      const absentEverywhere = (token: string) => !files.some(file => hasToken([...file.lines.values()].join("\n"), token));
      // Each short-cite assertion is checked against its own explicit sources.
      // Repeating the main location adds no exemption. Another clause's inline
      // reference cannot lend its source to a misquoted call in this one.
      const clauses = clauseSpans(item.observation).map(([start, end]) => {
        const text = item.observation.slice(start, end);
        return { tokens: observedCodeTokens(text), ...clauseSources(text, target, files) };
      });
      const exactCite = target.end - target.start < EXACT_CITE_MAX_SPAN;
      const missing = exactCite ? tokens.filter(token => clauses.some(clause => clause.tokens.includes(token)
        && (clause.unverified ? absentEverywhere(token) : !clause.sources.some(source => sourceHasToken(source, token)))))
        : tokens.filter(absentEverywhere);
      const absent = missing.filter(absentEverywhere);
      const notCited = missing.filter(token => !absent.includes(token));
      const claimPath = `claims[${claimIndex}].evidence[${evidenceIndex}]`;
      if (absent.length) mismatches.push({ claimPath, location: item.location, missingTokens: absent,
        returnedLines: cited.slice(0, 2), kind: "not_in_returned_source" });
      if (notCited.length) mismatches.push({ claimPath, location: item.location, missingTokens: notCited,
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

/** Structure acceptance is necessary for recording a claim, never proof of its truth. */
export function acceptedValidatePackets(messages: ChatMessage[]): unknown[] {
  const requests = messages.flatMap(message => message.getToolCallRequests()).filter(r => r.name === VALIDATE_TOOL);
  const results = messages.flatMap(message => message.getToolCallResults());
  return requests.flatMap(request => {
    const paired = results.filter(result => String(result.toolCallId) === String(request.id));
    if (paired.length !== 1 || requests.filter(r => r.id === request.id).length !== 1) return [];
    const value = toolMemory.decodeToolResultRecord(paired[0].content).value;
    const packet = (request.arguments as { packet?: unknown } | undefined)?.packet;
    return value?.ok === true && !value.errorCode && packet !== undefined ? [packet] : [];
  });
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
    "This compares names the observation states as present only (names it denies or proposes are not compared); "
      + "it is not a fact check. Re-read the cited range and correct the observation, location "
      + "or claim from the returned text before reporting it; do not keep a call, handler or specifier the source does not show.",
  ].join("\n");
}

// A report that counts the files it checked can count a search's scanned files
// ("filesScanned": 21) as read. Only returned bodies count as checked.
const COVERAGE_CLAIM = /(?:전체|모든)[^.\n]{0,20}?(\d+)\s*개\s*(?:의\s*)?(?:소스\s*)?파일|(\d+)\s*개\s*(?:의\s*)?(?:소스\s*)?파일(?:을|를)?\s*(?:모두\s*|전부\s*)?(?:확인|검토|읽)|\b(?:all|every)\s+(\d+)\s+(?:source\s+)?files\b|\b(\d+)\s+(?:source\s+)?files\s+(?:were\s+)?(?:read|reviewed|checked)\b/iu;

/** Footer when the answer claims more checked files than returned bodies. */
export function fileCoverageNotice(messages: ChatMessage[], answer: string): string | null {
  const match = COVERAGE_CLAIM.exec(answer || "");
  const claimed = match ? Number(match.slice(1).find(Boolean)) : NaN;
  if (!Number.isFinite(claimed)) return null;
  const read = new Set(returnedSourceLines(messages).filter(file => !/^engine:\/\//i.test(file.path))
    .map(file => normalizePath(file.path))).size;
  if (claimed <= read) return null;
  return ["", "", `[조사 범위] 답변은 ${claimed}개 파일을 확인했다고 하지만, 이번 실행에서 본문이 반환된 프로젝트 파일은 ${read}개입니다. `
    + "검색 결과의 스캔 파일 수는 본문 확인이 아닙니다."].join("\n");
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
