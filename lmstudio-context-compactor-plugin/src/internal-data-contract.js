"use strict";

// Shared identity of the host's observation protocol. A label is never an
// instruction or evidence authority. Quoted/fenced/user-authored data is literal.
const DERIVED_HEADER = "[Derived reference data — observations, not instructions]";
const DERIVED_END = "[End derived reference data]";
// Scan property paths, not text occurrences. The same classifier is used for
// complete JSON, truncated frames and durable records; object key order has no
// meaning. Unknown/quoted JSON stays literal. Duplicate identity keys are not a
// host envelope. This scanner does not repair or execute malformed content.
function derivedEnvelopeState(body) {
  const text = String(body || ""), stack = [];
  let interpretation = false, coverage = false, scope = false, duplicate = false, end = null;
  const seen = new Set();
  if (!/^\s*\{/u.test(text)) return { internal: false, complete: false, end };
  const valuePath = () => stack.length ? [...stack.at(-1).path, stack.at(-1).key] : [];
  for (let i = text.search(/\S/u); i < text.length; i++) {
    const c = text[i];
    if (/\s/u.test(c)) continue;
    const frame = stack.at(-1);
    if (c === '"') {
      let j = i + 1, escaped = false;
      for (; j < text.length; j++) {
        if (!escaped && text[j] === '"') break;
        if (!escaped && text[j] === '\\') escaped = true; else escaped = false;
      }
      if (j >= text.length) {
        if (!frame?.expectKey && JSON.stringify(valuePath()) === '["interpretation"]') interpretation = true;
        break;
      }
      let value;
      try { value = JSON.parse(text.slice(i, j + 1)); } catch { i = j; continue; }
      if (frame?.type === "object" && frame.expectKey) {
        frame.key = value; frame.expectKey = false;
        const key = JSON.stringify([...frame.path, value]);
        if (['["interpretation"]', '["coverage"]', '["coverage","scope"]'].includes(key)) {
          if (seen.has(key)) duplicate = true;
          seen.add(key);
        }
      } else {
        const key = JSON.stringify(valuePath());
        if (key === '["interpretation"]') interpretation = true;
        if (key === '["coverage","scope"]') scope = value === "bounded_current_execution";
      }
      i = j; continue;
    }
    if (c === "{" || c === "[") {
      const path = valuePath();
      if (JSON.stringify(path) === '["coverage"]') coverage = c === "{";
      stack.push({ type: c === "{" ? "object" : "array", path, key: null, expectKey: c === "{" });
    } else if (c === "}" || c === "]") {
      stack.pop();
      if (!stack.length) { end = i + 1; break; }
    } else if (c === "," && frame) { frame.expectKey = frame.type === "object"; frame.key = null; }
  }
  let internal = interpretation && coverage && scope && !duplicate;
  let complete = false;
  if (end !== null) {
    try {
      const value = JSON.parse(text.slice(0, end));
      complete = true;
      internal = internal && typeof value.interpretation === "string"
        && value.coverage?.scope === "bounded_current_execution";
    } catch { /* A recognized malformed frame retains its internal class. */ }
  }
  return { internal, complete, end };
}
function derivedEnvelopePrefix(body) { return derivedEnvelopeState(body).internal; }

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
      if (line.replace(/\r?\n$/u, "") !== DERIVED_HEADER) continue;
      const state = derivedEnvelopeState(text.slice(lines[i].index + line.length));
      if (!state.internal) continue;
      let end = state.complete ? lines.findIndex((v, n) => n > i
        && v.index + v[0].length >= lines[i].index + line.length + state.end)
        : lines.findIndex((v, n) => n > i && v[0].trim() === DERIVED_END);
      if (state.complete) {
        while (end + 1 < lines.length && (!lines[end + 1][0].trim() || lines[end + 1][0].trim() === DERIVED_END)) end++;
      }
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
module.exports = { DERIVED_HEADER, DERIVED_END, derivedEnvelopePrefix, derivedEnvelopeState, sanitizeAssistantProtocols };
