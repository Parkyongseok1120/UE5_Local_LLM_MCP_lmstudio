"use strict";
const fs = require("node:fs"), path = require("node:path");
const { fail, hash, fittedPage } = require("../../shared-tool-core/files");
const { lexicalFinding } = require("../../shared-tool-core/finding-model");

function mask(text) {
  // Preserve offsets and line numbers; comments and literals cannot be evidence.
  return text.replace(/\/\*[\s\S]*?\*\/|\/\/[^\r\n]*|\$*"""[\s\S]*?"""|(?:\$?@|@\$)"(?:""|[^"])*"|\$?"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'/g,
    value => value.replace(/[^\r\n]/g, " "));
}
function closing(code, start) {
  let depth = 1;
  for (let n = start + 1; n < code.length; n++) {
    if (code[n] === "{") depth++;
    if (code[n] === "}" && --depth === 0) return n;
  }
  return code.length;
}
function analyzeSource(text, file = "Assets/Unknown.cs", maxFindings = 1000) {
  const code = mask(text), methods = [], owners = [];
  for (const m of code.matchAll(/\b(?:class|struct)\s+\w+[^;{}]*\{/g)) {
    const start = m.index + m[0].length - 1;
    owners.push({ start, end: closing(code, start) });
  }
  for (const m of code.matchAll(/\b((?:(?:public|private|protected|internal|static|virtual|override|sealed|async|new|partial)\s+)*[\w<>?.\[\],]+\s+)(\w+)\s*\([^;{}]*\)\s*\{/g)) {
    const start = m.index + m[0].length - 1;
    const owner = owners.filter(o => o.start < start && o.end > start).at(-1);
    if (!owner) continue;
    methods.push({ name: m[2], signature: m[1], body: code.slice(start + 1, closing(code, start)), start: start + 1, owner });
  }
  const findings = [], starts = [0];
  for (const line of code.matchAll(/\r\n|\r|\n/g)) starts.push(line.index + line[0].length);
  function lineAt(offset) { let low=0, high=starts.length-1; while(low<=high) { const mid=(low+high)>>1; if(starts[mid]<=offset) low=mid+1; else high=mid-1; } return high+1; }
  function add(rule, method, offset, message, confidence, contract) {
    if (findings.length >= maxFindings) { findings.incomplete = true; return false; }
    findings.push(lexicalFinding(rule, file, lineAt(method.start + offset),
      message, confidence, [contract]));
    return true;
  }
  for (const method of methods) {
    if (findings.incomplete) break;
    if (["OnEnable", "Awake", "Start"].includes(method.name)) {
      for (const sub of method.body.matchAll(/([\w.]+)\s*\+=\s*([\w.]+)\s*;/g)) {
        if (findings.incomplete) break;
        const cleanup = methods.filter(m => m.owner === method.owner && ["OnDisable", "OnDestroy"].includes(m.name));
        const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const removed = new RegExp(`${escape(sub[1])}\\s*-=\\s*${escape(sub[2])}\\s*;`);
        if (!cleanup.some(m => removed.test(m.body))) add("UNITY_EVENT_CLEANUP", method, sub.index,
          "Subscription has no matching cleanup observed in this type's OnDisable/OnDestroy; inspect ownership and inherited cleanup.", "medium", "unity.policy.events");
      }
    }
    if (/\basync\b/.test(method.signature)) {
      const awaitAt = method.body.search(/\bawait\b/), tail = method.body.slice(Math.max(0, awaitAt));
      const access = /\b(?:this|transform|gameObject)\s*[.\[]|\b(?:GetComponent|GetComponentInChildren)\s*[<(]/.exec(tail);
      const guard = /\bdestroyCancellationToken\b|\b(?:ThrowIfCancellationRequested|IsCancellationRequested)\b|\bif\s*\([^)]*(?:!\s*this|this\s*[!=]=\s*null|(?:transform|gameObject)\s*==\s*null)/.test(method.body);
      if (awaitAt >= 0 && access && !guard) add("UNITY_ASYNC_LIFETIME", method, awaitAt + access.index,
        "Object access after await has no local destruction/cancellation guard observed; inspect cancellation propagation and object lifetime.", "medium", "unity.policy.async");
    }
    if (["Update", "FixedUpdate"].includes(method.name)) {
      for (const lookup of method.body.matchAll(/\b(?:GetComponent(?:InChildren|InParent)?|Find(?:ObjectOfType|ObjectsOfType|FirstObjectByType|AnyObjectByType|ObjectsByType|WithTag)?)\s*(?:<[^;{}>]+>)?\s*\(/g))
        if (!add("UNITY_HOT_LOOP_LOOKUP", method, lookup.index, "Lookup in a frame callback; measure frequency/cost before changing cache lifetime.", "medium", "unity.policy.hot-loop")) break;
    }
    const coroutine = /\bStartCoroutine\s*\(/.exec(method.body);
    if (coroutine && !["Start", "OnEnable"].includes(method.name)
      && !/\b(?:activeInHierarchy|isActiveAndEnabled)\b/.test(method.body))
      add("UNITY_COROUTINE_ACTIVATION", method, coroutine.index, "Coroutine start may run without an active GameObject; inspect callers and activation state.", "low", "unity.policy.coroutine");
  }
  return findings;
}
async function staticValidate(policy, files, args, envelope = value => value, { signal } = {}) {
  const relative = args.path || "Assets";
  if (relative !== "Assets" && !relative.startsWith("Assets/")) fail("path_denied", "Static validation is scoped to Assets C# sources");
  const root = policy.resolve(relative, false), findings = [], inputs = [], omitted = [];
  let nodes = 0, bytes = 0;
  async function visit(target, display) {
    if (signal?.aborted) fail("request_cancelled", "Static validation cancelled");
    if (++nodes > 10000) { if (!omitted.includes("node_limit")) omitted.push("node_limit"); return; }
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) { if (omitted.length < 32) omitted.push(display); return; }
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(target).sort()) {
        if (nodes >= 10000) { if (omitted.length < 32 && !omitted.includes("node_limit")) omitted.push("node_limit"); break; }
        await visit(path.join(target, name), `${display}/${name}`);
      }
    } else if (display.endsWith(".cs")) {
      if (findings.length >= 1000 || inputs.length >= 500 || bytes + stat.size > 8 * 1024 * 1024) { if (omitted.length < 32) omitted.push(display); return; }
      try {
        const source = await files.snapshot(display); bytes += source.buffer.length;
        inputs.push([display, source.hash]);
        const analyzed = analyzeSource(source.content, display, 1000 - findings.length);
        findings.push(...analyzed); if (analyzed.incomplete && omitted.length < 32) omitted.push("finding_limit:" + display);
      } catch { if (omitted.length < 32) omitted.push(display); }
    }
  }
  await visit(root, relative);
  const revision = hash(JSON.stringify([relative, inputs, omitted]));
  return fittedPage(findings, args, revision, paged => envelope({ status: "observed", ...paged,
    findings: paged.items, analysis: { analysisKind: "lexical", warningOnly: true, filesScanned: inputs.length,
      contractRevision: "unity_contracts@1", coverage: omitted.length ? "partial" : "scanned_sources", omitted },
    absenceVerified: false }), args.byteBudget);
}
module.exports = { mask, analyzeSource, staticValidate };
