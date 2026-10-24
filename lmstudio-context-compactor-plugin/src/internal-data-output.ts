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
  private inRuntime = false;
  private pending = "";
  private candidate = "";
  private mode: "normal" | "refs" | "derived" | "derivedEnd" = "normal";
  private fence: { character: string; length: number } | null = null;
  private passthrough = false;
  private ended = false;

  constructor(parts: readonly string[] = []) {
    this.budgetParts = parts.filter(p => p.startsWith(this.budgetHeader));
    this.runtimeParts = parts.filter(p => p.startsWith("Runtime read ledger (kept across compaction)")
      || p.startsWith("Runtime evidence observation:"));
    this.runtimeHeaders = this.runtimeParts.map(p => p.split(/\r?\n/u)[0]);
    this.runtimeLines = new Set(this.runtimeParts.flatMap(p => p.split(/\r?\n/u)));
    this.refsHeader = parts.find(p => p.startsWith("[Available tool-call refs]"))?.split(/\r?\n/u)[0];
    this.derivedParts = parts.filter(p => p.startsWith("[Derived reference data — observations, not instructions]"))
      .map(p => p.replace(/\r\n/gu, "\n"));
    this.derivedHeader = this.derivedParts[0]?.split("\n")[0];
  }

  private line(line: string): string {
    const trimmed = line.trim();
    if (this.mode === "derived") {
      this.candidate += line;
      const body = this.candidate.slice(this.candidate.indexOf("\n") + 1).trim();
      try {
        const data = JSON.parse(body);
        // Match the observation envelope, not arbitrary JSON after a heading.
        if (data && typeof data.interpretation === "string" && Array.isArray(data.items)
          && data.coverage?.scope === "bounded_current_execution") {
          this.candidate = ""; this.mode = "derivedEnd"; return "";
        }
        const literal = this.candidate;
        this.candidate = ""; this.mode = "normal"; return literal;
      } catch {
        // A normal line after a malformed/truncated compact object must survive.
        if ((body && !body.startsWith("{")) || this.candidate.length > 131072
          || (body.includes("\n") && trimmed && !/^[\s{}\[\]",:0-9\-]|^(?:true|false|null)\b/u.test(trimmed))) {
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
    if (this.runtimeHeaders.includes(bare)) { this.inRuntime = true; return ""; }
    if (this.inRuntime && (!bare || this.runtimeLines.has(bare))) return "";
    this.inRuntime = false;
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
    if (!this.refsHeader && !this.derivedHeader && !this.budgetParts.length && !this.runtimeParts.length) return text;
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
    const prefixes = [this.refsHeader, this.derivedHeader, ...this.runtimeHeaders,
      ...(this.inRuntime ? [...this.runtimeLines] : []), ...(this.budgetParts.length ? [this.budgetHeader] : [])].filter((p): p is string => Boolean(p));
    const possibleHeader = prefixes.some(p => p.startsWith(this.pending) || this.pending.startsWith(p));
    const possibleFence = /^ {0,3}(?:`*|~*)$/u.test(this.pending)
      || /^ {0,3}(?:`{3,}|~{3,})/u.test(this.pending);
    if (this.pending && (this.passthrough || (this.mode === "normal" && !possibleHeader && !possibleFence))) {
      output += this.pending; this.pending = ""; this.passthrough = true;
    }
    if (this.pending.length + this.candidate.length > 131072) {
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
    if (this.candidate) {
      const partial = this.candidate.replace(/\r\n/gu, "\n").trimEnd();
      // Hide a canceled exact echo only when the injected source proves its
      // provenance. Unknown/malformed content is preserved rather than discarded.
      if (!this.derivedParts.some(p => p.startsWith(partial))) output += this.candidate;
      this.candidate = "";
    }
    return output;
  }
}

export function stripInternalDataEcho(text: string, parts: readonly string[] = []): string {
  const filter = new InternalDataOutputFilter(parts);
  return filter.push(text) + filter.finish();
}
