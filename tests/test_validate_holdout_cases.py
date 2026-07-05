from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import error_taxonomy  # noqa: E402
import module_resolver  # noqa: E402
import validate_holdout_cases  # noqa: E402


CONFIG_PATH = ROOT / "config" / "rag_eval_real_project_holdout_cases.json"


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def test_real_project_holdout_config_validates():
    config = _load_config()
    errors, summary = validate_holdout_cases.validate_cases(config)

    assert errors == []
    assert summary["caseCount"] == 36
    assert summary["taxonomyCoveredCases"] >= 10
    assert summary["moduleResolverCoveredCases"] >= 8
    assert all(case.get("evalTier") for case in config["cases"])
    assert sum(1 for case in config["cases"] if case.get("evalTier") == "multifile_refactor") == 12


def test_duplicate_id_detection():
    config = _load_config()
    duplicate = copy.deepcopy(config["cases"][0])
    duplicate["id"] = config["cases"][0]["id"]
    config["cases"].append(duplicate)

    errors, _summary = validate_holdout_cases.validate_cases(config)

    assert any("duplicate id" in error for error in errors)


def test_public_path_hygiene_catches_local_absolute_paths():
    config = _load_config()
    config["cases"][0]["errorLog"] = (
        "C:" + "\\Users\\Example\\Project\\Source\\Demo.cpp: fatal error C1083"
    )

    errors, _summary = validate_holdout_cases.validate_cases(config)

    assert any("local/private absolute path" in error for error in errors)


def test_taxonomy_coverage_for_generated_h_and_c1083():
    generated = error_taxonomy.route_error_action(
        "error: #include found after .generated.h file - the .generated.h file should always be the last #include"
    )
    c1083 = error_taxonomy.route_error_action(
        "fatal error C1083: Cannot open include file: 'GameplayTagContainer.h': No such file or directory"
    )

    assert generated["errorSubkind"] == "GENERATED_H_NOT_LAST"
    assert c1083["broadMode"] == "module_fix"
    assert c1083["errorSubkind"] in {"C1083_MISSING_INCLUDE", "MISSING_INCLUDE_OWNER_MODULE", "INCLUDE_GENERIC"}


def test_module_resolver_coverage_for_required_holdout_modules():
    text = "GameplayTagContainer.h EnhancedInputComponent.h Blueprint/UserWidget.h Interfaces/IPluginManager.h"

    modules = module_resolver.resolve_modules_from_text(text)

    assert "GameplayTags" in modules
    assert "EnhancedInput" in modules
    assert "UMG" in modules
    assert "Projects" in modules


def test_validate_holdout_cases_cli_success():
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_holdout_cases.py"),
            "--config",
            str(CONFIG_PATH),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Holdout validation OK" in proc.stdout
