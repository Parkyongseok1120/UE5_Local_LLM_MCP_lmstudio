import type { ChatMessage } from "@lmstudio/sdk";
import { stripInternalDataEcho } from "./internal-data-output";
import type { TextFinding } from "./engine-claim-anchoring";
import { FINDING, REJECTED } from "./review-patterns.js";
import { type EvidenceBundle, ReviewEvidenceIndex } from "./review-evidence";
import { mentionsSubject } from "./review-subject";
const { pathKey } = require("./read-ledger.js") as { pathKey(value: string): string };
import { REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE, DELIVERED_REPORT_HEADER, DELIVERED_REPORT_END } from "./review-patterns.js";
export { REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE } from "./review-patterns.js";
export { ReviewEvidenceIndex } from "./review-evidence";
const { REASONING_SEPARATOR, visibleAssistantText } = require("./continuity-text.js") as {
  REASONING_SEPARATOR: string; visibleAssistantText(text: string): string;
};

export type ReviewRow = { identifier: string; status: "found" | "cleared" | "needs_recheck"; source: "reasoning" | "visible";
  round: number; evidence: string; bundle?: EvidenceBundle; sourcePath?: string; sourceLocation?: string; references?: string[];
  contextOwner?: string; relatedDisposition?: { identifier: string; evidence: string };
  /** The claim was a guess ("never used? Possibly."): kept as a candidate, never a report obligation. */
  speculative?: true };

/** Excerpt chars the rendered register carries in all. */
const MAX_EXCERPT_CHARS = 6000;
/** Marks a bundle line; observe() never reads one back as a judgment. */
const BUNDLE_MARK = "  ↳ ";

function bundleLine(bundle: EvidenceBundle, excerpt: boolean): string {
  const where = bundle.path ? `${bundle.path}${bundle.lines ? `:${bundle.lines[0]}${bundle.lines[1] !== bundle.lines[0] ? `-${bundle.lines[1]}` : ""}` : ""}`
    + (bundle.sha ? ` @${bundle.sha.slice(0, 8)}` : "") : "";
  return BUNDLE_MARK + [where, bundle.search, excerpt && bundle.excerpt ? `code: ${bundle.excerpt}` : ""].filter(Boolean).join(" · ");
}

/** Rows kept per status in the rendered register. Found rows outlive cleared
 * ones: a dropped found row is a finding the report can miss. */
