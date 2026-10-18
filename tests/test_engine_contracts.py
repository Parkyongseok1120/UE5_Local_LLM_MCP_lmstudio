import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import engine_contracts as contracts


def test_unity_contracts_keep_verified_version_and_evidence_root():
    data = contracts.load_contracts(engine="unity")
    assert data["validatedEngineVersions"] == ["6000.3"]
    assert contracts.contract_revision(data) == "unity_contracts@1"
    facts = [c for c in data["contracts"] if c["kind"] == "engine_fact"]
    assert len(facts) == 2
    assert all(ref["path"].startswith("Data/") for c in facts for ref in c["evidenceRefs"])


@pytest.mark.parametrize("path", ["Engine/Source/A.cs", "Data/../secret.xml", "C:/Data/a.xml", "Data/Managed\\a.xml"])
def test_unity_evidence_cannot_escape_editor_root(path):
    data = copy.deepcopy(contracts.load_contracts(engine="unity"))
    data["contracts"][0]["evidenceRefs"][0]["path"] = path
    with pytest.raises(contracts.ContractError, match="engine-root relative"):
        contracts.validate_contracts(data, expected_engine="unity")


def test_engine_facade_rejects_cross_engine_data():
    import unreal_contracts
    with pytest.raises(contracts.ContractError, match="engine must be"):
        unreal_contracts.validate_contracts(contracts.load_contracts(engine="unity"))
