"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { BatchReservation, reserveReadResult } = require("../dist/budget-broker");
const source = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
  byteBudget: { type: "integer", minimum: 2048, maximum: 65536, $comment: "direct-json-text-utf8" } } } };
const search = { name: "search_files", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
  maxChars: { type: "integer", minimum: 656, maximum: 20000, default: 20000 } } } };

test("v3 five parallel known-size reads leave room for a sixth", () => {
  // Observed old allowances: 3255/5863/3475/4060/6422; received allowances
  // (old +128): 1749/3835/1925/2393/835. Only sizes, no user source.
  const batch = new BatchReservation(23075, 1844);
  const bytes = [1749, 3835, 1925, 2393].map(tokens => (tokens - 128) * 2.5);
  const reads = bytes.map((size, i) => reserveReadResult(batch, String(i), source, {}, Math.floor(size)));
  assert.ok(reads.every(r => r.allowed));
  assert.ok(reads.reduce((sum, r) => sum + r.reservedTokens, 0) <= 10414);
  assert.equal(reserveReadResult(batch, "search-a", search, {}).allowed, true);
  assert.equal(reserveReadResult(batch, "search-b", search, { maxChars: 656 }).allowed, true);
});

test("v3 validator tool is not denied by read-result budget", () => {
  const validator = { name: "evidence_first_validate", pluginIdentifier: "mcp/evidence-first",
    parametersJsonSchema: { properties: { packet: { type: "object" } } } };
  assert.equal(reserveReadResult(new BatchReservation(1469), "validate", validator, { packet: {} }).allowed, true);
});

test("v3 single-request guard sees no batch total", () => {
  const batch = new BatchReservation(10000, 1844);
  // The later request has not been parsed. No count or look-ahead is provided.
  assert.equal(reserveReadResult(batch, "first", search, {}).allowed, true);
  assert.ok(batch.available >= 1844);
  assert.equal(reserveReadResult(batch, "later", search, { maxChars: 656 }).allowed, true);
});

test("v3 an unknown-size first read keeps its expansion margin", () => {
  const batch = new BatchReservation(20000, 1844);
  const result = reserveReadResult(batch, "new", source, { byteBudget: 4096 });
  assert.equal(result.reservedTokens, Math.ceil(4096 / 2.5) + 1024);
});
