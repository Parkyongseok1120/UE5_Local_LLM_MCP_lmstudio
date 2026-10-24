import type { ChatMessage } from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";
import { bundledEngineClaimFacts } from "./generated-design-guidance";
import { returnedSourceLines } from "./quote-anchoring";

// A P0/P1 claim can quote the project correctly and still rest on a wrong
// engine rule ("a Client RPC reaches every client"). The packet validator
// checks structure and the quote check compares names, so neither sees it.
// When a high-severity claim's wording touches a catalogued engine fact and
// this execution returned none of that fact's engine source, the model is
// handed the contract statement and the range to read. Advisory: nothing here
// blocks, rewrites a result, or decides whether the claim is right.

const LOOKUP_TOOL = "unreal_symbol_lookup";
const GATED_SEVERITIES = new Set(["P0", "P1"]);
const ENGINE_URI = /^engine:\/\//i;
const MAX_REPORTED = 3;

type EngineClaimFact = (typeof bundledEngineClaimFacts)[number];
export type EngineClaimGap = { claimPath: string; severity: string; factId: string; contractSet: string;
  engineVersions: readonly string[]; statement: string; evidence: string[] };

const catalogue = bundledEngineClaimFacts.map(fact => ({ fact, markers: fact.claimMarkers.map(marker => new RegExp(marker, "iu")) }));

const enginePath = (value: string) => value.replace(ENGINE_URI, "").replace(/\\/g, "/").replace(/:\d+(?:-\d+)?$/, "").toLowerCase();
const member = (symbol: string) => symbol.split("::").pop() || symbol;
const hasToken = (text: string, token: string) => new RegExp(`(^|[^\\w])${token}($|[^\\w])`).test(text);
const evidenceLabel = (ref: EngineClaimFact["evidenceRefs"][number]) => `${ref.path} (${ref.symbol})`;

/** Engine source this execution actually returned: engine:// file lines, and
 * the contract ids an engine_local lookup attached to its result. */
export function returnedEngineEvidence(messages: ChatMessage[]) {
  const files = returnedSourceLines(messages).filter(file => ENGINE_URI.test(file.path))
    .map(file => ({ path: enginePath(file.path), text: [...file.lines.values()].join("\n") }));
  const names = new Map<string, string>();
  const contractIds = new Set<string>();
  for (const message of messages) {
    for (const request of message.getToolCallRequests()) names.set(String(request.id || ""), request.name);
    for (const result of message.getToolCallResults()) {
      if (names.get(String(result.toolCallId || "")) !== LOOKUP_TOOL) continue;
      const value = toolMemory.decodeToolResultRecord((result as { content?: unknown }).content).value as Record<string, any> | undefined;
      const items = value?.relatedContracts?.items;
      if (Array.isArray(items)) for (const item of items) if (typeof item?.id === "string") contractIds.add(item.id);
    }
  }
  return { files, contractIds };
}
type ReturnedEngineEvidence = ReturnType<typeof returnedEngineEvidence>;

/** The text a claim asserts: the claim, its evidence observations and its
 * behavior path. Counter-evidence argues against the claim, so it is left out. */
function claimText(claim: Record<string, any>): string {
  const parts: string[] = [];
  if (typeof claim.claim === "string") parts.push(claim.claim);
  if (Array.isArray(claim.evidence)) for (const item of claim.evidence)
    if (typeof item?.observation === "string") parts.push(item.observation);
  if (Array.isArray(claim.behaviorPath)) for (const step of claim.behaviorPath)
    if (step && typeof step === "object") for (const value of Object.values(step)) if (typeof value === "string") parts.push(value);
  return parts.join("\n");
}

const fileRead = (evidence: ReturnedEngineEvidence, path: string) => {
  const wanted = enginePath(path);
  return evidence.files.filter(file => file.path === wanted || file.path.endsWith(`/${wanted}`) || wanted.endsWith(`/${file.path}`));
};

/** A fact is backed when its evidence member was returned from its file, when
 * an engine_local lookup attached it, or when the claim cites framework source
 * that this execution returned. */
