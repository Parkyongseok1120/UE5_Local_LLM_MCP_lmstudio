/** Only protocols actually supplied by this execution are candidates for hiding.
 * Quoted/fenced examples and ordinary JSON remain user content. This filter never
 * interprets a ref as a tool call or grants authority to an observation. */
export class InternalDataOutputFilter {
  private readonly refsHeader: string | undefined;
  private readonly derivedHeader: string | undefined;
  private readonly derivedParts: string[];
  private pending = "";
  private candidate = "";
  private mode: "normal" | "refs" | "derived" | "derivedEnd" = "normal";
  private fence: { character: string; length: number } | null = null;
  private passthrough = false;
  private ended = false;

  constructor(parts: readonly string[] = []) {
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
    if (this.refsHeader && bare === this.refsHeader) { this.mode = "refs"; return ""; }
    if (this.derivedHeader && bare === this.derivedHeader) {
      this.mode = "derived"; this.candidate = line.endsWith("\n") ? line : line + "\n"; return "";
    }
    return line;
  }

  push(text: string): string {
    if (this.ended || !text) return "";
    if (!this.refsHeader && !this.derivedHeader) return text;
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
    const prefixes = [this.refsHeader, this.derivedHeader].filter((p): p is string => Boolean(p));
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
    let output = this.passthrough ? this.pending : this.pending ? this.line(this.pending) : "";
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
