import type { ChatMessage } from "@lmstudio/sdk";
const { decodeToolResultRecord } = require("./compaction-tool-memory.js") as {
  decodeToolResultRecord(content: unknown): { value: unknown };
};
const { pathKey } = require("./read-ledger.js") as { pathKey(value: string): string };

/** Where a closed judgment stands and what it rests on, enough to write the
 * report from without re-reading the file (conversation 1792929236226: six
 * rounds re-read the project after a projection because the register kept
 * only one 240-char line per judgment). */
export type EvidenceBundle = { path: string; lines: [number, number] | null; sha: string | null; excerpt: string | null;
  search: string | null };

type IndexedRead = { path: string; key: string; sha: string | null; startLine: number | null; lines: string[] };
type IndexedSearch = { query: string; path: string; filesScanned: number | null; coverage: string | null;
  hits: Array<{ uri: string; line: number; text: string }> };

const SOURCE_READS = new Set(["read_file", "read_file_range", "read_symbol"]);
const MAX_INDEXED_CHARS = 400_000;
const MAX_SEARCHES = 64;
const EXCERPT_CHARS = 360;
const LINE_PREFIX = /^\s*\d+\t/u;
const CITATION = /([\w./\\-]+\.(?:h|hh|hpp|hxx|inl|c|cc|cpp|cxx|cs|ini))\s*[:(]\s*(\d+)(?:\s*[-–]\s*(\d+))?/u;
const fileName = (value: string) => value.replace(/\\/gu, "/").split("/").pop()!.toLowerCase();
const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");

/** Execution-local index of returned source bodies and searches. Read only by
 * the review register; never an authority on the current file. */
export class ReviewEvidenceIndex {
  private readonly reads = new Map<string, IndexedRead>();
  private readonly searches: IndexedSearch[] = [];

  observe(messages: readonly ChatMessage[]) {
    const requests = new Map<string, { name: string; arguments?: Record<string, unknown> }>();
    for (const message of messages) for (const request of message.getToolCallRequests())
      requests.set(String(request.id || ""), request as { name: string; arguments?: Record<string, unknown> });
    for (const message of messages) for (const result of message.getToolCallResults()) {
      const request = requests.get(String(result.toolCallId || ""));
      const value = decodeToolResultRecord(result.content).value as Record<string, any> | null;
      if (!request || !value || typeof value !== "object" || value.ok === false) continue;
      if (SOURCE_READS.has(request.name) && typeof value.content === "string" && typeof value.path === "string") this.addRead(value);
      if (request.name === "search_files" && Array.isArray(value.results)) this.searches.push({
        query: String(value.query ?? request.arguments?.query ?? ""), path: String(value.path ?? request.arguments?.path ?? ""),
        filesScanned: Number.isSafeInteger(value.filesScanned) ? value.filesScanned : null,
        coverage: typeof value.coverage === "string" ? value.coverage : null,
        hits: value.results.filter((hit: any) => hit && Number.isSafeInteger(hit.line))
          .map((hit: any) => ({ uri: String(hit.uri || hit.path || ""), line: hit.line, text: String(hit.text || "") })) });
    }
    while (this.searches.length > MAX_SEARCHES) this.searches.shift();
  }

  private addRead(value: Record<string, any>) {
    // A byte window past the start has no verified first line.
    const startLine = Number.isSafeInteger(value.startLine) ? value.startLine
      : !value.offsetBytes ? 1 : null;
    const key = pathKey(value.path), sha = typeof value.sha256 === "string" ? value.sha256.toLowerCase() : null;
    const prior = this.reads.get(key);
    // Keep the widest body of the newest version: a later narrow range does
    // not replace the whole file it lies in.
    const lines = String(value.content).split(/\r?\n/u).map(line => line.replace(LINE_PREFIX, ""));
    if (prior && prior.sha === sha && prior.startLine === 1 && prior.lines.length >= lines.length && startLine !== 1) return;
    this.reads.delete(key);
    this.reads.set(key, { path: value.path, key, sha, startLine, lines });
    let total = [...this.reads.values()].reduce((sum, read) => sum + read.lines.join("\n").length, 0);
    for (const oldest of this.reads.keys()) {
      if (total <= MAX_INDEXED_CHARS || this.reads.size <= 1) break;
      total -= this.reads.get(oldest)!.lines.join("\n").length; this.reads.delete(oldest);
    }
  }

  /** The current sha of a path's indexed body, if any. */
  shaOf(key: string): string | null { return this.reads.get(key)?.sha ?? null; }

  bundleFor(identifier: string, evidence: string): EvidenceBundle | null {
    const name = identifier.split("::").pop()!;
    const word = new RegExp(`(?<![\\w])${escapeRegExp(name)}(?![\\w])`, "u");
    const searches = this.searches.filter(search => word.test(search.query) || search.hits.some(hit => word.test(hit.text)));
    const search = searches.at(-1);
    const searchText = search ? `search "${search.query.slice(0, 80)}" in ${search.path || "?"}: ${search.filesScanned ?? "?"} files, `
      + `coverage ${search.coverage ?? "unknown"}, ${search.hits.filter(hit => word.test(hit.text)).length} hit(s)`
      + search.hits.filter(hit => word.test(hit.text)).slice(0, 4).map(hit => ` ${fileName(hit.uri)}:${hit.line}`).join(",") : null;
    // Location: a cited file:line, else a definition-like search hit, else the
    // first definition-like line of an indexed body.
    const cited = CITATION.exec(evidence);
    let read: IndexedRead | undefined, line: number | null = null, end: number | null = null, hitPath: string | null = null;
    if (cited) {
      read = [...this.reads.values()].find(item => fileName(item.path) === fileName(cited[1]));
      line = Number(cited[2]); end = cited[3] ? Number(cited[3]) : null;
    }
    if (!read) {
      const definition = new RegExp(`(?:::|\\b(?:void|bool|int32|float|class|struct|enum)\\s+)${escapeRegExp(name)}\\b|^\\s*\\w[\\w<>:*&\\s]*\\b${escapeRegExp(name)}\\s*[;(=]`, "u");
      // An out-of-line definition in a returned body first, then any hit in
      // one, then a hit's location alone.
      const hits = searches.flatMap(item => item.hits).filter(hit => word.test(hit.text));
      const bodyOf = (uri: string) => [...this.reads.values()].find(item => fileName(item.path) === fileName(uri));
      const outOfLine = new RegExp(`::${escapeRegExp(name)}\\b`, "u");
      const hit = hits.find(item => outOfLine.test(item.text) && bodyOf(item.uri)) || hits.find(item => definition.test(item.text) && bodyOf(item.uri))
        || hits.find(item => bodyOf(item.uri)) || hits[0];
      if (hit) {
        read = bodyOf(hit.uri);
        line = hit.line;
        hitPath = hit.uri;
      } else for (const item of [...this.reads.values()].reverse()) {
        if (item.startLine === null) continue;
        const index = item.lines.findIndex(text => definition.test(text)), any = index >= 0 ? index : item.lines.findIndex(text => word.test(text));
        if (any >= 0) { read = item; line = item.startLine + any; break; }
      }
    }
    if (!read && !searchText) return null;
    let excerpt: string | null = null;
    if (read && line !== null && read.startLine !== null) {
      const from = Math.max(0, line - read.startLine), to = Math.min(read.lines.length, (end ?? line + 3) - read.startLine + 1);
      excerpt = read.lines.slice(from, Math.max(from + 1, to)).map(text => text.trimEnd()).join(" ⏎ ").slice(0, EXCERPT_CHARS) || null;
    }
    return { path: read?.path ?? hitPath ?? (search ? search.path : ""), lines: line !== null && (read || hitPath) ? [line, end ?? line] : null,
      sha: read?.sha ?? null, excerpt, search: searchText };
  }
}
