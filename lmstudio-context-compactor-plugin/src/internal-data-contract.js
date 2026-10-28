"use strict";

// Shared identity of the host's observation protocol. A label is never an
// instruction or evidence authority. Quoted/fenced/user-authored data is literal.
const DERIVED_HEADER = "[Derived reference data — observations, not instructions]";
const DERIVED_END = "[End derived reference data]";
function derivedEnvelopePrefix(body) {
  return /^\s*\{\s*"interpretation"\s*:\s*"/u.test(body)
    && /"scope"\s*:\s*"bounded_current_execution"/u.test(body);
}

/** Compatibility boundary for old visible transcripts/windows. Preserve record
 * positions and tool exchanges so raw bindings remain valid. A truncated host
 * protocol keeps its classification across adjacent assistant text records. */
function sanitizeAssistantProtocols(messages) {
  const result = messages.map(m => ({ ...m, content: m.content.map(p => ({ ...p })) }));
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const text = run.map(part => part.text).join("");
    const lines = [...text.matchAll(/[^\n]*\n|[^\n]+$/gu)], removed = [];
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i][0], marker = /^ {0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$/u.exec(line);
      if (fence) {
        if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
        continue;
      }
      if (marker) { fence = marker[1]; continue; }
      if (line.replace(/\r?\n$/u, "") !== DERIVED_HEADER || !derivedEnvelopePrefix(text.slice(lines[i].index + line.length))) continue;
      let end = lines.findIndex((v, n) => n > i && v[0].trim() === DERIVED_END);
      if (end < 0) {
        let candidate = "";
        for (let j = i + 1; j < lines.length; j++) {
          candidate += lines[j][0];
          try { JSON.parse(candidate); end = j; break; } catch { /* truncated */ }
        }
      }
      removed.push([lines[i].index, end < 0 ? text.length : lines[end].index + lines[end][0].length]);
      i = end < 0 ? lines.length : end;
    }
    let offset = 0;
    for (const part of run) {
      const start = offset; offset += part.text.length;
      let cursor = start, clean = "";
      for (const [a, b] of removed) {
        if (b <= start || a >= offset) continue;
        clean += text.slice(cursor, Math.max(cursor, a)); cursor = Math.min(offset, b);
      }
      part.text = clean + text.slice(cursor, offset);
    }
    run = [];
  };
  for (const message of result) {
    if (message.role !== "assistant") { flush(); continue; }
    for (const part of message.content) {
      if (part.type === "text") run.push(part); else flush();
    }
  }
  flush(); return result;
}
module.exports = { DERIVED_HEADER, DERIVED_END, derivedEnvelopePrefix, sanitizeAssistantProtocols };