function backed(fact: EngineClaimFact, claim: Record<string, any>, evidence: ReturnedEngineEvidence): boolean {
  if (evidence.contractIds.has(fact.id)) return true;
  if (fact.evidenceRefs.some(ref => fileRead(evidence, ref.path).some(file => hasToken(file.text, member(ref.symbol))))) return true;
  return Array.isArray(claim.evidence) && claim.evidence.some((item: any) => item?.kind === "framework_source"
    && typeof item.location === "string" && fileRead(evidence, item.location).length > 0);
}

/** P0/P1 claims of one packet whose wording touches an engine fact that no
 * returned engine source backs. Only facts of the resolved project engine apply. */
export function packetEngineClaimGaps(packet: unknown, evidence: ReturnedEngineEvidence, engine: string): EngineClaimGap[] {
  const claims = (packet as { claims?: unknown })?.claims;
  if (!Array.isArray(claims)) return [];
  const gaps: EngineClaimGap[] = [];
  claims.forEach((claim, index) => {
    if (!claim || typeof claim !== "object" || !GATED_SEVERITIES.has(String(claim.severity))) return;
    const text = claimText(claim);
    for (const { fact, markers } of catalogue) {
      if (fact.engine !== engine || !markers.some(marker => marker.test(text)) || backed(fact, claim, evidence)) continue;
      gaps.push({ claimPath: `claims[${index}]`, severity: String(claim.severity), factId: fact.id, contractSet: fact.contractSet,
        engineVersions: fact.engineVersions, statement: fact.statement, evidence: fact.evidenceRefs.map(evidenceLabel) });
    }
  });
  return gaps;
}

// A report written without a packet still grades findings in its headings, and
// progress text shown before it can state a finding as confirmed. The same
// facts apply to both; a heading that rejects its finding makes no claim.
const SEVERITY = /\bP([0-3])\b/;
const HEADING = /^\s{0,3}#{1,6}\s/;
const REJECTED = /기각|철회|버그\s*아님|결함\s*아님|\bnot\s+a\s+(?:bug|defect)\b|\brejected\b|\bretract/iu;
const CONFIRMED = /\b(?:confirmed|verified)\b|확정|확인했|확인된\s*(?:버그|결함)/iu;
const FINDING = /\bbug\b|\bdefect\b|버그|결함/iu;
export type TextFinding = { label: string; severity: string; text: string };

