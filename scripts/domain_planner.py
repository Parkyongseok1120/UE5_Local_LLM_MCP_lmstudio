#!/usr/bin/env python
"""Domain-specific plan slices and fixEvidence helpers for agent orchestrator."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

DomainKind = Literal[
    "generic",
    "component",
    "subsystem",
    "replication",
    "gas",
    "animation",
    "architecture",
]

FIX_EVIDENCE_MAX_CHARS = 4096
FIX_EVIDENCE_MAX_FILES = 2
FIX_EVIDENCE_MAX_SYMBOLS = 4
FIX_EVIDENCE_MAX_FORBIDDEN = 3


@dataclass
class PlanSlice:
    slice_id: str
    title: str
    files: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    required_includes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixEvidence:
    error_subkind: str = ""
    symbols: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    required_includes: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    patch_template: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text_lower(text: str) -> str:
    return str(text or "").lower()


def detect_domain_kind(request: str, mode: str = "auto") -> DomainKind:
    text = _text_lower(f"{mode} {request}")
    if mode in {"prototype_component"} or (
        "component" in text and any(m in text for m in ("createdefaultsubobject", "uactorcomponent", "컴포넌트"))
    ):
        return "component"
    if mode in {"prototype_subsystem"} or any(
        m in text for m in ("subsystem", "ugameinstancesubsystem", "uworldsubsystem", "서브시스템")
    ):
        return "subsystem"
    if any(m in text for m in ("replication", "onrep", "rpc", "doreplicated", "복제")):
        return "replication"
    if any(m in text for m in ("gameplay ability", "gas", "abilitysystem", "gameplayeffect", "attribute set")):
        return "gas"
    if any(m in text for m in ("animinstance", "animnotify", "notifystate", "animation blueprint", "애니")):
        return "animation"
    if any(m in text for m in ("architecture", "ownership", "lifetime", "authority", "아키텍처")):
        return "architecture"
    return "generic"


def select_subsystem_lifetime(request: str) -> dict[str, Any]:
    text = _text_lower(request)
    requested = "world"
    if any(m in text for m in ("gameinstance", "game instance", "session", "게임 인스턴스")):
        requested = "game_instance"
    elif any(m in text for m in ("localplayer", "local player")):
        requested = "local_player"
    elif any(m in text for m in ("world", "level", "map")):
        requested = "world"
    elif any(m in text for m in ("engine", "global")):
        requested = "engine"

    mapping = {
        "world": {
            "recommendedBase": "UWorldSubsystem",
            "rejectedBases": ["UGameInstanceSubsystem", "UEngineSubsystem"],
            "reason": "Per-world state should use UWorldSubsystem.",
        },
        "game_instance": {
            "recommendedBase": "UGameInstanceSubsystem",
            "rejectedBases": ["UWorldSubsystem", "UEngineSubsystem"],
            "reason": "Session/persistent game state should use UGameInstanceSubsystem.",
        },
        "local_player": {
            "recommendedBase": "ULocalPlayerSubsystem",
            "rejectedBases": ["UWorldSubsystem", "UGameInstanceSubsystem"],
            "reason": "Per-local-player state should use ULocalPlayerSubsystem.",
        },
        "engine": {
            "recommendedBase": "UEngineSubsystem",
            "rejectedBases": ["UWorldSubsystem", "UGameInstanceSubsystem"],
            "reason": "Process-wide services should use UEngineSubsystem.",
        },
    }
    payload = dict(mapping.get(requested, mapping["world"]))
    payload["requestedLifetime"] = requested
    return payload


def build_domain_slices(domain_kind: DomainKind, request: str) -> list[PlanSlice]:
    if domain_kind == "component":
        symbol = _first_symbol(request) or "UExampleComponent"
        module_hint = symbol[1:] if symbol.startswith("U") else "ExampleComponent"
        return [
            PlanSlice(
                slice_id="component_scaffold",
                title="Create component header/cpp skeleton",
                files=[f"Public/Components/{module_hint}.h", f"Private/Components/{module_hint}.cpp"],
                postconditions=["UCLASS component compiles", "GENERATED_BODY present", "Tick disabled unless requested"],
                required_includes=["CoreMinimal.h", f"{module_hint}.generated.h"],
            ),
            PlanSlice(
                slice_id="component_registration",
                title="Register component on owner actor",
                files=["<owner>.h", "<owner>.cpp"],
                postconditions=["CreateDefaultSubobject uses concrete include", "member declared in header"],
                required_includes=[f"Components/{module_hint}.h"],
            ),
        ]
    if domain_kind == "subsystem":
        lifetime = select_subsystem_lifetime(request)
        base = lifetime["recommendedBase"]
        symbol = _first_symbol(request) or "UExampleSubsystem"
        module_hint = symbol[1:] if symbol.startswith("U") else "ExampleSubsystem"
        return [
            PlanSlice(
                slice_id="subsystem_header",
                title=f"Declare {base} subclass",
                files=[f"Public/Subsystems/{module_hint}.h"],
                postconditions=[f"inherits {base}", "Initialize/Deinitialize declared"],
                required_includes=[f"Subsystems/{base[1:]}.h"],
            ),
            PlanSlice(
                slice_id="subsystem_impl",
                title="Implement subsystem lifecycle",
                files=[f"Private/Subsystems/{module_hint}.cpp"],
                postconditions=["no CreateDefaultSubobject in ctor", "timers/delegates cleared in Deinitialize"],
                required_includes=[f"Subsystems/{module_hint}.h"],
            ),
        ]
    if domain_kind == "replication":
        return [
            PlanSlice(slice_id="rep_contract", title="Declare replicated properties", files=["<actor>.h"], postconditions=["UPROPERTY(Replicated) present"]),
            PlanSlice(slice_id="rep_onrep", title="Add OnRep handlers", files=["<actor>.h", "<actor>.cpp"], postconditions=["GetLifetimeReplicatedProps updated"]),
            PlanSlice(slice_id="rep_rpc", title="Add Server/Client RPC stubs", files=["<actor>.h", "<actor>.cpp"], postconditions=["authority checks in _Implementation"]),
            PlanSlice(slice_id="rep_validation", title="Validate replication setup", files=["<actor>.cpp"], postconditions=["bReplicates true on actor"]),
        ]
    if domain_kind == "gas":
        return [
            PlanSlice(slice_id="gas_asc", title="Bootstrap AbilitySystemComponent", files=["<character>.h", "<character>.cpp"]),
            PlanSlice(slice_id="gas_ability", title="Grant ability set", files=["<character>.cpp"]),
            PlanSlice(slice_id="gas_attributes", title="Wire attribute set", files=["<attributes>.h", "<attributes>.cpp"]),
        ]
    if domain_kind == "animation":
        return [
            PlanSlice(slice_id="anim_instance", title="AnimInstance hooks", files=["<anim_instance>.h", "<anim_instance>.cpp"]),
            PlanSlice(slice_id="anim_notify", title="Notify/NotifyState lifecycle", files=["<notify>.h", "<notify>.cpp"]),
        ]
    if domain_kind == "architecture":
        return [
            PlanSlice(
                slice_id="architecture_plan",
                title="Ownership and lifetime checklist",
                files=[],
                postconditions=["ownership documented", "authority boundary documented", "no writes until approved"],
            ),
        ]
    return []


def build_fix_evidence(
    request: str,
    error_route: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    route = error_route or {}
    subkind = str(route.get("errorSubkind") or "")
    evidence = FixEvidence(error_subkind=subkind)

    for symbol in re.findall(r"\bU[A-Z][A-Za-z0-9_]+\b", request or ""):
        if symbol not in evidence.symbols:
            evidence.symbols.append(symbol)
        if len(evidence.symbols) >= FIX_EVIDENCE_MAX_SYMBOLS:
            break

    for item in route.get("allowedPatchTargets") or []:
        text = str(item)
        if text not in evidence.target_files:
            evidence.target_files.append(text)
    for item in route.get("forbiddenActions") or []:
        text = str(item)
        if text not in evidence.forbidden_actions:
            evidence.forbidden_actions.append(text)

    evidence.target_files = evidence.target_files[:FIX_EVIDENCE_MAX_FILES]
    evidence.forbidden_actions = evidence.forbidden_actions[:FIX_EVIDENCE_MAX_FORBIDDEN]
    evidence.symbols = evidence.symbols[:FIX_EVIDENCE_MAX_SYMBOLS]

    if subkind == "COMPONENT_REGISTRATION_MISSING_INCLUDE":
        symbol = evidence.symbols[0] if evidence.symbols else "UBoxComponent"
        include_path = "Components/BoxComponent.h"
        patch_target = "referencing cpp"
        if project_root and project_root.is_dir():
            try:
                from include_resolver import resolve_project_symbol_include

                for cpp in (project_root / "Source").rglob("*.cpp"):
                    text = cpp.read_text(encoding="utf-8-sig", errors="replace")
                    if f"CreateDefaultSubobject<{symbol}" in text or f"NewObject<{symbol}" in text:
                        resolution = resolve_project_symbol_include(project_root, symbol, cpp, "create_default_subobject")
                        if resolution:
                            include_path = resolution.preferred_include
                            patch_target = resolution.target_file
                            evidence.reason = resolution.reason
                            break
            except Exception:
                pass
        evidence.required_includes = [include_path]
        evidence.patch_template = (
            f'Missing include for project component {symbol}.\n'
            f'Add: #include "{include_path}"\n'
            f"To: {patch_target}\n"
            "Do not modify Build.cs when owner and consumer modules match."
        )
    elif route.get("notes"):
        evidence.reason = str((route.get("notes") or [""])[0])

    if not any(
        [
            evidence.error_subkind,
            evidence.symbols,
            evidence.target_files,
            evidence.required_includes,
            evidence.patch_template,
        ]
    ):
        return None

    payload = evidence.to_dict()
    payload["errorSubkind"] = payload.pop("error_subkind", "")
    payload["targetFiles"] = payload.pop("target_files", [])
    payload["requiredIncludes"] = payload.pop("required_includes", [])
    payload["forbiddenActions"] = payload.pop("forbidden_actions", [])
    payload["patchTemplate"] = payload.pop("patch_template", "")
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) > FIX_EVIDENCE_MAX_CHARS:
        payload["patch_template"] = payload.get("patch_template", "")[:800]
        payload["reason"] = payload.get("reason", "")[:400]
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized) > FIX_EVIDENCE_MAX_CHARS:
            payload["patch_template"] = payload.get("patch_template", "")[:400]
    return payload


def architecture_ambiguity_gate(request: str) -> dict[str, Any]:
    text = _text_lower(request)
    score = 0.35
    if any(m in text for m in ("maybe", "either", "or", "unclear", "ambiguous", "아마", "불명확")):
        score += 0.25
    if any(m in text for m in ("across", "multiple modules", "whole project", "전체", "여러 모듈")):
        score += 0.2
    if any(m in text for m in ("ownership", "lifetime", "authority", "소유", "수명")):
        score += 0.1
    score = min(score, 1.0)
    if score > 0.7:
        action = "ask_user_once"
    elif score > 0.5:
        action = "plan_only"
    else:
        action = "bounded_assumption"
    return {"ambiguityScore": round(score, 2), "recommendedAction": action}


def _first_symbol(text: str) -> str:
    match = re.search(r"\bU[A-Z][A-Za-z0-9_]+\b", text or "")
    return match.group(0) if match else ""
