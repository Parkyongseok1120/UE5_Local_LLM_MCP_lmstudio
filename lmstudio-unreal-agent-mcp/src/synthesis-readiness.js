"use strict";

const fs = require("fs");
const path = require("path");
const { invokeCanonicalControl } = require("./task-control-transition");

const POLICY = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, "../../config/synthesis_readiness_policy.json"),
  "utf8",
));

function deriveSynthesisReadiness(state = {}) {
  return invokeCanonicalControl("derive_synthesis_readiness", state).readiness || {};
}

function synthesisLatchMatches(state = {}) {
  return invokeCanonicalControl("synthesis_latch_matches", state).value === true;
}

module.exports = { POLICY, deriveSynthesisReadiness, synthesisLatchMatches };