/** Graded findings of an answer: each heading section whose heading names a severity. */
export function gradedSections(answer: string): TextFinding[] {
  const sections: Array<{ heading: string; lines: string[] }> = [];
  for (const line of String(answer || "").split(/\r?\n/)) {
    if (HEADING.test(line)) sections.push({ heading: line.trim(), lines: [line] });
    else sections.at(-1)?.lines.push(line);
  }
  return sections.flatMap(({ heading, lines }) => {
    const severity = SEVERITY.exec(heading);
    return severity && !REJECTED.test(heading)
      ? [{ label: heading.replace(/^#+\s*/, "").slice(0, 80), severity: `P${severity[1]}`, text: lines.join("\n") }] : [];
  });
}

/** Visible lines before the final answer that state a finding as confirmed. */
export function confirmedProgressStatements(visible: readonly ChatMessage[], finalText: string): TextFinding[] {
  const found: TextFinding[] = [];
  for (const message of visible) {
    if (!message.isAssistantMessage()) continue;
    const text = message.getText();
    if (!text || (finalText && text.trim() === finalText.trim())) continue;
    for (const line of text.split(/\r?\n/)) {
      if (!CONFIRMED.test(line) || !(SEVERITY.test(line) || FINDING.test(line)) || REJECTED.test(line)) continue;
      found.push({ label: line.trim().slice(0, 80), severity: SEVERITY.exec(line) ? `P${SEVERITY.exec(line)![1]}` : "unrated", text: line });
    }
  }
  return found;
}

/** Engine-fact gaps in free-text findings, by the same markers and backing rules as packets. */
export function textEngineClaimGaps(findings: readonly TextFinding[], evidence: ReturnedEngineEvidence, engine: string,
  kind: "report" | "progress"): EngineClaimGap[] {
  const gaps: EngineClaimGap[] = [];
  for (const finding of findings) {
    for (const { fact, markers } of catalogue) {
      if (fact.engine !== engine || !markers.some(marker => marker.test(finding.text)) || backed(fact, {}, evidence)) continue;
      gaps.push({ claimPath: `${kind} "${finding.label}"`, severity: finding.severity, factId: fact.id, contractSet: fact.contractSet,
        engineVersions: fact.engineVersions, statement: fact.statement, evidence: fact.evidenceRefs.map(evidenceLabel) });
    }
  }
  return gaps;
}

/** Footer for findings the delivered report grades, and for progress claims it dropped. */
export function reportEngineClaimNotice(report: EngineClaimGap[], progress: EngineClaimGap[]): string | null {
  const retracted = progress.filter(gap => !report.some(r => r.factId === gap.factId));
  if (!report.length && !retracted.length) return null;
  const firstSentence = (statement: string) => statement.split(/(?<=\.)\s/)[0];
  const lines = report.slice(0, MAX_REPORTED).map(gap => `- ${gap.claimPath} (${gap.severity}) ↔ \`${gap.factId}\`: ${firstSentence(gap.statement)}`);
  const dropped = retracted.slice(0, MAX_REPORTED).map(gap => `- 중간 진행 문장 ${gap.claimPath} ↔ \`${gap.factId}\`: 최종 보고에는 남지 않았습니다.`);
  return ["", "", "[엔진 동작 근거 · 보고서] 아래 지적은 엔진 동작에 기대지만, 이번 실행은 해당 엔진 소스를 반환하지 않았습니다. "
    + "엔진 계약 문장과 표현을 대조한 결과이며 사실 판정이 아닙니다.", ...lines, ...dropped].join("\n");
}

/** Runtime fact for the next input, or null when every gated claim is backed. */
export function engineClaimFact(gaps: EngineClaimGap[]): string | null {
  if (!gaps.length) return null;
  const listed = gaps.slice(0, MAX_REPORTED).map(gap => `- ${gap.claimPath} (${gap.severity}) touches \`${gap.factId}\` `
    + `(${gap.contractSet}, engine fact checked on ${gap.engineVersions.join(", ")} source): ${gap.statement} `
    + `Engine source: ${gap.evidence.join("; ")}`);
  const more = gaps.length > MAX_REPORTED ? [`- ${gaps.length - MAX_REPORTED} more not listed`] : [];
  return [
    `Runtime evidence observation: the last evidence packet rates ${gaps.length} claim(s) P0/P1 in wording that depends on engine behavior, `
      + "and no engine source returned in this execution backs it.",
    ...listed, ...more,
    "This names a catalogued engine fact the claim's wording touches; it is not a verdict on the claim, and a passed packet "
      + "validation does not check it. Before reporting the claim at P0/P1, read that engine range on the project's engine "
      + "(read_symbol or read_file_range on engine://...) and reconcile the claim with it; otherwise report it as suspected "
      + "with the engine behavior named as unverified.",
  ].join("\n");
}

/** Footer for the delivered answer when the last packet's gaps remain.
 * The answer text itself is never rewritten. */
export function engineClaimNotice(gaps: EngineClaimGap[]): string | null {
  if (!gaps.length) return null;
  const listed = gaps.slice(0, MAX_REPORTED).map(gap => `- ${gap.claimPath} (${gap.severity}) ↔ \`${gap.factId}\`: ${gap.evidence[0]}`);
  const more = gaps.length > MAX_REPORTED ? [`- 외 ${gaps.length - MAX_REPORTED}건`] : [];
  return ["", "", `[엔진 동작 근거] 마지막 근거 패킷의 P0/P1 지적 ${gaps.length}건이 엔진 동작에 기대지만, 이번 실행은 해당 엔진 소스를 반환하지 않았습니다. `
    + "엔진 계약 문장과 표현을 대조한 결과이며 사실 판정이 아닙니다. 해당 지적은 엔진 구현을 확인한 뒤 판단해야 합니다.", ...listed, ...more].join("\n");
}
