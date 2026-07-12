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
    depends_on: list[str] = field(default_factory=list)
    domain: str = "generic"
    slice_kind: str = "compile"
    required_validators: list[str] = field(default_factory=list)

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


DOMAIN_SIGNALS: dict[DomainKind, tuple[str, ...]] = {
    "component": ("component", "createdefaultsubobject", "uactorcomponent", "컴포넌트"),
    "subsystem": ("subsystem", "ugameinstancesubsystem", "uworldsubsystem", "서브시스템"),
    "replication": ("replication", "replicate", "replicated", "networked", "onrep", "rpc", "doreplicated", "복제"),
    "gas": ("gameplay ability", "gas", "abilitysystem", "gameplayeffect", "attribute set"),
    "animation": ("animinstance", "animnotify", "notifystate", "animation blueprint", "애니"),
    "architecture": ("architecture", "ownership", "lifetime", "authority", "아키텍처"),
}


@dataclass
class DomainProfile:
    primary: DomainKind
    scores: dict[str, float]
    secondary_domains: list[DomainKind] = field(default_factory=list)
    confidence: float = 0.0
    mixed: bool = False
    architecture_required: bool = False
    network_sensitive: bool = False
    asset_metadata_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "primaryDomain": self.primary,
            "secondaryDomains": list(self.secondary_domains),
            "scores": self.scores,
            "confidence": self.confidence,
            "mixed": self.mixed,
            "architectureRequired": self.architecture_required,
            "networkSensitive": self.network_sensitive,
            "assetMetadataRequired": self.asset_metadata_required,
        }


def _marker_present(text: str, marker: str) -> bool:
    if marker.isascii() and re.fullmatch(r"[a-z0-9_ ]+", marker):
        return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", text))
    return marker in text


def build_domain_profile(request: str, mode: str = "auto") -> DomainProfile:
    text = _text_lower(f"{mode} {request}")
    scores: dict[str, float] = {"generic": 0.1}
    for kind, markers in DOMAIN_SIGNALS.items():
        hits = sum(1 for marker in markers if _marker_present(text, marker))
        if hits:
            scores[kind] = min(0.35 + hits * 0.2, 1.0)
    if mode == "prototype_component":
        scores["component"] = max(scores.get("component", 0), 0.9)
    if mode == "prototype_subsystem":
        scores["subsystem"] = max(scores.get("subsystem", 0), 0.9)

    ranked = [(kind, score) for kind, score in sorted(scores.items(), key=lambda item: item[1], reverse=True) if kind != "generic"]
    primary: DomainKind = ranked[0][0] if ranked else "generic"  # type: ignore[assignment]
    secondary = [kind for kind, score in ranked[1:] if score >= 0.45]
    top_score = ranked[0][1] if ranked else scores["generic"]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    mixed = bool(secondary) and top_score >= 0.45 and second_score >= 0.45
    subsystem_ambiguous = primary == "subsystem" and select_subsystem_lifetime(request)["requestedLifetime"] == "unknown"
    architecture_required = (
        mixed
        or subsystem_ambiguous
        or scores.get("architecture", 0) >= 0.45
        or primary == "architecture"
    )
    network_sensitive = scores.get("replication", 0) >= 0.45 or (
        scores.get("gas", 0) >= 0.45 and any(token in text for token in ("server", "client", "multiplayer", "network"))
    )
    asset_metadata_required = scores.get("animation", 0) >= 0.45 and any(
        token in text for token in ("blueprint", "asset", "montage", "\uC790\uC0B0")
    )
    return DomainProfile(
        primary=primary,
        scores=scores,
        secondary_domains=secondary,  # type: ignore[arg-type]
        confidence=round(top_score, 2),
        mixed=mixed,
        architecture_required=architecture_required,
        network_sensitive=network_sensitive,
        asset_metadata_required=asset_metadata_required,
    )


def detect_domain_kind(request: str, mode: str = "auto") -> DomainKind:
    return build_domain_profile(request, mode).primary


def select_subsystem_lifetime(request: str) -> dict[str, Any]:
    text = _text_lower(request)
    requested = "unknown"
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
    if requested == "unknown":
        return {
            "requestedLifetime": "unknown",
            "recommendedBase": None,
            "rejectedBases": [],
            "reason": "Subsystem lifetime is ambiguous; choose ownership before code generation.",
            "requiresClarification": True,
        }
    payload = dict(mapping[requested])
    payload["requestedLifetime"] = requested
    payload["requiresClarification"] = False
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
        if not base:
            return [
                PlanSlice(
                    slice_id="subsystem_lifetime_decision",
                    title="Choose subsystem lifetime and ownership",
                    files=[],
                    postconditions=["lifetime selected", "network scope selected"],
                    domain="subsystem",
                    slice_kind="architecture",
                )
            ]
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
            PlanSlice(slice_id="rep_rpc", title="Add Server/Client RPC stubs", files=["<actor>.h", "<actor>.cpp"], postconditions=["RPC declaration and _Implementation signatures align"]),
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
                slice_kind="architecture",
            ),
        ]
    return []


FIX_ONLY_MODES = frozenset({"compile_fix", "module_fix", "reflection_fix"})
INFORMATIONAL_SLICE_KINDS = frozenset({"architecture", "analysis", "investigation"})
EXECUTABLE_TASK_KINDS = frozenset({"edit", "codegen", "refactor", "compile_fix"})


def allows_executable_slices(task_kind: str, mode: str = "auto") -> bool:
    """Return True when domain scaffolding slices may execute as edits."""
    normalized_mode = (mode or "auto").strip().lower()
    if task_kind == "compile_fix" or normalized_mode in FIX_ONLY_MODES:
        return False
    if normalized_mode in {"prototype_component", "prototype_subsystem"}:
        return True
    if task_kind in {"edit", "codegen", "refactor"}:
        return True
    return False


