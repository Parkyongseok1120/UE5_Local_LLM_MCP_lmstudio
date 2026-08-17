from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from control_protocol_spec import control_protocol_identity, load_control_protocol_spec
from validate_control_protocol import (
    discover_emitted_error_codes,
    flatten_error_catalog,
    validate_spec,
)


ROOT = Path(__file__).resolve().parents[1]


def test_every_emitted_literal_error_code_has_one_conservative_classification() -> None:
    spec = load_control_protocol_spec(repository_root=ROOT)
    result = validate_spec(spec)
    catalog, errors = flatten_error_catalog(spec["errorCatalog"])

    assert not errors
    assert result["emittedErrorCodeCount"] == len(discover_emitted_error_codes())
    assert set(catalog) == set(discover_emitted_error_codes())
    assert any(policy["terminal"] for policy in catalog.values())
    assert sum(
        policy["retry"] == "same_call_after_refresh" for policy in catalog.values()
    ) < len(catalog) // 10
    assert catalog["TASK_AUTH_INCOMPLETE"]["retry"] == "same_call_after_refresh"
    assert catalog["INVALID_TOOL_ARGUMENTS"]["retry"] == "changed_input"
    assert catalog["CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH"]["terminal"] is True
    assert catalog["CONTROL_RUNTIME_VERSION_MISMATCH"]["terminal"] is True
    assert "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH" in discover_emitted_error_codes()


def test_unclassified_emitted_code_fails_the_gate() -> None:
    spec = copy.deepcopy(load_control_protocol_spec(repository_root=ROOT))
    first_category = next(iter(spec["errorCatalog"].values()))
    first_group = next(values for values in first_category.values() if values)
    removed = first_group.pop()

    with pytest.raises(ValueError, match=removed):
        validate_spec(spec)


def test_transition_and_authorization_schema_cover_current_cross_runtime_contract() -> None:
    spec = load_control_protocol_spec(repository_root=ROOT)
    transition = spec["transitionPolicy"]
    authorization = spec["authorizationSchema"]

    assert set(transition["dispositions"]) == {
        "continue",
        "require_tool",
        "rediscover",
        "checkpoint",
        "await_user",
        "workflow_stop",
        "complete",
    }
    assert {
        "evidence_complete",
        "environment_recovery",
        "evidence_required",
        "repair_planning_required",
        "revalidate_required",
        "checkpoint_rebase_required",
        "phase_budget_checkpoint_required",
        "repair_required",
    }.issubset(transition["recoveryStatuses"])
    assert set(authorization["secretFields"]) == {"authToken", "ownerCapability"}
    assert set(authorization["routeBindingFields"]).issubset(authorization["requiredFields"])


def test_protocol_identity_hashes_are_deterministic() -> None:
    first = control_protocol_identity(repository_root=ROOT)
    second = control_protocol_identity(repository_root=ROOT)

    assert first == second
    for field, value in first.items():
        if field.endswith("Hash"):
            assert len(value) == 64
            int(value, 16)
