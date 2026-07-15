from pathlib import Path

from unreal_static_validate import (
    build_delegate_arity_map,
    validate_delegate_broadcast_consistency,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_delegate_broadcast_two_param_delegate_flagged_when_one_arg(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "Stamina.h"
    cpp = project / "Source" / "Demo" / "Private" / "Stamina.cpp"
    _write(
        header,
        """DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FOnStaminaChanged, float, OldValue, float, NewValue);
class UStamina
{
public:
    FOnStaminaChanged OnStaminaChanged;
};
""",
    )
    _write(cpp, "void Trigger(float Value) { OnStaminaChanged.Broadcast(Value); }\n")
    findings = validate_delegate_broadcast_consistency(
        cpp, cpp.read_text(encoding="utf-8"), project, build_delegate_arity_map(project)
    )
    mismatch = next(item for item in findings if item.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH")
    assert "passes 1 argument(s)" in mismatch.message
    assert "requires 2" in mismatch.message


def test_delegate_broadcast_nested_expression_counts_as_one_arg(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "Score.h"
    cpp = project / "Source" / "Demo" / "Private" / "Score.cpp"
    _write(
        header,
        """DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnScoreChanged, float, Score);
class UScore { public: FOnScoreChanged OnScoreChanged; };
""",
    )
    _write(cpp, "void Trigger() { OnScoreChanged.Broadcast(FMath::Clamp(GetScore(1, 2), 0, 10)); }\n")
    findings = validate_delegate_broadcast_consistency(
        cpp, cpp.read_text(encoding="utf-8"), project, build_delegate_arity_map(project)
    )
    assert not any(item.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" for item in findings)
