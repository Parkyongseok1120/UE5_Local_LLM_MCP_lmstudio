import { subjectLeaf, subjectToken } from "./review-subject";

/** What a report review round may publish. The review is held until its
 * generation ends; this decides once, and the screen, the saved transcript
 * and the next model input all use the same text.
 * Conversation 1794429926122: the review rewrote the delivered 5,019-char
 * report as 5,430 chars on the same seven topics, reworded so no exact echo
 * matched, and the user saw the report twice. */
export type ReviewAcceptanceDecision = "accepted" | "salvaged" | "rejected" | "withheld";
export type RestatementSignals = { lengthRatio: number; lineOverlap: number; identifierCoverage: number;
  headingMatches: number; reportHeadings: number };
export type ReviewAcceptance = { decision: ReviewAcceptanceDecision; text: string; generatedChars: number;
  reason?: "restatement" | "incomplete"; signals?: RestatementSignals; salvagedBlocks?: number };

type Block = { heading: string | null; lines: string[] };

const HEADING = /^\s{0,3}(?:#{1,6}\s+\S|\*\*[^*\n]+\*\*\s*:?\s*$)/u;
const FENCE = /^\s{0,3}(?:```|~~~)/u;
// No \b after Hangul: \b is ASCII-word based.
const SUPPLEMENT_HEADING = /^\s{0,3}#{1,6}\s*(?:검토\s*보완|review\s+supplement)(?=\s|$|[:(])/imu;
const CORRECTION = /정정|철회|기각|번복|잘못\s*(?:기재|판단|적|썼)|\bretract\w*|\bwithdr[ae]w\w*|\bcorrect(?:ion|ed)\b/iu;
const NO_PROBLEM = /문제\s*(?:없|아님)|정상(?:입니다|이다|임|으로)?(?=\s|[.,)]|$)|(?:버그|결함)\s*아님|\bnot\s+a\s+(?:bug|defect|problem)\b|\bno\s+(?:issue|defect)s?\b/iu;
const SEVERITY = /\bP[0-3]\b/gu;
const SIGNIFICANT_LINE_CHARS = 12;
/** A report shorter than this is too small to tell a rewrite from a supplement. */
const MIN_REPORT_CHARS = 400;
const SALVAGE_NOTE = "(검토 단계가 기존 보고서를 다시 작성해, 겹치는 내용은 생략하고 달라진 부분만 옮겼습니다.)";

const norm = (line: string) => line.replace(/\s+/gu, " ").trim();
const significant = (line: string) => norm(line).length >= SIGNIFICANT_LINE_CHARS;

/** Sections by heading; a text with fewer than three falls back to paragraphs.
 * Fenced code is never split. */
function blocks(text: string): Block[] {
  const lines = text.split(/\r?\n/u);
  const split = (boundary: (line: string) => "heading" | "blank" | null) => {
    const out: Block[] = [];
    let current: Block = { heading: null, lines: [] }, fenced = false;
    const flush = () => { if (current.lines.some(line => line.trim())) out.push(current); };
    for (const line of lines) {
      const kind = fenced ? null : boundary(line);
      if (FENCE.test(line)) fenced = !fenced;
      if (kind === "heading") { flush(); current = { heading: norm(line), lines: [line] }; continue; }
      if (kind === "blank") { flush(); current = { heading: null, lines: [] }; continue; }
      current.lines.push(line);
    }
    flush();
    return out;
  };
  const sections = split(line => HEADING.test(line) ? "heading" : null);
  return sections.length >= 3 ? sections : split(line => HEADING.test(line) ? "heading" : line.trim() ? null : "blank");
}

/** Distinctive code names: CamelCase, snake_case, qualified names and file names. */
function identifiers(text: string): Set<string> {
  const out = new Set<string>();
  for (const match of text.matchAll(/[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*(?:\.(?:h|hpp|cpp|cc|cs|ini|js|ts|py))?/gu)) {
    const token = match[0];
    if (token.length < 4) continue;
    if (/\.(?:h|hpp|cpp|cc|cs|ini|js|ts|py)$/u.test(token) || /::|_|[a-z][A-Z]|[A-Z]{2}[a-z]/u.test(token)) out.add(token);
  }
  return out;
}

function headingWords(heading: string): Set<string> {
  return new Set(heading.replace(/^[#*\s]+|[*\s]+$/gu, "").replace(/^\d+[.)]\s*/u, "")
    .split(/[\s/·,–—:()\-]+/u).map(word => word.toLowerCase()).filter(word => word.length >= 2));
}

function similarHeadings(left: string, right: string): boolean {
  const a = headingWords(left), b = headingWords(right);
  if (!a.size || !b.size) return false;
  const shared = [...a].filter(word => b.has(word)).length;
  const number = (heading: string) => /^[#*\s]*(\d+)[.)]/u.exec(heading)?.[1];
  return shared / new Set([...a, ...b]).size >= 0.4 || Boolean(number(left) && number(left) === number(right) && shared >= 1);
}

export function restatementSignals(candidate: string, report: string, subjects: readonly string[] = []): RestatementSignals {
  const reportLines = new Set(report.split(/\r?\n/u).filter(significant).map(norm));
  const candidateLines = candidate.split(/\r?\n/u).filter(significant).map(norm);
  const lineOverlap = candidateLines.length ? candidateLines.filter(line => reportLines.has(line)).length / candidateLines.length : 0;
  const excluded = new Set(subjects.flatMap(subject => [subject, subjectLeaf(subject)]));
  const reportIds = [...identifiers(report)].filter(id => !excluded.has(id) && !excluded.has(subjectLeaf(id)));
  const candidateIds = identifiers(candidate);
  const identifierCoverage = reportIds.length >= 6 ? reportIds.filter(id => candidateIds.has(id)).length / reportIds.length : 0;
  const reportHeadings = blocks(report).map(block => block.heading).filter((heading): heading is string => Boolean(heading));
  const candidateHeadings = blocks(candidate).map(block => block.heading).filter((heading): heading is string => Boolean(heading));
  const headingMatches = reportHeadings.filter(heading => candidateHeadings.some(other => similarHeadings(heading, other))).length;
  return { lengthRatio: report.trim().length ? candidate.trim().length / report.trim().length : 0,
    lineOverlap, identifierCoverage, headingMatches, reportHeadings: reportHeadings.length };
}

/** A rewrite of the delivered report, as opposed to a supplement that quotes
 * parts of it. Headings alone never decide: a correction may quote one. */
export function isRestatement(signals: RestatementSignals, reportChars: number): boolean {
  if (reportChars < MIN_REPORT_CHARS || signals.lengthRatio < 0.4) return false;
  return signals.lineOverlap >= 0.6 || signals.identifierCoverage >= 0.6
    || signals.reportHeadings >= 3 && signals.headingMatches >= Math.max(3, Math.ceil(signals.reportHeadings * 0.6))
      && signals.identifierCoverage >= 0.4;
}

function mentions(text: string, subject: string): boolean {
  if (/^[A-Za-z_][\w:]*$/u.test(subject)) return subjectToken(subject).test(text) || subjectToken(subjectLeaf(subject)).test(text);
  const file = subject.replace(/\\/gu, "/").split("/").pop() || subject;
  return text.includes(subject) || file.length >= 4 && text.includes(file);
}

const severities = (text: string) => [...new Set(text.match(SEVERITY) || [])].sort().join(",");

/** The report section a rewritten section restates: same heading, else the most shared code names. */
function counterpart(block: Block, report: readonly Block[]): Block | null {
  if (block.heading) {
    const byHeading = report.find(other => other.heading && similarHeadings(block.heading!, other.heading));
    if (byHeading) return byHeading;
  }
  const ids = identifiers(block.lines.join("\n"));
  let best: Block | null = null, bestShared = 0;
  for (const other of report) {
    const shared = [...identifiers(other.lines.join("\n"))].filter(id => ids.has(id)).length;
    if (shared > bestShared) { best = other; bestShared = shared; }
  }
  return ids.size && bestShared / ids.size >= 0.5 ? best : null;
}

/** Sections of a rewrite that carry something the report does not: a named
 * review obligation, an explicit correction, a changed severity or verdict,
 * or (for a near-verbatim copy) any changed line, with what it dropped. */
function salvageBlocks(candidate: string, report: string, subjects: readonly string[], verbatim: boolean): string[] {
  const reportBlocks = blocks(report);
  const reportLines = new Set(report.split(/\r?\n/u).filter(significant).map(norm));
  const kept: string[] = [];
  for (const block of blocks(candidate)) {
    const text = block.lines.join("\n").trim();
    const other = counterpart(block, reportBlocks), otherText = other?.lines.join("\n") || "";
    const named = subjects.some(subject => mentions(text, subject));
    const corrected = CORRECTION.test(text) && !CORRECTION.test(otherText);
    const flipped = Boolean(other) && (severities(text) !== severities(otherText) && Boolean(severities(text) || severities(otherText))
      || NO_PROBLEM.test(text) !== NO_PROBLEM.test(otherText));
    let changed = false, dropped: string[] = [];
    if (verbatim) {
      const own = new Set(block.lines.filter(significant).map(norm));
      changed = [...own].some(line => !reportLines.has(line));
      dropped = other ? other.lines.filter(line => significant(line) && !own.has(norm(line)) && !FENCE.test(line)) : [];
      changed ||= dropped.length > 0;
    }
    if (!(named || corrected || flipped || changed)) continue;
    const removal = dropped.slice(0, 3).map(line => `> 앞선 보고서에서 바뀌거나 빠진 줄: ${norm(line).slice(0, 160)}`);
    kept.push([text, ...removal].join("\n"));
  }
  return kept;
}

export function acceptReviewSupplement(input: { candidate: string; report: string; subjects?: readonly string[]; complete: boolean }): ReviewAcceptance {
  const { candidate, report, complete } = input, subjects = input.subjects || [];
  const generatedChars = candidate.length;
  if (!candidate.trim()) return { decision: "accepted", text: candidate, generatedChars };
  // A cut or stopped review cannot be judged from part of its text.
  if (!complete) return { decision: "withheld", text: "", generatedChars, reason: "incomplete" };
  const signals = restatementSignals(candidate, report, subjects);
  if (!isRestatement(signals, report.trim().length)) return { decision: "accepted", text: candidate, generatedChars, signals };
  const heading = SUPPLEMENT_HEADING.exec(candidate);
  if (heading) {
    const section = candidate.slice(heading.index).trim();
    if (!isRestatement(restatementSignals(section, report, subjects), report.trim().length))
      return { decision: "salvaged", text: `\n\n${section}`, generatedChars, reason: "restatement", signals, salvagedBlocks: 1 };
  }
  const kept = salvageBlocks(candidate, report, subjects, signals.lineOverlap >= 0.6);
  if (!kept.length) return { decision: "rejected", text: "", generatedChars, reason: "restatement", signals };
  return { decision: "salvaged", text: `\n\n## 검토 보완\n${SALVAGE_NOTE}\n\n${kept.join("\n\n")}`, generatedChars,
    reason: "restatement", signals, salvagedBlocks: kept.length };
}
