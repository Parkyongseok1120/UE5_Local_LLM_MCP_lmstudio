"use strict";

/**
 * Node smoke mirror for logic-claim false-positive guards.
 * Full coverage lives in tests/test_review_claim_validate_logic.py (pytest).
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const os = require("os");

const LOGIC_MISSING = /(누락|로직\s*없음|missing\s+logic|does\s+nothing|should\s+call\s+SetActorTransform|버그)/i;
const BY_DESIGN = /(그대로\s*사용|에셋에\s*저장된|authored\s+world|as\s+authored|Level\s+Sequence\s+에셋에\s+저장된)/i;
const MODE = /\b(AuthoredWorld|ExplicitTransform|InstigatorToSubject|ApplyDynamicTransform)\b/;

function findByDesignNearMode(headerText, tokens) {
  const lines = headerText.split(/\r?\n/);
  const tokenRe = new RegExp(tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "i");
  for (let i = 0; i < lines.length; i += 1) {
    if (!tokenRe.test(lines[i])) continue;
    const window = lines.slice(Math.max(0, i - 5), Math.min(lines.length, i + 5)).join("\n");
    if (BY_DESIGN.test(window)) return true;
  }
  return false;
}

test("authored world false-positive claim is rejected by by_design heuristic", () => {
  const casesPath = path.join(__dirname, "..", "config", "rag_eval_project_review_cases.json");
  const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
  const c = cases.cases.find((row) => row.id === "project_example_authored_world_by_design");
  assert.ok(c, "case missing");
  const claim = c.badAnswerFixture;
  const header = c.snippets[0].content;
  assert.ok(LOGIC_MISSING.test(claim));
  assert.ok(MODE.test(claim));
  assert.ok(findByDesignNearMode(header, ["AuthoredWorld"]));
});

test("core review case list includes authored world false positive", () => {
  const casesPath = path.join(__dirname, "..", "config", "rag_eval_project_review_cases.json");
  const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
  const ids = cases.cases.map((row) => row.id);
  assert.ok(ids.includes("project_example_authored_world_by_design"));
  assert.ok(ids.includes("project_example_damage_semantics_and_wiring"));
});

test("damage semantics bad fixture contains the framework and wiring traps", () => {
  const casesPath = path.join(__dirname, "..", "config", "rag_eval_project_review_cases.json");
  const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
  const c = cases.cases.find((row) => row.id === "project_example_damage_semantics_and_wiring");
  assert.ok(c, "case missing");
  assert.match(c.badAnswerFixture, /Super::TakeDamage/);
  assert.match(c.badAnswerFixture, /HealthComponent/);
  assert.ok(c.requiredOutputPatterns.some((pattern) => pattern.includes("BehaviorPath")));
});

test("tool_orchestration review gates mention logic-missing validate", () => {
  const orch = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "config", "tool_orchestration.json"), "utf8")
  );
  assert.ok(
    orch.tasks.project_review.gates.includes(
      "unreal_review_claim_validate_negative_and_logic_missing"
    )
  );
});