const MAX_FOUND_ROWS = 40;
const MAX_CLEARED_ROWS = 20;
/** Found rows pushed out of the rendered register still count as missing. */
const MAX_RETIRED_ROWS = 100;
// A judgment's subject is the code named nearest before its keyword: "In
// `RespawnPlayer`, ... But note: `ResetForRespawn()` ... is never called" is
// about ResetForRespawn (conversation 1792929236226, r6). Its status comes
// from the sentence that holds the keyword.
const SENTENCE_BREAK = /(?<=[.!?])\s+/u;
const CODE_SPAN = /`([^`\n]+)`/gu;
const IDENTIFIER = /[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*/u;
const BARE_IDENTIFIER = /\b[A-Za-z_]\w*::[A-Za-z_]\w*\b|\b(?:[A-Z][a-z0-9]+){2,}\w*\b|\b[a-z]+(?:[A-Z][a-z0-9]+)+\w*\b/gu;
// Names too broad to be a finding's subject: receivers, type families, macros,
// the harness's own contract ids and fixtures (r26 of the same run).
const NOT_A_SUBJECT = new Set(["this", "self", "Super", "GameMode", "GameState", "PlayerState", "PlayerController",
  "Character", "Pawn", "Actor", "Fixture", "UFUNCTION", "UPROPERTY", "UCLASS", "USTRUCT", "GENERATED_BODY",
  // Reflection specifiers name a property's metadata, not the property.
  "EditAnywhere", "EditDefaultsOnly", "EditInstanceOnly", "VisibleAnywhere", "VisibleDefaultsOnly", "BlueprintReadWrite",
  "BlueprintReadOnly", "BlueprintCallable", "BlueprintPure", "BlueprintAuthorityOnly", "Replicated", "ReplicatedUsing",
  "OnRep", "NetMulticast", "Server", "Client", "Reliable", "Unreliable", "Transient", "Category", "Meta",
  // Continuity-note schema fields cannot become missing code findings.
  "openQuestions", "rejectedHypotheses", "reviewClaims", "modelNotes", "decisions", "currentTurnFindings",
  "currentOwner", "connectionPlan", "requiredLifetime", "sharingScope", "definitionVsRuntime", "counterEvidence",
  "patternRefs", "evidenceRefs", "architectureTombstones", "architectureOmittedCount"]);
// Tool-call and evidence ids in backticks: long tokens mixing case and digits.
const OPAQUE_ID = /^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])[A-Za-z0-9_-]{20,}$/u;
const global = (pattern: RegExp) => new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : pattern.flags + "g");
const FINDING_ALL = global(FINDING);
const CITED_PATH = /([\w./\\-]+\.(?:h|hpp|cpp|cc|cs|ini))\s*[:(]\s*\d+/u;
function rejected(text: string): boolean {
  const caveat = text.replace(/\bnot\s+a\s+(?:bug|defect|problem)\b|(?:버그|결함)\s*아님/giu, "");
  return !/\b(?:not|never|cannot|can't|unresolved|incorrect|may|might|could|will|would|should|if|unless|only|but)\b|\?|미해결|기각되지|아님|아니|않|경우|조건|다만|하지만|예정|계획/iu.test(caveat)
    && (REJECTED.test(text) || /\b(?:resolved|cleared|no\s+leak|not\s+a\s+problem)\b/iu.test(text));
}
function plainRetraction(row: ReviewRow): boolean {
  const name = row.identifier.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  return new RegExp(`^\\s*(?:[-*]\\s*)?${name}(?:\\(\\))?\\s+(?:is\\s+(?:now\\s+)?(?:not a (?:bug|defect|problem)|resolved|cleared|correct|fine)|(?:기각|철회)(?:합니다|됨|되었습니다)?)[.!]?\\s*$`, "iu")
    .test(row.evidence.replace(/[`*]/gu, ""));
}

type Named = { index: number; name: string };
function namesIn(line: string): { spans: Named[]; bare: Named[] } {
  // Super::X is a call to the overridden X: the subject is X.
  const usable = (code: string | undefined) => {
    const name = code?.replace(/^Super::/u, "");
    return name && name.length >= 3 && !NOT_A_SUBJECT.has(name) && !OPAQUE_ID.test(name) ? name : null;
  };
  const keep = (items: Array<{ index: number; name: string | null }>) => items.filter((item): item is Named => Boolean(item.name));
  const spans = keep([...line.matchAll(CODE_SPAN)].filter(match => !/^[a-z]+\.[a-z]/u.test(match[1]))
    .map(match => ({ index: match.index!, name: usable(match[1].match(IDENTIFIER)?.[0]) })));
  const bare = keep([...line.replace(CODE_SPAN, match => " ".repeat(match.length)).matchAll(BARE_IDENTIFIER)]
    .map(match => ({ index: match.index!, name: usable(match[0]) })));
  return { spans, bare };
}

function subjectAt(names: { spans: Named[]; bare: Named[] }, at: number): string | null {
  for (const candidates of [names.spans, names.bare]) {
    const before = candidates.filter(item => item.index < at).at(-1);
    if (before) return before.name;
  }
  return names.spans[0]?.name || names.bare[0]?.name || null;
}

