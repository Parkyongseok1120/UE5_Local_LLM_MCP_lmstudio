import type { ChatMessage } from "@lmstudio/sdk";
import { stripInternalDataEcho } from "./internal-data-output";
import type { TextFinding } from "./engine-claim-anchoring";
import { FINDING, REJECTED } from "./review-patterns.js";
import { type EvidenceBundle, ReviewEvidenceIndex } from "./review-evidence";
const { pathKey } = require("./read-ledger.js") as { pathKey(value: string): string };
import { REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE } from "./review-patterns.js";
export { REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE } from "./review-patterns.js";
export { ReviewEvidenceIndex } from "./review-evidence";
const { REASONING_SEPARATOR, visibleAssistantText } = require("./continuity-text.js") as {
  REASONING_SEPARATOR: string; visibleAssistantText(text: string): string;
};

export type ReviewRow = { identifier: string; status: "found" | "cleared" | "needs_recheck"; source: "reasoning" | "visible";
  round: number; evidence: string; bundle?: EvidenceBundle };

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
  "OnRep", "NetMulticast", "Server", "Client", "Reliable", "Unreliable", "Transient", "Category", "Meta"]);
// Tool-call and evidence ids in backticks: long tokens mixing case and digits.
const OPAQUE_ID = /^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])[A-Za-z0-9_-]{20,}$/u;
const global = (pattern: RegExp) => new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : pattern.flags + "g");
const FINDING_ALL = global(FINDING);

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

/** Each judgment of a line as [subject, status]: one per finding keyword, its
 * sentence deciding the status. A line that only rejects clears its leading
 * subject ("**Issue 9: `TakeDamage` — ... not a bug"). */
function judgments(line: string): Array<[string, ReviewRow["status"]]> {
  const names = namesIn(line), out = new Map<string, ReviewRow["status"]>();
  if (!FINDING.test(line)) {
    const subject = REJECTED.test(line) ? names.spans[0]?.name || names.bare[0]?.name : null;
    return subject ? [[subject, "cleared"]] : [];
  }
  let offset = 0;
  for (const sentence of line.split(SENTENCE_BREAK)) {
    const start = line.indexOf(sentence, offset); offset = start + sentence.length;
    const rejected = REJECTED.test(sentence);
    for (const match of sentence.matchAll(FINDING_ALL)) {
      const subject = subjectAt(names, start + match.index!);
      if (subject && !out.has(subject)) out.set(subject, rejected ? "cleared" : "found");
    }
  }
  return [...out];
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
        for (const [identifier, status] of judgments(line))
          this.record({ identifier, status, source, round, evidence: line.trim().slice(0, 240) });
      }
    }
  }

  private claimKey(row: ReviewRow): string {
    const path = /([\w./\\-]+\.(?:h|hpp|cpp|cc|cs|ini))\s*[:(]\s*\d+/u.exec(row.evidence)?.[1];
    return `${row.identifier}\u0000${path ? pathKey(path) : "unqualified"}`;
  }

  private record(row: ReviewRow) {
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
      const current = row.bundle?.path ? index.shaOf(pathKey(row.bundle.path), row.bundle.sourceKey) : null;
      if (row.bundle) {
        if (current && current !== row.bundle.sha) row.status = "needs_recheck";
        continue;
      }
      row.bundle = index.bundleFor(row.identifier, row.evidence) ?? undefined;
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
    return this.rows.flatMap(row => [`- [${row.status}] ${row.identifier} (${row.source}, round ${row.round}): ${row.evidence}`,
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
  /** Found rows the answer does not name, including ones the bounded register
   * already pushed out of the model input. */
  missing(answer: string): ReviewRow[] {
    return [...this.retired.values(), ...this.rows].filter(row => {
      if (row.status === "cleared") return false;
      if (row.status === "needs_recheck") return !answer.split(/\r?\n/u).some(line => line.includes(row.identifier)
        && /미확인|미검증|재검토|needs_recheck|unverified|not verified|recheck|withdraw/iu.test(line));
      return !answer.includes(row.identifier);
    });
  }
}

/** Header of the delivered report a review supplement follows. */
export const DELIVERED_REPORT_HEADER = "[Delivered report — already shown to the user; write only what follows it]";

export function deliveredReportFact(answer: string): string {
  return `${DELIVERED_REPORT_HEADER}
${answer.trim()}
[End delivered report]`;
}

export function reportReviewFact(missing: readonly ReviewRow[], contracts: readonly { factId: string; statement: string }[]): string {
  return ["Runtime evidence observation: report review obligations (working notes, not verified facts)",
    ...missing.flatMap(row => [`- Missing ${row.identifier}: ${row.evidence}${row.status === "needs_recheck"
      ? ` [needs_recheck; observed sha ${row.bundle?.sha || "unknown"}; newer source cannot validate this old judgment]` : ""}`,
      ...(row.bundle ? [bundleLine(row.bundle, true)] : [])]),
    ...contracts.map(gap => `- Engine contract ${gap.factId}: ${gap.statement}`)].join("\n");
}

export function reportReviewNotice(missing: readonly ReviewRow[], cause: "budget" | "empty" = "budget"): string | null {
  const why = cause === "empty" ? "검토 보완 호출이 내용을 생성하지 않았습니다." : "입력 예산 때문에 검토 보완을 생성하지 못했습니다.";
  const where = (row: ReviewRow) => row.bundle?.lines ? ` (${row.bundle.path.split("/").pop()}:${row.bundle.lines[0]})` : "";
  return missing.length ? `\n\n[보고 검토 · 누락] ${why} 다음 작업 메모는 보고에서 다뤄지지 않았으며, 확인된 결함으로 간주할 수 없습니다.\n`
    + missing.map(row => `- ${row.identifier}${where(row)}: ${row.evidence}`).join("\n") : null;
}
