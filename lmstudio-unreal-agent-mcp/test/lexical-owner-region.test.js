"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { lexicalOwnerRegion, lexicalSource } = require("../src/direct-symbol-extraction");
const fixtures = require("../../tests/fixtures/lexical-owner-regions.json");
test("owner regions share Python fixtures, including ambiguous and unclosed owners", () => {
  for (const item of fixtures) {
    const region = lexicalOwnerRegion(lexicalSource(item.source), item.owner);
    assert.equal(Boolean(region), item.found, item.source);
    if (region) {
      assert.equal(item.source[region.open], "{");
      assert.equal(item.source[region.close], "}");
    }
  }
});
