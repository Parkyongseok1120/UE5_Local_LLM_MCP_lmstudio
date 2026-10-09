"use strict";
// Shared by the execution policy and the durable window decoder.
const SUMMARY_POLICY = Object.freeze({ maxCalls: 2, maxElapsedMs: 60000,
  failureCooldownRounds: 3, retainedEvents: 32 });
module.exports = { SUMMARY_POLICY };
