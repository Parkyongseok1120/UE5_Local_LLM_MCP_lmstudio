"""Cross-runtime contract checks for Python-issued requestIntent v1."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CORE = ROOT / "lmstudio-context-compactor-plugin" / "src" / "compaction-core.js"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import build_agent_plan, build_request_intent  # noqa: E402


NODE_PROBE = r"""
const fs = require('node:fs');
const core = require(process.argv[1]);
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const matching = core.compactRequestIntent(payload.intent, payload.objective);
const stale = core.compactRequestIntent(
  payload.intent,
  `${payload.objective} changed objective`,
);
process.stdout.write(JSON.stringify({
  objectiveHash: core.objectiveHashOf(payload.objective),
  matching,
  stale,
  classification: core.classifyUserIntent(payload.objective, {
    requestIntent: payload.intent,
  }),
}));
"""


def _node_probe(objective: str, intent: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Python/JS intent parity contract")
    completed = subprocess.run(
        [node, "-e", NODE_PROBE, str(CORE)],
        input=json.dumps({"objective": objective, "intent": intent}, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_python_read_only_intent_prevents_js_mutation_escalation_and_stale_reuse():
    objective = "Do not modify source; only analyze how the implementation works"
    intent = build_request_intent(objective, "cpp_analysis")

    result = _node_probe(objective, intent)

    assert intent["mutability"] == "none"
    assert intent["negated"] is True
    assert result["objectiveHash"] == intent["objectiveHash"]
    assert result["matching"] == intent
    assert result["classification"] == "READ_ONLY"
    assert result["stale"] is None


def test_python_source_mutation_intent_prevents_js_read_only_downgrade():
    objective = "소스 파일의 처리 흐름을 개선해"
    plan = build_agent_plan(objective, "auto").to_dict()
    intent = plan["requestIntent"]

    result = _node_probe(objective, intent)

    assert plan["taskKind"] == "edit"
    assert intent["mutability"] == "source_files"
    assert result["matching"] == intent
    assert result["classification"] == "MUTATION"
    assert result["stale"] is None


@pytest.mark.parametrize(
    "objective",
    [
        "\u0085Analyze the active project only\u0085",
        "\u00a0\u2003Analyze the active project only\u3000",
    ],
)
def test_python_and_js_share_the_exact_objective_trim_protocol(objective: str):
    intent = build_request_intent(objective, "cpp_analysis")

    result = _node_probe(objective, intent)

    assert result["objectiveHash"] == intent["objectiveHash"]
    assert result["matching"] == intent
