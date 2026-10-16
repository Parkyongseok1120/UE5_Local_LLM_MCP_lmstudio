"use strict";

// Closed intervals internally. Native byte/character end offsets are exclusive.
function normalizeRanges(ranges) {
  const candidates = (ranges || []).map(range => {
    const start = Array.isArray(range) ? range[0] : range?.start;
    const end = Array.isArray(range) ? range[1] : range?.end;
    return Number.isSafeInteger(start) && Number.isSafeInteger(end) && start >= 0 && end >= start
      ? [start, end] : null;
  }).filter(Boolean).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const range of candidates) {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1] + 1) previous[1] = Math.max(previous[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

function subtractRanges(candidate, prior) {
  let remaining = normalizeRanges([candidate]);
  for (const [lo, hi] of normalizeRanges(prior)) {
    remaining = remaining.flatMap(([start, end]) => {
      if (hi < start || lo > end) return [[start, end]];
      return [...(start < lo ? [[start, lo - 1]] : []), ...(hi < end ? [[hi + 1, end]] : [])];
    });
  }
  return normalizeRanges(remaining);
}

const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
function normalizeSourceCoverage(entries) {
  const byScope = new Map();
  for (const item of (entries || []).slice(0, 64)) {
    if (!item || !["utf8_byte", "line"].includes(item.unit)) continue;
    const start = item.unit === "line" ? 1 : 0;
    const ranges = normalizeRanges((item.ranges || []).slice(0, 80)).filter(r => r[0] >= start);
    if (!ranges.length) continue;
    const provider = String(item.provider || "").slice(0, 200);
    const query = String(item.query || "").slice(0, 256);
    const representation = String(item.representation || "source").slice(0, 100);
    const key = JSON.stringify([item.unit, provider, query, representation]);
    const total = integer(item.total);
    const previous = byScope.get(key);
    const extentConflict = item.extentConflict === true || previous?.extentConflict === true
      || Boolean(previous && previous.total !== null && total !== null && previous.total !== total);
    const extent = extentConflict ? null : total ?? previous?.total ?? null;
    const merged = normalizeRanges([...(previous?.ranges || []), ...ranges]);
    // Retention can remove evidence, never fill gaps or invent completeness.
    const retained = merged.length <= 16 ? merged : [...merged.slice(0, 8), ...merged.slice(-8)];
    const complete = extent !== null && retained.length === 1 && retained[0][0] === start
      && retained[0][1] === extent - (start === 0 ? 1 : 0);
    const next = integer(item.nextOffsetBytes);
    byScope.set(key, { unit: item.unit, provider, query, representation, ranges: retained,
      total: extent, ...(extentConflict ? { extentConflict: true } : {}),
      ...(item.unit === "utf8_byte" && next !== null ? { nextOffsetBytes: next } : {}),
      state: complete ? "complete" : "partial" });
  }
  return { schemaVersion: 1, entries: [...byScope.values()].slice(-16) };
}

function sourceCoverage(item) {
  if (item.sourceCoverage?.schemaVersion === 1 && Array.isArray(item.sourceCoverage.entries))
    return normalizeSourceCoverage(item.sourceCoverage.entries);
  const scope = { provider: item.provider, query: item.sourceQueryKey,
    representation: item.representation || "source" };
  const entries = [];
  const start = integer(item.offsetBytes), end = integer(item.nextOffsetBytes), total = integer(item.size);
  if (start !== null && end !== null && end > start && (total === null || end <= total))
    entries.push({ ...scope, unit: "utf8_byte", ranges: [[start, end - 1]], total, nextOffsetBytes: end });
  const lines = [...(Array.isArray(item.observedLineRanges) ? item.observedLineRanges : []),
    { startLine: item.startLine, endLine: item.endLine }]
    .filter(r => integer(r.startLine) !== null && r.startLine >= 1 && integer(r.endLine) !== null && r.endLine >= r.startLine)
    .map(r => [r.startLine, r.endLine]);
  if (lines.length) entries.push({ ...scope, unit: "line", ranges: lines,
    total: integer(item.totalLinesAtObservation ?? item.totalLines) });
  return normalizeSourceCoverage(entries);
}

module.exports = { normalizeRanges, subtractRanges, normalizeSourceCoverage, sourceCoverage };
