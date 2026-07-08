from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ubt_utils import sanitize_ubt_target, ubt_subprocess_env  # noqa: E402


def test_ubt_subprocess_env_sets_dotnet_roll_forward() -> None:
    env = ubt_subprocess_env({})
    assert env["DOTNET_ROLL_FORWARD"] == "LatestMajor"
    env = ubt_subprocess_env({"DOTNET_ROLL_FORWARD": "Disable"})
    assert env["DOTNET_ROLL_FORWARD"] == "Disable"


def test_sanitize_ubt_target_rejects_header_path() -> None:
    sanitized, reason = sanitize_ubt_target(
        r"C:\Temp\HoldoutFixture\Source\HoldoutFixture\Public\HoldoutGlobalSubsystem.h"
    )
    assert sanitized == "HoldoutFixtureEditor"
    assert reason is not None


def test_build_ubt_command_includes_log_file(tmp_path: Path) -> None:
    from ubt_utils import build_ubt_command

    log_file = tmp_path / "Saved" / "Logs" / "case.log"
    cmd = build_ubt_command(
        Path("UnrealBuildTool.exe"),
        tmp_path / "Demo.uproject",
        "DemoEditor",
        log_file=log_file,
    )
    assert f"-Log={log_file}" in cmd
