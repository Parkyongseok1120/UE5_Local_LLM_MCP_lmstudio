from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_proposal_store import (  # noqa: E402
    apply_proposal_repairs,
    load_proposal_draft,
    merge_proposal_patch,
    save_proposal_draft,
)


def test_proposal_patch_merges_objects_and_replaces_arrays(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    base = {
        "decision": "reuse",
        "networking": {"authorityOwner": "Rules", "requestPath": ["old"]},
        "validationPlan": ["old"],
    }
    merged = merge_proposal_patch(
        base,
        {
            "networking": {"requestPath": ["client", "rpc", "rules"]},
            "validationPlan": ["static", "build"],
        },
    )
    assert merged["networking"] == {
        "authorityOwner": "Rules",
        "requestPath": ["client", "rpc", "rules"],
    }
    assert merged["validationPlan"] == ["static", "build"]
    assert base["networking"]["requestPath"] == ["old"]

    revision = save_proposal_draft("chat-1", "/portable/project", merged)
    loaded = load_proposal_draft("chat-1", "/portable/project")
    assert loaded == {"proposal": merged, "revision": revision}


def test_proposal_repairs_replace_only_exact_dotted_paths():
    base = {
        "decision": "reuse",
        "networking": {
            "authorityOwner": "Rules",
            "requestPath": ["old"],
        },
        "migrationPlan": [],
    }
    repaired = apply_proposal_repairs(
        base,
        [
            {
                "jsonPath": "networking.requestPath",
                "value": ["client", "owned rpc", "server rules"],
            },
            {"jsonPath": "migrationPlan", "value": ["add compatible request path"]},
        ],
    )

    assert repaired == {
        "decision": "reuse",
        "networking": {
            "authorityOwner": "Rules",
            "requestPath": ["client", "owned rpc", "server rules"],
        },
        "migrationPlan": ["add compatible request path"],
    }
    assert base["networking"]["requestPath"] == ["old"]


def test_proposal_repairs_reject_array_indexes_and_missing_values():
    try:
        apply_proposal_repairs({}, [{"jsonPath": "stateInventory.0.owner", "value": "Rules"}])
    except ValueError as exc:
        assert "invalid proposal repair jsonPath" in str(exc)
    else:
        raise AssertionError("array index path should fail")

    try:
        apply_proposal_repairs({}, [{"jsonPath": "networking.requestPath"}])
    except ValueError as exc:
        assert "missing value" in str(exc)
    else:
        raise AssertionError("repair without value should fail")