def partition_plan_slices(
    dag_slices: list[PlanSlice],
    *,
    task_kind: str,
    mode: str = "auto",
) -> tuple[list[PlanSlice], list[PlanSlice]]:
    """Split informational guidance from explicitly authorized executable steps."""
    informational: list[PlanSlice] = []
    executable: list[PlanSlice] = []
    can_execute = allows_executable_slices(task_kind, mode)
    for item in dag_slices:
        if item.slice_kind in INFORMATIONAL_SLICE_KINDS:
            informational.append(item)
        elif not can_execute:
            informational.append(item)
        else:
            executable.append(item)
    return informational, executable


def build_domain_slice_dag(profile: DomainProfile, request: str) -> list[PlanSlice]:
    """Merge primary/secondary domain slices behind one ownership decision."""
    domains: list[DomainKind] = [profile.primary, *profile.secondary_domains]
    slices: list[PlanSlice] = []
    if profile.architecture_required and profile.primary != "architecture":
        slices.append(
            PlanSlice(
                slice_id="ownership_decision",
                title="Resolve ownership, lifetime, authority, and asset boundaries",
                files=[],
                postconditions=["ownership documented", "lifetime documented", "authority documented"],
                domain="architecture",
                slice_kind="architecture",
            )
        )
    seen: set[str] = {item.slice_id for item in slices}
    previous = "ownership_decision" if slices else ""
    for domain in domains:
        if domain in {"generic", "architecture"}:
            continue
        for item in build_domain_slices(domain, request):
            if item.slice_id in seen:
                continue
            if item.slice_id == "subsystem_lifetime_decision" and "ownership_decision" in seen:
                continue
            item.domain = domain
            if previous and not item.depends_on:
                item.depends_on = [previous]
            if not item.required_validators and item.slice_kind == "compile":
                item.required_validators = ["domain_static_validate", "ubt_build"]
            slices.append(item)
            seen.add(item.slice_id)
            previous = item.slice_id
    if profile.primary == "architecture" and not slices:
        return build_domain_slices("architecture", request)
    return slices


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
                from plugin_project_context import iter_scan_root_files, resolve_scan_roots

                scan_roots = resolve_scan_roots(project_root)
                for scan_root in scan_roots:
                    for cpp in iter_scan_root_files(
                        scan_root,
                        skip_dirs={"Intermediate", "Binaries", "Saved", "ThirdParty"},
                    ):
                        if cpp.suffix.lower() != ".cpp":
                            continue
                        text = cpp.read_text(encoding="utf-8-sig", errors="replace")
                        if f"CreateDefaultSubobject<{symbol}" in text or f"NewObject<{symbol}" in text:
                            resolution = resolve_project_symbol_include(
                                project_root, symbol, cpp, "create_default_subobject"
                            )
                            if resolution:
                                include_path = resolution.preferred_include
                                patch_target = resolution.target_file
                                evidence.reason = resolution.reason
                                break
                    if evidence.reason:
                        break
            except Exception as exc:
                evidence.reason = f"include resolver degraded: {exc}"
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
        payload["patchTemplate"] = payload.get("patchTemplate", "")[:800]
        payload["reason"] = payload.get("reason", "")[:400]
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized) > FIX_EVIDENCE_MAX_CHARS:
            payload["patchTemplate"] = payload.get("patchTemplate", "")[:400]
    return payload


def architecture_ambiguity_gate(request: str) -> dict[str, Any]:
    text = _text_lower(request)
    score = 0.35
    if any(m in text for m in ("maybe", "either", " or ", "unclear", "ambiguous", "아마", "불명확")):
        score += 0.25
    if any(m in text for m in ("across", "multiple modules", "whole project", "전체", "여러 모듈")):
        score += 0.2
    if any(m in text for m in ("ownership", "lifetime", "authority", "소유", "수명")):
        score += 0.1
    if any(m in text for m in ("subsystem", "replication", "gas", "game instance", "world subsystem")):
        score += 0.05
    if "subsystem" in text and select_subsystem_lifetime(request)["requestedLifetime"] == "unknown":
        score = max(score, 0.75)
    score = min(score, 1.0)

    if score >= 0.85:
        action = "human_approval"
    elif score > 0.7:
        action = "ask_user_once"
    elif score > 0.5:
        action = "plan_only"
    else:
        action = "bounded_assumption"

    questions: list[str] = []
    if action == "ask_user_once":
        if any(m in text for m in ("lifetime", "수명", "subsystem", "game instance", "world")):
            questions.append("Which lifetime scope should own this state (World, GameInstance, LocalPlayer, or Engine)?")
        if any(m in text for m in ("ownership", "authority", "소유", "ssot")):
            questions.append("Which module/class should be the single source of truth for this state?")
        if any(m in text for m in ("replication", "rpc", "onrep", "복제")):
            questions.append("Should this run on server authority only, or also replicate to clients?")
        if not questions:
            questions.append("Which architectural boundary (module, subsystem, or actor) should own this change?")
        questions = questions[:3]

    assumptions: list[str] = []
    if action == "bounded_assumption":
        if "world" in text or "level" in text:
            assumptions.append("Assume per-world ownership via UWorldSubsystem unless contradicted.")
        elif "game instance" in text or "session" in text:
            assumptions.append("Assume session ownership via UGameInstanceSubsystem unless contradicted.")

    return {
        "architectureRequired": True,
        "ambiguityScore": round(score, 2),
        "recommendedAction": action,
        "clarificationQuestions": questions,
        "assumptions": assumptions,
    }


def _first_symbol(text: str) -> str:
    match = re.search(r"\bU[A-Z][A-Za-z0-9_]+\b", text or "")
    return match.group(0) if match else ""
