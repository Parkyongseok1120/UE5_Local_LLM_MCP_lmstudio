"use strict";

// Deterministic scoring of one recovery run from what was recorded: the call
// that met the scenario's failure, the calls after it, and the final text.
// Anything the record does not contain is "not_recorded", never a pass.

// Harness footers are deterministic notices, never model wording.
const HARNESS_FOOTERS = /^\[(?:검증|검색) 범위\][^\n]*$/gmu;
const VERIFICATION_FOOTER = /^\[검증 범위\][^\n]*$/mu;
const SEARCH_FOOTER = /^\[검색 범위\][^\n]*$/mu;

function modelText(answer) {
  return String(answer || "").replace(HARNESS_FOOTERS, "").trim();
}

function pathOf(call) {
  return String(call?.args?.path || "").replace(/\\/g, "/");
}

function sameCall(a, b) {
  return a.name === b.name && JSON.stringify(a.args || {}) === JSON.stringify(b.args || {});
}

function check(id, status, detail) {
  return { id, status, ...(detail ? { detail } : {}) };
}

function successfulObservation(call) {
  const result = call?.result;
  return (result?.ok === true || ["observed", "completed", "accepted"].includes(result?.status)) && !result.errorCode && !result.duplicate
    && !["failed", "error", "outcome_unknown", "no_new_information"].includes(result.status);
}

function returnedBody(call) {
  if (!successfulObservation(call)) return "";
  return [call.result.content, call.result.text, call.result.body].find(value => typeof value === "string") || "";
}

function scoreFollowUp(expect, fault, after) {
  if (!fault) return check("follow_up", "not_recorded", "the failure call was not reached");
  const wanted = after.filter(call => expect.tools.includes(call.name));
  if (!wanted.length) return check("follow_up", "fail", "no recovery call after the failure");
  const good = wanted.find(call => {
    if (!successfulObservation(call)) return false;
    if (expect.notIdentical && sameCall(call, fault)) return false;
    if (expect.pathPrefix && !pathOf(call).startsWith(expect.pathPrefix)) return false;
    if (expect.pathIncludes && !pathOf(call).includes(expect.pathIncludes)) return false;
    if (expect.continues) {
      const next = fault.result?.nextOffsetBytes;
      const offset = Number(call.args?.offsetBytes || 0);
      const larger = Number(call.args?.byteBudget || 0) > Number(fault.args?.byteBudget || 0)
        || (call.args?.byteBudget === undefined && fault.args?.byteBudget !== undefined);
      if (!(Number.isFinite(next) && offset === next) && !larger && call.name !== "read_file_range") return false;
    }
    return true;
  });
  return good ? check("follow_up", "pass", `${good.name} ${pathOf(good)}`)
    : check("follow_up", wanted.some(call => !call.result) ? "not_recorded" : "fail",
      "no successful recovery call with recorded results in the expected scope");
}

// A guessed answer or a successful filename search does not prove that the
// model obtained the source. Require the scenario's evidence in returned text
// from that exact file; keywords in arguments, errors or final prose do not count.
function scoreSourceEvidence(expect, fault, after, faultExpected) {
  if (faultExpected && !fault) return check("source_evidence", "not_recorded");
  const reads = after.filter(call => ["read_file", "read_file_range", "read_symbol"].includes(call.name)
    && pathOf(call) === expect.path);
  const bodies = reads.map(returnedBody).filter(Boolean);
  // A search line returned from the exact file is source text too, when the
  // scenario allows it: the assignment line itself, not a filename match.
  if (expect.searchLines) {
    for (const call of after.filter(item => item.name === "search_files" && successfulObservation(item))) {
      for (const line of call.result.results || []) if (line.uri === expect.path && typeof line.text === "string") bodies.push(line.text);
    }
  }
  const found = bodies.length > 0 && (expect.includes || []).every(token => bodies.some(body => body.includes(token)));
  return check("source_evidence", found ? "pass" : reads.some(call => !call.result) ? "not_recorded" : "fail",
    found ? expect.path : "required source text was not returned from the expected file");
}

function scoreRun(scenario, run) {
  const expect = scenario.expect || {};
  const calls = Array.isArray(run?.calls) ? run.calls : [];
  // A scenario without an injected failure scores the whole run from its start.
  const faultExpected = Boolean(scenario.faultCall);
  const fault = calls.find(call => call.faultCall === true) || null;
  const after = !faultExpected ? calls : fault ? calls.slice(calls.indexOf(fault) + 1) : [];
  const text = modelText(run?.answer);
  const checks = [];
  if (run?.error) checks.push(check("run_error", "fail", String(run.error)));
  if (faultExpected) {
    checks.push(fault ? check("failure_reached", "pass", fault.name) : check("failure_reached", "not_recorded"));
    const identicalRepeats = fault && !expect.allowIdenticalRetry ? after.filter(call => sameCall(call, fault)).length : 0;
    checks.push(fault ? check("no_identical_repeat", identicalRepeats ? "fail" : "pass", identicalRepeats ? `${identicalRepeats}x` : "")
      : check("no_identical_repeat", "not_recorded"));
  }
  for (const fact of expect.resultFacts || []) {
    const observed = calls.some(call => call.name === fact.tool && Object.entries(fact.fields).every(([key, value]) => call.result?.[key] === value));
    checks.push(check(`result:${fact.tool}:${JSON.stringify(fact.fields)}`, observed ? "pass" : "fail"));
  }
  if (expect.followUp) checks.push(scoreFollowUp(expect.followUp, fault, after));
  if (expect.sourceEvidence) checks.push(scoreSourceEvidence(expect.sourceEvidence, fault, after, faultExpected));
  for (const fact of expect.firstInputIncludes || []) {
    const first = Array.isArray(run?.inputs) && run.inputs[0];
    checks.push(first ? check(`input:${fact}`, String(first.text || "").includes(fact) ? "pass" : "fail")
      : check(`input:${fact}`, "not_recorded"));
  }
  if (!run?.answer) checks.push(check("final_answer", "not_recorded"));
  for (const required of expect.finalRequires || []) {
    checks.push(run?.answer ? check(`requires:${required}`, text.includes(required) ? "pass" : "fail")
      : check(`requires:${required}`, "not_recorded"));
  }
  for (const forbidden of expect.finalForbids || []) {
    const matched = new RegExp(forbidden, "iu").test(text);
    checks.push(run?.answer ? check(`forbids:${forbidden}`, matched ? "fail" : "pass") : check(`forbids:${forbidden}`, "not_recorded"));
  }
  if (expect.harnessFooterWhenForbiddenWording && run?.answer) {
    const wording = (expect.finalForbids || []).some(pattern => new RegExp(pattern, "iu").test(text));
    const footer = (expect.harnessFooterWhenForbiddenWording === "search" ? SEARCH_FOOTER : VERIFICATION_FOOTER)
      .test(String(run.answer));
    checks.push(check("harness_scope_footer", wording === footer ? "pass" : "fail",
      wording ? (footer ? "footer present" : "footer missing") : (footer ? "unexpected footer" : "")));
  }
  const statuses = checks.map(item => item.status);
  const status = statuses.includes("fail") ? "fail" : statuses.includes("not_recorded") ? "incomplete" : "pass";
  return { scenario: scenario.id, failureKind: scenario.failureKind, model: run?.model || null, status, checks,
    error: run?.error || null };
}

module.exports = { modelText, scoreRun };
