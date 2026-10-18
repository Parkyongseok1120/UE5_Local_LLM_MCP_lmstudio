"""Compatibility facade for the Unreal contract consumers."""
from engine_contracts import (
    CONTRACTS_PATH,
    ContractError,
    contract_by_id,
    contract_revision,
    contracts_for_topic,
    engine_version_tier,
    engine_version_validated,
    load_contracts,
    load_contracts_or_none,
    related_contracts,
    render_contract_text,
)
from engine_contracts import validate_contracts as _validate_contracts


def validate_contracts(data):
    return _validate_contracts(data, expected_engine="unreal")
