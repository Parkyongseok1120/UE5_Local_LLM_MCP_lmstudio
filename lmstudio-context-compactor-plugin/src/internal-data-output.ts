import { REVIEW_REGISTER_HEADER as REVIEW_HEADER, REVIEW_REGISTER_GUIDANCE as REVIEW_GUIDANCE } from "./review-patterns.js";
const LEDGER_HEADER = "Runtime read ledger (kept across compaction)";
/** The header renderReferenceData writes (reference-rendering.ts). */
const { DERIVED_HEADER, HOST_FRAME_HEADERS, HOST_FRAME_MAX_PROSE_LINES, derivedEnvelopePrefix } = require("./internal-data-contract.js") as {
  DERIVED_HEADER: string; HOST_FRAME_HEADERS: readonly string[]; HOST_FRAME_MAX_PROSE_LINES: number;
  derivedEnvelopePrefix(body: string): boolean;
};
export { HOST_FRAME_HEADERS };
// Lines of a JSON echo, including a pretty-printed one.
const JSON_LINE = /^(?:[{}\[\]",:0-9-]|true\b|false\b|null\b)/u;
const OBSERVATION_HEADER = "Runtime evidence observation:";
const OMITTED_HEADER = "Runtime data omitted for budget:";
const REVIEW_ROW_SHAPE = /^- \[(?:found|cleared)\] \S+ \((?:reasoning|visible), round \d+\): /u;
// The evidence line under a register row (review-register.ts bundleLine).
const REVIEW_BUNDLE_SHAPE = /^ {2}↳ \S/u;
// A re-rendered observation header: its counts change between rounds.
const OBSERVATION_FAMILY = /^Runtime evidence observation: \d+ earlier tool result\(s\) are present in this input only in part/u;
// "- <path> sha <8 hex>: ..." (ledger) or "- <path>: ..." (observation).
const RUNTIME_ROW = /^- (\S+?)(?: sha [0-9a-f]{8})?: /u;
// Long injected sentences are recognized by their opening when a model rewrites a number further on.
const LINE_PREFIX_CHARS = 48;
// What a model echoes once it has seen the ledger and observation blocks: rows
// in their exact shape for any file, with invented handles, and the fixed
// sentences that follow them, without the header (2026-10-25 run
// 1792913431525). These shapes are hidden whenever such a block is injected.
const LEDGER_ROW_SHAPE = /^- \S+ sha [0-9a-f]{8}: delivered .*; visible now: /u;
const OBSERVATION_ROW_SHAPE = /^- \S+: .*\(\d+ of \d+ chars shown/u;
const OVERFLOW_ROW = /^- \d+ more file\(s\)/u;
/** Openings of the fixed sentences read-ledger.js and resume-facts.ts append. */
export const RUNTIME_SENTENCE_OPENINGS = ["Partial here does not mean the source was never read",
  "These are last-observed versions, not a fresh disk check"];

/** Only protocols actually supplied by this execution are candidates for hiding.
 * Quoted/fenced examples and ordinary JSON remain user content. This filter never
 * interprets a ref as a tool call or grants authority to an observation. */
export class InternalDataOutputFilter {
  private readonly refsHeader: string | undefined;
  private readonly derivedHeader: string | undefined;
  private readonly derivedParts: string[];
  private readonly budgetParts: string[];
  private readonly budgetHeader = "Runtime read-result budget facts: ";
  private readonly runtimeParts: string[];
  private readonly runtimeHeaders: string[];
  private readonly runtimeLines: Set<string>;
  /** Source paths the injected ledger/observation rows name. */
  private readonly runtimePaths: Set<string>;
  private readonly runtimeLinePrefixes: Set<string>;
  private readonly ledgerInjected: boolean;
  private readonly observationInjected: boolean;
  private readonly reviewInjected: boolean;
  /** Headers of host frames this input carries, and their fixed prose lines. */
  private readonly frameHeaders: string[];
  private readonly frameLines: Set<string>;
  private inRuntime = false;
  private pending = "";
  private candidate = "";
  private frameJson = "";
  private mode: "normal" | "refs" | "derived" | "derivedEnd" | "frame" = "normal";
  private fence: { character: string; length: number } | null = null;
  private passthrough = false;
  private ended = false;
  private discardedDerived = false;
  private protocolSuppressed = false;
  private protocolIncomplete = false;
  get observation() { return { suppressedProtocol: this.protocolSuppressed, incompleteProtocol: this.protocolIncomplete }; }

  constructor(parts: readonly string[] = []) {
    this.budgetParts = parts.filter(p => p.startsWith(this.budgetHeader));
    this.runtimeParts = parts.filter(p => p.startsWith(LEDGER_HEADER) || p.startsWith(OBSERVATION_HEADER)
      || p.startsWith(REVIEW_HEADER) || p.startsWith(OMITTED_HEADER));
    this.runtimeHeaders = this.runtimeParts.map(p => p.split(/\r?\n/u)[0]);
    this.runtimeLines = new Set(this.runtimeParts.flatMap(p => p.split(/\r?\n/u)));
    const rows = this.runtimeParts.flatMap(p => p.split(/\r?\n/u).slice(1));
    this.runtimePaths = new Set(rows.map(row => RUNTIME_ROW.exec(row)?.[1]).filter((path): path is string => Boolean(path)));
    this.runtimeLinePrefixes = new Set(rows.filter(row => row.length >= LINE_PREFIX_CHARS && !row.startsWith("- "))
      .map(row => row.slice(0, LINE_PREFIX_CHARS)));
    this.ledgerInjected = this.runtimeParts.some(p => p.startsWith(LEDGER_HEADER));
    this.reviewInjected = this.runtimeParts.some(p => p.startsWith(REVIEW_HEADER));
    this.observationInjected = this.runtimeParts.some(p => p.includes("\n") && OBSERVATION_FAMILY.test(p));
    this.refsHeader = parts.find(p => p.startsWith("[Available tool-call refs]"))?.split(/\r?\n/u)[0];
    this.derivedParts = parts.filter(p => p.startsWith(DERIVED_HEADER)).map(p => p.replace(/\r\n/gu, "\n"));
    this.derivedHeader = this.derivedParts[0]?.split("\n")[0];
    // A frame may follow derived data inside one part, so scan every line.
    const frames = parts.flatMap(part => {
      const lines = part.replace(/\r\n/gu, "\n").split("\n");
      return lines.flatMap((line, index) => HOST_FRAME_HEADERS.includes(line)
        ? [{ header: line, prose: lines.slice(index + 1, index + 1 + HOST_FRAME_MAX_PROSE_LINES)
          .map(row => row.trimEnd()).filter(row => row && !row.trimStart().startsWith("{")) }] : []);
    });
    this.frameHeaders = [...new Set(frames.map(frame => frame.header))];
    this.frameLines = new Set(frames.flatMap(frame => frame.prose));
  }

  /** Inside an injected host frame: its prose lines, then its JSON (complete,
   * pretty-printed or cut off). Anything else ends the frame and is user text. */
  private frameLine(line: string, trimmed: string): string | null {
    if (this.frameJson || trimmed.startsWith("{")) {
      this.frameJson += line;
      try { JSON.parse(this.frameJson); this.protocolSuppressed = true; this.frameJson = ""; this.mode = "normal"; return ""; }
      catch { /* Incomplete so far. */ }
      if (this.frameJson.length <= 131072 && (!trimmed || JSON_LINE.test(trimmed))) return "";
      // A cut-off echo followed by prose: the JSON part is still the protocol.
      this.frameJson = ""; this.protocolSuppressed = true; this.protocolIncomplete = true; this.mode = "normal";
      return null;
    }
    if (!trimmed || this.frameLines.has(line.replace(/\r?\n$/u, "").trimEnd())) return "";
    this.mode = "normal";
    return null;
  }

  /** A re-rendered header of an injected runtime part (its counts may differ). */
  private runtimeHeader(bare: string): boolean {
    return this.runtimeHeaders.includes(bare)
      || (this.ledgerInjected && bare.startsWith(LEDGER_HEADER))
      || (this.observationInjected && OBSERVATION_FAMILY.test(bare));
  }

  private get runtimeInjected() { return this.ledgerInjected || this.observationInjected || this.reviewInjected; }

  /** Inside a runtime block: any row naming an injected path, an injected
   * line, a rewritten injected sentence, or the overflow line. */
  private runtimeBlockLine(bare: string): boolean {
    if (!bare || this.runtimeLines.has(bare) || this.headerlessRuntimeLine(bare)) return true;
    if (bare.length >= LINE_PREFIX_CHARS && this.runtimeLinePrefixes.has(bare.slice(0, LINE_PREFIX_CHARS))) return true;
    const path = RUNTIME_ROW.exec(bare)?.[1];
    return Boolean(path && this.runtimePaths.has(path));
  }

  /** A runtime line echoed without its header: an injected row verbatim or,
   * while this input carries ledger/observation data, a line in a row's exact
   * shape for any file (the model invents rows for files the bounded ledger
   * did not list) or one of the fixed sentences. Ordinary bullets about the
   * same files do not have those shapes. */
  private headerlessRuntimeLine(bare: string): boolean {
    if (bare.startsWith("- ") && this.runtimeLines.has(bare)) return true;
    if (!this.runtimeInjected) return false;
    if (this.reviewInjected && (REVIEW_ROW_SHAPE.test(bare) || REVIEW_BUNDLE_SHAPE.test(bare) || bare === REVIEW_GUIDANCE)) return true;
    return LEDGER_ROW_SHAPE.test(bare) || OBSERVATION_ROW_SHAPE.test(bare) || OVERFLOW_ROW.test(bare)
      || RUNTIME_SENTENCE_OPENINGS.some(opening => bare.startsWith(opening));
  }

  private line(line: string): string {
    const trimmed = line.trim();
    if (this.mode === "frame") {
      const handled = this.frameLine(line, trimmed);
      if (handled !== null) return handled;
    }
    if (this.mode === "derived") {
      if (this.discardedDerived) {
        if (trimmed === "[End derived reference data]") { this.mode = "derivedEnd"; this.discardedDerived = false; }
        return "";
      }
      this.candidate += line;
      const body = this.candidate.slice(this.candidate.indexOf("\n") + 1).trim();
      try {
        const data = JSON.parse(body);
        // Match the observation envelope, not arbitrary JSON after a heading.
        // Its items may be rearranged in an echo (2026-10-25 run moved them
        // under referenceContext), so the envelope's own fields decide.
        if (derivedEnvelopePrefix(body)) {
          this.protocolSuppressed = true; this.candidate = ""; this.mode = "derivedEnd"; return "";
        }
        const literal = this.candidate;
        this.candidate = ""; this.mode = "normal"; return literal;
      } catch {
        // A forged echo can be malformed JSON (conversation 1792916809646 v0
        // r3: an invented hash and call ID, one brace short). Between the
        // injected header and the end marker, opening with the envelope's own
        // fields, it is still the protocol, not report text.
        if (trimmed === "[End derived reference data]") {
          const json = body.slice(0, body.lastIndexOf("[End derived reference data]")).trim();
          if (derivedEnvelopePrefix(json)) {
            this.protocolSuppressed = true; this.candidate = ""; this.mode = "derivedEnd"; return "";
          }
        }
        // A normal line after a malformed/truncated compact object must survive.
        if (this.candidate.length > 131072 && derivedEnvelopePrefix(body)) {
          this.protocolSuppressed = true; this.protocolIncomplete = true;
          this.discardedDerived = true; this.candidate = ""; return "";
        }
        if ((body && !body.startsWith("{")) || this.candidate.length > 131072
          || (!derivedEnvelopePrefix(body) && body.includes("\n") && trimmed && !/^[\s{}\[\]",:0-9\-]|^(?:true|false|null)\b/u.test(trimmed))) {
          const literal = this.candidate;
          this.candidate = ""; this.mode = "normal"; return literal;
        }
        return "";
      }
    }
    if (this.mode === "derivedEnd") {
      if (!trimmed || trimmed === "[End derived reference data]") return "";
      this.mode = "normal";
    }
    if (this.mode === "refs") {
      if (!trimmed || /^(?:[-*]\s+)?tool-call:[A-Za-z0-9_-]{1,80}\s+[A-Za-z0-9_.:/-]+(?:\s+.*)?$/u.test(trimmed)) return "";
      this.mode = "normal";
    }
    const marker = /^ {0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$/u.exec(line);
    if (this.fence) {
      if (marker && marker[1][0] === this.fence.character && marker[1].length >= this.fence.length
        && !marker[2].trim()) this.fence = null;
      return line;
    }
    if (marker) { this.fence = { character: marker[1][0], length: marker[1].length }; return line; }
    // Do not trim leading indentation: indented code and blockquotes are literal.
    const bare = line.replace(/\r?\n$/u, "").trimEnd();
    if (this.frameHeaders.includes(bare)) { this.mode = "frame"; this.frameJson = ""; return ""; }
    if (this.runtimeHeader(bare)) { this.inRuntime = true; return ""; }
    if (this.inRuntime && this.runtimeBlockLine(bare)) return "";
    this.inRuntime = false;
    // 2026-10-25 runs: answers began with ledger/observation rows and their
    // closing sentences, without a header.
    if (this.headerlessRuntimeLine(bare)) return "";
    if (this.budgetParts.length && bare.startsWith(this.budgetHeader)) {
      // A model may rewrite numeric values while echoing the injected protocol.
      // Recognize its bounded schema and only its supplied instruction suffix;
      // never hide arbitrary prose/JSON, quoted examples or fenced content.
      const end = bare.indexOf("]. Limits bound the full response, not source body length.");
      const jsonEnd = end >= 0 ? end + 1 : bare.endsWith("]") ? bare.length : -1;
      if (jsonEnd > 0) try {
        const rows = JSON.parse(bare.slice(this.budgetHeader.length, jsonEnd));
        if (Array.isArray(rows) && rows.length > 0 && rows.length <= 4 && rows.every(row => row
          && ["byteBudget", "maxChars", "maxBytes"].includes(row.field)
          && Number.isFinite(row.requested) && Number.isFinite(row.effective))) {
          const suffix = bare.slice(jsonEnd);
          if (!suffix) return "";
          for (const part of this.budgetParts) {
            const start = part.indexOf("]. Limits bound the full response, not source body length.");
            const knownSuffix = start >= 0 ? part.slice(start + 1).trimEnd() : "";
            if (knownSuffix && suffix.startsWith(knownSuffix)) {
              const rest = suffix.slice(knownSuffix.length).trimStart();
              return rest ? rest + (line.endsWith("\n") ? "\n" : "") : "";
            }
          }
        }
      } catch { /* Unknown/malformed text belongs to the user output. */ }
    }
    if (this.refsHeader && bare === this.refsHeader) { this.mode = "refs"; return ""; }
    if (this.derivedHeader && bare === this.derivedHeader) {
      this.mode = "derived"; this.candidate = line.endsWith("\n") ? line : line + "\n"; return "";
    }
    return line;
  }

  push(text: string): string {
    if (this.ended || !text) return "";
    if (!this.refsHeader && !this.derivedHeader && !this.budgetParts.length && !this.runtimeParts.length
      && !this.frameHeaders.length) return text;
    this.pending += text;
    let output = "", newline: number;
    while ((newline = this.pending.indexOf("\n")) >= 0) {
      const line = this.pending.slice(0, newline + 1);
      this.pending = this.pending.slice(newline + 1);
      output += this.passthrough ? line : this.line(line);
      this.passthrough = false;
    }
    if (this.mode === "refs" && this.pending.trim()
      && !["- tool-call:", "* tool-call:", "tool-call:"].some(p => p.startsWith(this.pending) || this.pending.startsWith(p))) {
      this.mode = "normal";
    }
    if (this.mode === "derivedEnd" && this.pending.trim()
      && !"[End derived reference data]".startsWith(this.pending.trimEnd())) this.mode = "normal";
    // Ordinary prose streams immediately; only possible metadata/fence prefixes
    // are held until their line is classified. No paragraph-sized delay.
    const prefixes = [this.refsHeader, this.derivedHeader, ...this.frameHeaders, ...this.runtimeHeaders,
      ...(this.ledgerInjected ? [LEDGER_HEADER] : []), ...(this.observationInjected ? [OBSERVATION_HEADER] : []),
      ...(this.runtimeInjected ? RUNTIME_SENTENCE_OPENINGS : []),
      ...(this.reviewInjected ? [REVIEW_GUIDANCE] : []),
      ...(this.inRuntime ? [...this.runtimeLines] : []), ...(this.budgetParts.length ? [this.budgetHeader] : [])].filter((p): p is string => Boolean(p));
    // A bullet may be a runtime row: hold its line until it can be classified.
    const possibleRow = (this.runtimeInjected || this.runtimePaths.size > 0)
      && ("- ".startsWith(this.pending) || this.pending.startsWith("- "));
    // Inside a block, a rewritten injected sentence diverges only after its opening.
    const possibleSentence = this.inRuntime && (this.pending.length >= LINE_PREFIX_CHARS
      ? this.runtimeLinePrefixes.has(this.pending.slice(0, LINE_PREFIX_CHARS))
      : [...this.runtimeLinePrefixes].some(prefix => prefix.startsWith(this.pending)));
    const possibleHeader = possibleRow || possibleSentence
      || prefixes.some(p => p.startsWith(this.pending) || this.pending.startsWith(p));
    const possibleFence = /^ {0,3}(?:`*|~*)$/u.test(this.pending)
      || /^ {0,3}(?:`{3,}|~{3,})/u.test(this.pending);
    if (this.pending && (this.passthrough || (this.mode === "normal" && !possibleHeader && !possibleFence))) {
      output += this.pending; this.pending = ""; this.passthrough = true;
    }
    if (this.mode === "frame" && this.pending.length + this.frameJson.length > 131072) {
      this.protocolSuppressed = true; this.protocolIncomplete = true;
      this.frameJson = ""; this.pending = ""; this.mode = "normal"; return output;
    }
    if (this.pending.length + this.candidate.length > 131072) {
      const body = this.candidate.slice(this.candidate.indexOf("\n") + 1) + this.pending;
      if (this.mode === "derived" && (this.discardedDerived || derivedEnvelopePrefix(body))) {
        this.protocolSuppressed = true; this.protocolIncomplete = true; this.discardedDerived = true;
        this.candidate = ""; this.pending = ""; return output;
      }
      output += this.candidate + this.pending;
      this.candidate = ""; this.pending = ""; this.mode = "normal"; this.passthrough = true;
    }
    return output;
  }

  finish(): string {
    if (this.ended) return "";
    this.ended = true;
    const canceledBudgetEcho = !this.fence && !this.passthrough && this.pending.startsWith(this.budgetHeader)
      && this.budgetParts.some(part => part.startsWith(this.pending));
    const canceledRuntimeEcho = !this.fence && !this.passthrough && this.pending.startsWith("Runtime ")
      && this.pending.length >= 25 && this.runtimeParts.some(part => part.startsWith(this.pending));
    let output = canceledBudgetEcho || canceledRuntimeEcho ? "" : this.passthrough ? this.pending : this.pending ? this.line(this.pending) : "";
    this.pending = "";
    if (this.frameJson) {
      // A frame echo cut off by the end of generation.
      this.protocolSuppressed = true; this.protocolIncomplete = true; this.frameJson = "";
    }
    if (this.candidate) {
      const partial = this.candidate.replace(/\r\n/gu, "\n").trimEnd();
      const body = partial.slice(partial.indexOf("\n") + 1);
      if (derivedEnvelopePrefix(body) || this.derivedParts.some(p => p.startsWith(partial))) {
        this.protocolSuppressed = true; this.protocolIncomplete = true;
      } else output += this.candidate;
      this.candidate = "";
    }
    return output;
  }
}

export function stripInternalDataEcho(text: string, parts: readonly string[] = []): string {
  const filter = new InternalDataOutputFilter(parts);
  return filter.push(text) + filter.finish();
}