// Conversation 1794429926122: "Also they have both HitStunTimer and
// HitShakeTimer but HitShakeTimer never used? Possibly." was filed as a found
// row and started a 150 s report review. A guess is judged by the claim it
// qualifies, not by its source or by any "?" or "may" on the line: an observed
// fact with a conditional impact ("never cleared, so it may fire after
// destroy") stays a finding. Code spans and quotes are masked first.
const HEDGE_BEFORE = /\b(?:possibly|maybe|perhaps|probably|likely|presumably|apparently|seemingly|seems?|appears?|looks?\s+like|I\s+(?:think|guess|suspect|believe)|not\s+sure|unsure|unclear|whether|if|(?:might|may|could)\s+(?:also\s+)?(?:be|have\s+been))\b/iu;
const HEDGE_AFTER = /\b(?:possibly|probably|maybe|perhaps|I\s+think|I\s+guess|apparently)\b/iu;
// Hangul has no \b; "수 있다" (can happen) is an impact, not a guess.
const HEDGE_KO = /아마|추정|것\s*같|듯(?:하|함|합|싶|해)|일\s*(?:지도|수도)|인지\s*(?:확인|불확실|모르|모름)|불확실|확인\s*필요|의심/u;
const STANDALONE_HEDGE = /^\s*\(?\s*(?:possibly|maybe|perhaps|probably|not\s+sure|unsure|unclear|unverified|needs?\s+(?:to\s+)?(?:verify|check|confirm)|to\s+(?:verify|check|confirm)|아마(?:도)?|추정|불확실|확인\s*필요)\s*[.!?…)]*\s*$/iu;
const CLAUSE_BREAK = /[,;:—–(]|\s-\s/gu;

function speculativeAt(line: string, at: number): boolean {
  const masked = line.replace(CODE_SPAN, match => " ".repeat(match.length))
    .replace(/"[^"\n]*"|“[^”\n]*”/gu, match => " ".repeat(match.length));
  const ends = [...masked.matchAll(/[.!?。](?=\s|$)/gu)].map(match => match.index!);
  const start = (ends.filter(end => end < at).at(-1) ?? -1) + 1;
  const end = ends.find(end => end >= at) ?? masked.length;
  if (masked[end] === "?") return true;
  const breaks = [...masked.slice(start, end).matchAll(CLAUSE_BREAK)].map(match => start + match.index!);
  const clauseStart = (breaks.filter(position => position < at).at(-1) ?? start - 1) + 1;
  const clauseEnd = breaks.find(position => position > at) ?? end;
  const before = masked.slice(clauseStart, at), after = masked.slice(at, clauseEnd);
  if (HEDGE_BEFORE.test(before) || HEDGE_AFTER.test(after) || HEDGE_KO.test(before + after)) return true;
  const next = ends.find(position => position > end) ?? masked.length;
  return end < masked.length && STANDALONE_HEDGE.test(masked.slice(end + 1, next + 1));
}

/** Each judgment of a line as [subject, status]: one per finding keyword, its
 * sentence deciding the status. A line that only rejects clears its leading
 * subject ("**Issue 9: `TakeDamage` — ... not a bug"). A later sentence about
 * the same subject replaces an earlier one ("bug: ... Later verification: not a bug"). */
function judgments(line: string): Array<[string, ReviewRow["status"], string, boolean]> {
  const out = new Map<string, [ReviewRow["status"], string, boolean]>(), lineNames = namesIn(line);
  let offset = 0;
  for (const sentence of line.split(SENTENCE_BREAK)) {
    const start = line.indexOf(sentence, offset); offset = start + sentence.length;
    const names = namesIn(sentence);
    const isRejected = rejected(sentence);
    if (!FINDING.test(sentence)) {
      const subject = isRejected ? names.spans[0]?.name || names.bare[0]?.name || lineNames.spans[0]?.name : null;
      if (subject) { out.delete(subject); out.set(subject, ["cleared", sentence, false]); }
      continue;
    }
    for (const match of sentence.matchAll(FINDING_ALL)) {
      const subject = isRejected ? subjectAt(names, match.index!) || subjectAt(lineNames, start + match.index!)
        : subjectAt(lineNames, start + match.index!);
      if (subject) { out.delete(subject); out.set(subject, [isRejected ? "cleared" : "found", isRejected ? sentence : line,
        !isRejected && speculativeAt(line, start + match.index!)]); }
    }
  }
  return [...out].map(([identifier, [status, text, speculative]]) => [identifier, status, text, speculative]);
}

/** Execution-owned working notes. Neither a proof ledger nor an authority source. */
export class ReviewRegister {
  private readonly entries = new Map<string, ReviewRow>();
  private readonly retired = new Map<string, ReviewRow>();
  get rows(): ReviewRow[] { return [...this.entries.values()]; }
  observe(messages: readonly ChatMessage[], round: number, privateParts: readonly string[] = []) {
    for (const message of messages) {
      if (!message.isAssistantMessage()) continue;
      const raw = message.getText(), visible = visibleAssistantText(raw);
      const parts: Array<[ReviewRow["source"], string]> = visible === raw ? [["visible", raw]]
        : [["reasoning", raw.slice(0, raw.indexOf(REASONING_SEPARATOR))], ["visible", visible]];
      for (const [source, text] of parts) for (const line of stripInternalDataEcho(text, privateParts).split(/\r?\n/u)) {
        if (/^\s*(?:Runtime |\[Prior assistant|\[Delivered report|[-*] \[(?:found|cleared)\]|↳ )/u.test(line)) continue;
        for (const [identifier, status, text, speculative] of judgments(line)) {
          const names = namesIn(text);
          const citation = CITED_PATH.exec(text);
          this.record({ identifier, status, source, round, evidence: text.trim().slice(0, 240), ...(speculative ? { speculative } : {}),
            sourcePath: citation?.[1], sourceLocation: citation?.[0],
            contextOwner: /\b[Ii]n\s+`?([A-Z][A-Za-z0-9_]*)`?\b/u.exec(text)?.[1],
            references: [...new Set([...names.spans, ...names.bare].map(item => item.name).concat(
              [...text.matchAll(CODE_SPAN)].flatMap(span => [...span[1].matchAll(/\b[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*/gu)].map(m => m[0]))))].slice(0, 64) }, text);
        }
      }
    }
  }

  private claimKey(row: ReviewRow): string {
    const path = row.sourcePath || CITED_PATH.exec(row.evidence)?.[1];
    return `${row.identifier}\u0000${path ? pathKey(path) : row.contextOwner ? `owner:${row.contextOwner}` : "unqualified"}`;
  }

  private record(row: ReviewRow, fullEvidence: string) {
    // A later guess about a claim already stated outright does not weaken it.
    const prior = this.entries.get(this.claimKey(row)) || this.retired.get(this.claimKey(row));
    if (row.speculative && prior && prior.status !== "cleared" && !prior.speculative) return;
    if (row.status === "cleared") {
      // An explicit member retraction can name a call in an earlier claim
      // filed under its caller. Co-occurrence is not proof that their claims
      // are equivalent, so preserve this as an identity question, never clear.
      const related = [...this.entries.values(), ...this.retired.values()].filter(other => {
        if (other.status === "cleared" || other.identifier === row.identifier || !other.references?.includes(row.identifier)) return false;
        const path = other.sourcePath || other.bundle?.path;
        if (!path) return false;
        if (row.sourcePath) return pathKey(row.sourcePath) === pathKey(path);
        const owner = path.replace(/\\/gu, "/").split("/").pop()!.replace(/\.[^.]+$/u, "");
        if (row.contextOwner) return row.contextOwner === owner;
        return namesIn(fullEvidence).spans.some(name => name.name === owner)
          || new RegExp(`\\b${owner.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}\\b`, "u").test(fullEvidence);
      });
      for (const other of related) {
        other.status = "needs_recheck";
        other.relatedDisposition = { identifier: row.identifier, evidence: row.evidence };
        other.round = row.round;
      }
    }
    // A retraction that names no file reaches the file-qualified claims of the
    // same subject: one such claim is that claim; several are ambiguous and
    // need a recheck rather than staying confirmed or being cleared wholesale.
    if (row.status !== "found" && !row.sourcePath) {
      let qualified = [...this.entries.values(), ...this.retired.values()].filter(other => other.identifier === row.identifier
        && other.status !== "cleared" && !this.claimKey(other).endsWith("\u0000unqualified"));
      const scoped = qualified.filter(other => {
        const owner = other.sourcePath?.replace(/\\/gu, "/").split("/").pop()?.replace(/\.[^.]+$/u, "");
        if (row.contextOwner) return owner === row.contextOwner;
        return owner && new RegExp(`\\b${owner.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}\\b`, "u").test(fullEvidence);
      });
      if (row.contextOwner || scoped.length) qualified = scoped;
      const unambiguous = Boolean(row.contextOwner || scoped.length || plainRetraction(row));
      for (const other of qualified) {
        other.status = qualified.length === 1 && unambiguous ? row.status : "needs_recheck"; other.round = row.round;
        if (!unambiguous) other.relatedDisposition = { identifier: row.identifier, evidence: row.evidence };
      }
    }
    // Reinsertion keeps the bounded register ordered by latest observation.
    const key = this.claimKey(row);
    this.entries.delete(key); this.retired.delete(key);
    this.entries.set(key, row);
    for (const [status, limit] of [["found", MAX_FOUND_ROWS], ["cleared", MAX_CLEARED_ROWS]] as const) {
      const rows = this.rows.filter(entry => status === "found" ? entry.status !== "cleared" : entry.status === status);
      for (const oldest of rows.slice(0, Math.max(0, rows.length - limit))) {
        this.entries.delete(this.claimKey(oldest));
        if (oldest.status !== "cleared") this.retired.set(this.claimKey(oldest), oldest);
      }
    }
    while (this.retired.size > MAX_RETIRED_ROWS) this.retired.delete(this.retired.keys().next().value!);
  }
  /** Judgments keep their observed version. New source is a recheck obligation,
   * never fresh support for an old found/cleared judgment. */
  attach(index: ReviewEvidenceIndex) {
    for (const row of [...this.entries.values(), ...this.retired.values()]) {
      row.bundle ||= index.bundleFor(row.identifier, `${row.sourceLocation || ""} ${row.evidence}`) ?? undefined;
      const current = row.bundle?.path ? index.shaOf(pathKey(row.bundle.path), row.bundle.sourceKey) : null;
      if (row.bundle) {
        if (index.superseded(row.bundle) || current && current !== row.bundle.sha) row.status = "needs_recheck";
        continue;
      }
    }
  }

  lines(): string[] {
    // Excerpts go first where they matter (found rows, newest first) within a
    // bounded register; locations and search scope are always kept.
    let excerptChars = 0;
    const withExcerpt = new Set(this.rows.filter(row => row.status === "found").reverse().filter(row => {
      const size = row.bundle?.excerpt?.length ?? 0;
      if (!size || excerptChars + size > MAX_EXCERPT_CHARS) return false;
      excerptChars += size; return true;
    }));
    return this.rows.flatMap(row => [`- [${row.status}] ${row.identifier} (${row.source}, round ${row.round}${row.speculative ? ", speculative" : ""}): ${reviewRowText(row)}`,
      ...(row.bundle ? [bundleLine(row.bundle, withExcerpt.has(row))] : [])]);
  }

  /** Working notes cannot certify that every subject in a file was reviewed. */
  closedFileFacts(_key: string, _sha: string | null): string[] | null { return null; }
  fact(): string | null {
    return this.entries.size ? [REVIEW_REGISTER_HEADER, ...this.lines(), REVIEW_REGISTER_GUIDANCE].join("\n") : null;
  }
  findings(): TextFinding[] {
    return this.rows.filter(row => row.status === "found").map(row => ({ label: row.identifier,
      severity: /\bP[0-3]\b/u.exec(row.evidence)?.[0] || "unrated", text: row.evidence }));
  }
  /** The open review candidates at their latest status: found or needing a
   * recheck, including rows the bounded register pushed out of the model
   * input. Read-only; a renderer does not judge them again or count them as
   * confirmed defects. */
  candidates(): ReviewRow[] {
    return [...this.retired.values(), ...this.rows].filter(row => row.status !== "cleared");
  }
  /** Found rows the answer does not name, including ones the bounded register
   * already pushed out of the model input. A guess is a candidate, not an
   * omission: it never starts a review on its own. */
  missing(answer: string): ReviewRow[] {
    const rows = [...this.retired.values(), ...this.rows];
    return rows.filter(row => {
      if (row.status === "cleared" || row.speculative) return false;
      if (row.status === "needs_recheck") return !answer.split(/\r?\n/u).some(line => mentionsSubject(line, row, rows)
        && /미확인|미검증|재검토|needs_recheck|unverified|not verified|recheck|withdraw/iu.test(line));
      return Boolean(row.bundle?.path) && !answer.split(/\r?\n/u).some(line => mentionsSubject(line, row, rows));
    });
  }
}

/** Header of the delivered report a review supplement follows. */
export { DELIVERED_REPORT_HEADER };

export function deliveredReportFact(answer: string): string {
  return `${DELIVERED_REPORT_HEADER}
${answer.trim()}
${DELIVERED_REPORT_END}`;
}

export function reportReviewFact(missing: readonly ReviewRow[], contracts: readonly { factId: string; statement: string }[]): string {
  return ["Runtime evidence observation: report review obligations (working notes, not verified facts)",
    ...missing.flatMap(row => [`- Missing ${row.identifier}: ${reviewRowText(row)}${row.status === "needs_recheck"
      ? ` [needs_recheck; observed sha ${row.bundle?.sha || "unknown"}; newer source cannot validate this old judgment]` : ""}`,
      ...(row.bundle ? [bundleLine(row.bundle, true)] : [])]),
    ...contracts.map(gap => `- Engine contract ${gap.factId}: ${gap.statement}`)].join("\n");
}

export function reportReviewNotice(missing: readonly ReviewRow[],
  cause: "budget" | "empty" | "duplicate" | "restatement" | "incomplete" | "unresolved" = "budget"): string | null {
  const why = cause === "duplicate" ? "중복된 출력을 제외한 뒤 새로 전달된 검토 보완 내용이 없습니다."
    : cause === "empty" ? "검토 보완에서 새로 전달된 본문이 없습니다."
    : cause === "restatement" ? "검토 보완이 앞선 보고서를 다시 작성한 내용이어서 표시하지 않았습니다."
    : cause === "incomplete" ? "검토 보완이 끝나지 않아(출력 한도·중지·오류) 표시하지 않았습니다."
    : cause === "unresolved" ? "전달된 검토 보완이 일부 항목을 다루지 않았습니다."
    : "입력 예산 때문에 검토 보완을 생성하지 못했습니다.";
  const where = (row: ReviewRow) => row.bundle?.lines ? ` (${row.bundle.path.split("/").pop()}:${row.bundle.lines[0]})` : "";
  return missing.length ? `\n\n[보고 검토 · 누락] ${why} 다음 작업 메모의 최종 처리 상태를 연결하지 못했습니다. 확인된 결함으로 간주할 수 없습니다.\n`
    + missing.map(row => `- ${row.identifier}${where(row)}: ${reviewRowText(row)}`).join("\n") : null;
}

function reviewRowText(row: ReviewRow, maxChars?: number): string {
  if (maxChars !== undefined) {
    if (!row.relatedDisposition) return row.evidence.slice(0, maxChars);
    const prefix = `판단 연결 미확인 · 관련 정정(${row.relatedDisposition.identifier.slice(0, 40)}). 원가설(미검증): `;
    const separator = " / 정정: ";
    const each = Math.max(0, Math.floor((maxChars - prefix.length - separator.length) / 2));
    const excerpt = (text: string) => text.length <= each ? text : text.slice(0, Math.max(0, each - 1)) + "…";
    return prefix + excerpt(row.evidence) + separator + excerpt(row.relatedDisposition.evidence);
  }
  return row.relatedDisposition
    ? `판단 연결 미확인. 원래 미검증 가설: ${row.evidence} 관련 정정(${row.relatedDisposition.identifier}): ${row.relatedDisposition.evidence}`
    : row.evidence;
}

/** Open review candidates at the head of a partial report: working notes,
 * newest first, never counted or presented as confirmed defects. Bounded in
 * rows and characters; what does not fit is counted, not dropped silently. */
export function reviewCandidateLines(rows: readonly ReviewRow[], maxRows = 12, maxChars = 2400): string[] {
  if (!rows.length) return [];
  const shown: string[] = [];
  let chars = 0;
  for (const row of [...rows].reverse()) {
    const where = row.bundle?.path ? ` (${row.bundle.path.split("/").pop()}${row.bundle.lines ? `:${row.bundle.lines[0]}` : ""}`
      + `${row.bundle.sha ? ` @${row.bundle.sha.slice(0, 8)}` : ""})` : "";
    const line = `- ${row.identifier}${where} [${row.speculative ? "추측" : row.status === "found" ? "미검증 발견" : "재확인 필요"}]: ${reviewRowText(row, 160)}`;
    if (shown.length >= maxRows || chars + line.length > maxChars) break;
    shown.push(line); chars += line.length;
  }
  const omitted = rows.length - shown.length;
  return ["미검증 검토 후보 (작업 메모이며 확인된 결함이 아님):", ...shown, ...(omitted > 0 ? [`- 외 ${omitted}개는 길이 제한으로 생략`] : []), ""];
}
