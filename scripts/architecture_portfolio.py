#!/usr/bin/env python
"""Adaptive, source-evidence-gated Unreal architecture portfolio search."""

from __future__ import annotations

import re
from typing import Any

PORTFOLIO_VERSION = 2
LEGACY_SCORE_FIELDS = ("complexity", "maintainability", "performance", "risk")
ADAPTIVE_SCORE_FIELDS = (
    "fit",
    "testability",
    "migration",
    "complexity",
    "risk",
    "performance",
)
SCORE_WEIGHTS = {
    "fit": 0.30,
    "testability": 0.15,
    "migration": 0.10,
    "complexity": 0.15,
    "risk": 0.20,
    "performance": 0.10,
}
MINIMUM_SELECTION_MARGIN = 4.0
MINIMUM_RECOMMENDATION_FIT = 3.0

PATTERN_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "actor_component",
        "label": "ActorComponent",
        "ownerTokens": ("actorcomponent", "component", "actor", "pawn", "character"),
        "base": {
            "testability": 4.0,
            "migration": 4.0,
            "complexity": 2.0,
            "risk": 2.0,
            "performance": 3.0,
        },
        "lifecycle": "per_actor",
        "cardinality": "one_per_actor",
        "notes": "Actor-owned runtime behavior with actor lifecycle and replication bridge.",
    },
    {
        "id": "world_subsystem",
        "label": "WorldSubsystem",
        "ownerTokens": ("worldsubsystem", "uworldsubsystem"),
        "base": {
            "testability": 4.0,
            "migration": 3.0,
            "complexity": 3.0,
            "risk": 3.0,
            "performance": 4.0,
        },
        "lifecycle": "per_world",
        "cardinality": "one_per_world",
        "notes": "Map/world-scoped coordinator; destroyed with the world.",
    },
    {
        "id": "game_instance_subsystem",
        "label": "GameInstanceSubsystem",
        "ownerTokens": ("gameinstancesubsystem", "ugameinstancesubsystem", "gameinstance"),
        "base": {
            "testability": 4.0,
            "migration": 3.0,
            "complexity": 3.0,
            "risk": 3.0,
            "performance": 4.0,
        },
        "lifecycle": "session",
        "cardinality": "one_per_game_instance",
        "notes": "Game-instance/session state that survives ordinary map travel.",
    },
    {
        "id": "local_player_subsystem",
        "label": "LocalPlayerSubsystem",
        "ownerTokens": ("localplayersubsystem", "ulocalplayersubsystem", "localplayer"),
        "base": {
            "testability": 4.0,
            "migration": 3.0,
            "complexity": 3.0,
            "risk": 3.0,
            "performance": 4.0,
        },
        "lifecycle": "local_player",
        "cardinality": "one_per_local_player",
        "notes": "Client-local player state across player/world transitions.",
    },
    {
        "id": "engine_subsystem",
        "label": "EngineSubsystem",
        "ownerTokens": ("enginesubsystem", "uenginesubsystem"),
        "base": {
            "testability": 3.0,
            "migration": 2.0,
            "complexity": 3.0,
            "risk": 4.0,
            "performance": 4.0,
        },
        "lifecycle": "engine",
        "cardinality": "one_per_engine",
        "notes": "Engine-process singleton for genuinely engine-global facilities.",
    },
    {
        "id": "owned_uobject_service",
        "label": "Owned UObject service",
        "ownerTokens": ("uobject", "service", "manager"),
        "base": {
            "testability": 5.0,
            "migration": 4.0,
            "complexity": 2.0,
            "risk": 3.0,
            "performance": 3.0,
        },
        "lifecycle": "follows_outer",
        "cardinality": "owner_defined",
        "notes": "Composition-friendly service whose lifetime must be proved through its Outer.",
    },
    {
        "id": "gas",
        "label": "Gameplay Ability System",
        "ownerTokens": (
            "abilitysystem",
            "gameplayability",
            "attributeset",
            "gameplayeffect",
            "abilitysystemcomponent",
        ),
        "base": {
            "testability": 4.0,
            "migration": 2.0,
            "complexity": 5.0,
            "risk": 3.0,
            "performance": 3.0,
        },
        "lifecycle": "per_actor",
        "cardinality": "one_asc_per_avatar_or_owner",
        "notes": "Replicated and predicted abilities, attributes, effects, and tags.",
    },
    {
        "id": "mass",
        "label": "Mass Entity",
        "ownerTokens": (
            "massentity",
            "massprocessor",
            "massfragment",
            "masssubsystem",
            "umass",
            "/mass",
            "\\mass",
        ),
        "base": {
            "testability": 3.0,
            "migration": 1.0,
            "complexity": 5.0,
            "risk": 4.0,
            "performance": 5.0,
        },
        "lifecycle": "per_world",
        "cardinality": "many_entities",
        "notes": "Data-oriented fragments/processors for very high entity counts.",
    },
    {
        "id": "data_asset_config",
        "label": "DataAsset/config",
        "ownerTokens": ("dataasset", "primarydataasset", "developer settings", "config"),
        "base": {
            "testability": 5.0,
            "migration": 4.0,
            "complexity": 2.0,
            "risk": 2.0,
            "performance": 5.0,
        },
        "lifecycle": "asset",
        "cardinality": "shared_immutable_data",
        "notes": "Designer-authored static tuning, not mutable runtime authority.",
    },
    {
        "id": "module_boundary_service",
        "label": "Module-boundary service",
        "ownerTokens": ("module:", "plugin_module:", "imoduleinterface", "modularfeature"),
        "base": {
            "testability": 5.0,
            "migration": 3.0,
            "complexity": 4.0,
            "risk": 3.0,
            "performance": 4.0,
        },
        "lifecycle": "module",
        "cardinality": "one_per_module_or_provider",
        "notes": "Interface/provider boundary for dependency inversion across modules.",
    },
)

_CATALOG_BY_ID = {item["id"]: item for item in PATTERN_CATALOG}


def _clean_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in value
            if isinstance(item, str) and str(item).strip()
        )
    )


def _owner_candidates(analysis: dict[str, Any]) -> list[str]:
    owners: list[str] = []
    for row in (analysis.get("stateTransitions") or {}).get(
        "stateOwnershipCandidates", []
    ):
        if isinstance(row, dict):
            owners.append(str(row.get("ownerCandidate") or "").strip())
    for row in (analysis.get("lifecycle") or {}).get("callbacks", []):
        if isinstance(row, dict):
            owners.append(str(row.get("ownerCandidate") or "").strip())
    for row in (analysis.get("topology") or {}).get("owners", []):
        if isinstance(row, dict):
            owners.append(str(row.get("id") or "").strip())
    return list(dict.fromkeys(owner for owner in owners if owner))


def _source_location(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    path = str(
        value.get("filePath")
        or value.get("file")
        or value.get("path")
        or value.get("location")
        or ""
    ).strip()
    line = value.get("lineStart") or value.get("line") or value.get("lineNumber")
    if path and isinstance(line, int) and not isinstance(line, bool) and line > 0:
        return f"{path}:{line}"
    return path


def _source_records(analysis: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in (analysis.get("stateTransitions") or {}).get(
        "stateOwnershipCandidates", []
    ):
        if not isinstance(row, dict):
            continue
        owner = str(row.get("ownerCandidate") or "").strip()
        evidence_rows = row.get("evidence") or row.get("mutationLocations") or []
        locations = [
            location
            for location in (_source_location(item) for item in evidence_rows)
            if location
        ]
        locations.extend(_clean_strings(row.get("writerFiles")))
        if owner:
            for location in locations[:12] or [""]:
                records.append(
                    {"kind": "state_owner", "label": owner, "location": location}
                )
    for row in (analysis.get("lifecycle") or {}).get("callbacks", []):
        if not isinstance(row, dict):
            continue
        owner = str(row.get("ownerCandidate") or "").strip()
        if owner:
            location = _source_location(row.get("evidence"))
            records.append(
                {
                    "kind": "lifecycle_owner",
                    "label": owner,
                    "location": location or str(row.get("location") or ""),
                }
            )
    for row in (analysis.get("topology") or {}).get("owners", []):
        if not isinstance(row, dict):
            continue
        owner = str(row.get("id") or "").strip()
        files = _clean_strings(row.get("files"))
        if owner:
            records.append(
                {
                    "kind": "source_owner",
                    "label": owner,
                    "location": files[0] if files else "",
                }
            )
        for path in files:
            records.append(
                {"kind": "source_file", "label": f"{owner} {path}", "location": path}
            )
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        identity = (record["kind"], record["label"], record["location"])
        if identity not in seen:
            seen.add(identity)
            unique.append(record)
    return unique


def _risk_signals(analysis: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    topology = analysis.get("topology") or {}
    state = analysis.get("stateTransitions") or {}
    lifecycle = analysis.get("lifecycle") or {}
    graph = analysis.get("graphEvidence") or {}
    if topology.get("sourceDependencyCycles"):
        signals.append("source_dependency_cycle")
    if int(state.get("multipleWriterCandidateCount") or 0) > 0:
        signals.append("multiple_state_writers")
    if lifecycle.get("pairingGaps"):
        signals.append("lifecycle_pairing_gap")
    if graph.get("complete") is False:
        signals.append("incomplete_source_graph")
    if (analysis.get("focus") or {}).get("unmatchedSymbols"):
        signals.append("unmatched_focus_symbol")
    return signals


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _dimension(value: Any, confidence: float, evidence: list[str]) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": round(confidence, 2),
        "evidence": list(dict.fromkeys(evidence)),
    }


def infer_architecture_requirements(
    analysis: dict[str, Any],
    *,
    objective: str = "",
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    objective_clean = str(objective or "").strip()
    constraints_clean = _clean_strings(constraints or [])
    text = " ".join([objective_clean, *constraints_clean]).casefold()
    evidence_prefix = [
        f"objective: {objective_clean}" if objective_clean else "",
        *(f"constraint: {item}" for item in constraints_clean),
    ]
    declared_evidence = [item for item in evidence_prefix if item]

    if _has(text, r"\bper[- ]actor\b", r"\beach actor\b", r"actor[- ]owned", r"액터"):
        lifecycle = _dimension("per_actor", 0.95, declared_evidence)
        cardinality = _dimension("one_per_actor", 0.95, declared_evidence)
    elif _has(text, r"\blocal player\b", r"\bper[- ]player\b", r"로컬 플레이어"):
        lifecycle = _dimension("local_player", 0.95, declared_evidence)
        cardinality = _dimension("one_per_local_player", 0.95, declared_evidence)
    elif _has(text, r"cross[- ]map", r"across maps?", r"map travel", r"session", r"맵 전환"):
        lifecycle = _dimension("session", 0.93, declared_evidence)
        cardinality = _dimension("one_per_session", 0.82, declared_evidence)
    elif _has(text, r"\bper[- ]world\b", r"world[- ]scoped", r"\bmap[- ]scoped\b"):
        lifecycle = _dimension("per_world", 0.92, declared_evidence)
        cardinality = _dimension("one_per_world", 0.85, declared_evidence)
    elif _has(text, r"engine[- ]wide", r"engine[- ]global", r"process[- ]global"):
        lifecycle = _dimension("engine", 0.92, declared_evidence)
        cardinality = _dimension("one_per_engine", 0.90, declared_evidence)
    elif _has(text, r"static tuning", r"designer[- ]authored", r"balance config", r"정적 튜닝"):
        lifecycle = _dimension("asset", 0.90, declared_evidence)
        cardinality = _dimension("shared_immutable_data", 0.88, declared_evidence)
    else:
        lifecycle = _dimension("unknown", 0.20, [])
        cardinality = _dimension("unknown", 0.20, [])

    prediction_required = _has(
        text,
        r"\bpredicted\b",
        r"\bprediction\b",
        r"client prediction",
        r"예측",
    )
    replication_required = prediction_required or _has(
        text,
        r"\breplicat(?:e|ed|ion)\b",
        r"networked",
        r"server authoritative",
        r"복제",
    )
    if prediction_required:
        authority = _dimension("server_authoritative_with_client_prediction", 0.97, declared_evidence)
    elif _has(text, r"server authoritative", r"authority", r"서버 권한"):
        authority = _dimension("server_authoritative", 0.92, declared_evidence)
    elif _has(text, r"local[- ]only", r"client[- ]only", r"로컬 전용"):
        authority = _dimension("local_only", 0.90, declared_evidence)
    else:
        authority = _dimension("unknown", 0.20, [])

    persistence_required = _has(
        text,
        r"cross[- ]map",
        r"across maps?",
        r"survive.*map",
        r"persist.*travel",
        r"맵 전환",
    )
    persistence = _dimension(
        True if persistence_required else "not_declared",
        0.94 if persistence_required else 0.20,
        declared_evidence if persistence_required else [],
    )

    entity_counts = [
        int(value.replace(",", ""))
        for value in re.findall(r"\b(\d[\d,]*)\s*(?:entities|actors|units|agents)\b", text)
    ]
    entity_counts.extend(
        int(value) * 1000
        for value in re.findall(r"\b(\d+)\s*k\s*(?:entities|actors|units|agents)?\b", text)
    )
    high_scale = bool(entity_counts and max(entity_counts) >= 10_000) or _has(
        text,
        r"\b100k\b",
        r"\bmassive entity",
        r"very high entity",
        r"10만",
    )
    ordinary_scale = bool(entity_counts) and not high_scale
    scale = _dimension(
        "very_high"
        if high_scale
        else ("ordinary" if ordinary_scale else "not_declared"),
        0.98 if high_scale else (0.85 if ordinary_scale else 0.20),
        declared_evidence if high_scale or ordinary_scale else [],
    )

    static_tuning = _has(
        text,
        r"static tuning",
        r"designer[- ]authored",
        r"balance (?:data|config)",
        r"data asset",
        r"정적 튜닝",
        r"디자이너 데이터",
    )
    mutable_runtime = _has(
        text,
        r"runtime state",
        r"mutable",
        r"attributes?",
        r"health",
        r"cooldown",
        r"state",
        r"런타임 상태",
    ) and not static_tuning
    designer_data = _dimension(
        "static_tuning" if static_tuning else "not_declared",
        0.94 if static_tuning else 0.30,
        declared_evidence if static_tuning else [],
    )
    blueprint_required = _has(
        text,
        r"blueprint",
        r"designer[- ]facing",
        r"블루프린트",
    )
    blueprint = _dimension(
        "required" if blueprint_required else "not_declared",
        0.92 if blueprint_required else 0.30,
        declared_evidence if blueprint_required else [],
    )
    if _has(text, r"editor[- /]runtime", r"editor module", r"runtime module"):
        boundary_value = "editor_runtime"
        boundary_confidence = 0.93
    elif _has(text, r"module boundary", r"plugin boundary", r"dependency inversion"):
        boundary_value = "module"
        boundary_confidence = 0.90
    elif _has(text, r"worker thread", r"async thread", r"thread[- ]safe", r"game thread"):
        boundary_value = "thread_runtime"
        boundary_confidence = 0.88
    else:
        boundary_value = "runtime"
        boundary_confidence = 0.35

    return {
        "version": PORTFOLIO_VERSION,
        "lifecycleScope": lifecycle,
        "ownershipCardinality": cardinality,
        "authority": authority,
        "replication": _dimension(
            "required" if replication_required else "not_declared",
            0.96 if replication_required else 0.30,
            declared_evidence if replication_required else [],
        ),
        "prediction": _dimension(
            "required" if prediction_required else "not_declared",
            0.97 if prediction_required else 0.30,
            declared_evidence if prediction_required else [],
        ),
        "persistenceAcrossMaps": persistence,
        "scalePerformance": scale,
        "designerData": designer_data,
        "blueprintExposure": blueprint,
        "threadRuntimeEditorBoundary": _dimension(
            boundary_value,
            boundary_confidence,
            declared_evidence if boundary_confidence >= 0.8 else [],
        ),
        "mutableRuntimeState": _dimension(
            True if mutable_runtime else "not_declared",
            0.86 if mutable_runtime else 0.20,
            declared_evidence if mutable_runtime else [],
        ),
        "proofBoundary": (
            "Requirement dimensions are deterministic interpretations of objective, "
            "constraints, and source inventory. Unknown or weakly supported dimensions "
            "remain explicit and lower candidate confidence."
        ),
    }


def _pattern_source_evidence(
    pattern: dict[str, Any],
    records: list[dict[str, str]],
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for record in records:
        haystack = f"{record['label']} {record['location']}".casefold()
        if any(token in haystack for token in pattern["ownerTokens"]):
            evidence.append(record)
    return evidence[:12]


def _hard_contradictions(
    pattern_id: str,
    requirements: dict[str, Any],
) -> list[str]:
    lifecycle = requirements["lifecycleScope"]["value"]
    authority = requirements["authority"]["value"]
    replication = requirements["replication"]["value"] == "required"
    prediction = requirements["prediction"]["value"] == "required"
    persists = requirements["persistenceAcrossMaps"]["value"] is True
    high_scale = requirements["scalePerformance"]["value"] == "very_high"
    mutable = requirements["mutableRuntimeState"]["value"] is True
    static_tuning = requirements["designerData"]["value"] == "static_tuning"
    contradictions: list[str] = []
    if pattern_id == "engine_subsystem" and (
        lifecycle in {"per_actor", "per_world", "local_player"}
        or replication
        or prediction
    ):
        contradictions.append(
            "EngineSubsystem is too broad and has no actor/world replication authority"
        )
    if pattern_id == "world_subsystem" and persists:
        contradictions.append("WorldSubsystem is destroyed during map travel")
    if pattern_id == "world_subsystem" and (replication or prediction):
        contradictions.append("WorldSubsystem does not directly replicate or predict actor state")
    if pattern_id == "game_instance_subsystem" and lifecycle == "per_actor":
        contradictions.append("GameInstanceSubsystem cannot preserve per-actor ownership/cardinality")
    if pattern_id == "local_player_subsystem" and (
        authority.startswith("server_") or lifecycle in {"per_actor", "per_world", "engine"}
    ):
        contradictions.append("LocalPlayerSubsystem is client-local, not server/world authority")
    if pattern_id == "actor_component" and persists:
        contradictions.append("ActorComponent lifetime does not inherently survive map travel")
    if pattern_id == "mass" and persists:
        contradictions.append("Mass entity ownership is world-scoped and does not survive map travel")
    if pattern_id == "owned_uobject_service" and prediction:
        contradictions.append("Plain owned UObject service has no built-in prediction contract")
    if pattern_id == "gas" and (high_scale or lifecycle in {"session", "engine", "asset"}):
        contradictions.append("GAS actor/ASC ownership conflicts with the declared scope or entity scale")
    if pattern_id == "mass" and prediction and _has(
        str(requirements["authority"]["evidence"]),
        r"attribute",
    ):
        contradictions.append("Mass is not the primary owner for GAS-style predicted attributes")
    if pattern_id == "data_asset_config" and (mutable or replication or prediction):
        contradictions.append("DataAsset/config cannot own mutable replicated runtime state")
    if pattern_id == "data_asset_config" and high_scale and not static_tuning:
        contradictions.append("DataAsset/config cannot own high-scale runtime entity processing")
    if pattern_id == "module_boundary_service" and (
        lifecycle == "per_actor" and (mutable or replication or prediction)
    ):
        contradictions.append("A module service cannot be the direct per-actor replicated state owner")
    if static_tuning and pattern_id in {"gas", "mass"} and not mutable:
        contradictions.append("Runtime framework ownership is unnecessary for static tuning alone")
    return contradictions


def _clamp_score(value: float) -> float:
    return round(max(1.0, min(5.0, value)), 2)


def _score_pattern(
    pattern_ids: tuple[str, ...],
    requirements: dict[str, Any],
    owner_evidence: list[dict[str, str]],
    risk_signals: list[str],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    primary = _CATALOG_BY_ID[pattern_ids[0]]
    base = dict(primary["base"])
    scores = {
        "fit": 3.0,
        "testability": float(base["testability"]),
        "migration": float(base["migration"]),
        "complexity": float(base["complexity"]),
        "risk": float(base["risk"]),
        "performance": float(base["performance"]),
    }
    evidence: dict[str, list[str]] = {
        "fit": ["neutral catalog prior before requirement matching"],
        "testability": [f"{primary['label']} catalog testability baseline"],
        "migration": [f"{primary['label']} catalog migration baseline"],
        "complexity": [f"{primary['label']} catalog complexity cost"],
        "risk": [f"{primary['label']} catalog adoption risk"],
        "performance": [f"{primary['label']} catalog performance profile"],
    }
    lifecycle = requirements["lifecycleScope"]["value"]
    lifecycle_matches = {
        "per_actor": {"actor_component", "gas"},
        "per_world": {"world_subsystem", "mass"},
        "local_player": {"local_player_subsystem"},
        "session": {"game_instance_subsystem", "local_player_subsystem"},
        "engine": {"engine_subsystem"},
        "asset": {"data_asset_config"},
    }
    if lifecycle != "unknown":
        if primary["id"] in lifecycle_matches.get(str(lifecycle), set()):
            scores["fit"] += 1.5
            evidence["fit"].append(f"lifecycleScope={lifecycle} matches {primary['label']}")
        else:
            scores["fit"] -= 0.75
            scores["risk"] += 0.5
            evidence["risk"].append(f"lifecycleScope={lifecycle} needs an ownership bridge")

    replication = requirements["replication"]["value"] == "required"
    prediction = requirements["prediction"]["value"] == "required"
    if replication:
        if "gas" in pattern_ids:
            scores["fit"] += 1.2
            evidence["fit"].append("GAS supplies replicated actor-state conventions")
        elif "actor_component" in pattern_ids:
            scores["fit"] += 0.5
            scores["risk"] += 0.5
            evidence["risk"].append("custom component replication still needs authority tests")
        elif "mass" in pattern_ids:
            scores["fit"] += 0.2
            scores["risk"] += 0.8
            evidence["risk"].append("Mass replication requires a separate validated replication path")
        else:
            scores["fit"] -= 1.5
    if prediction:
        if "gas" in pattern_ids:
            scores["fit"] += 1.5
            scores["testability"] += 0.3
            evidence["fit"].append("prediction requirement directly favors GAS")
        else:
            scores["fit"] -= 1.5
            scores["risk"] += 1.0

    if requirements["persistenceAcrossMaps"]["value"] is True:
        if primary["id"] in {
            "game_instance_subsystem",
            "local_player_subsystem",
            "engine_subsystem",
        }:
            scores["fit"] += 1.5
            evidence["fit"].append("owner survives ordinary map travel")
        else:
            scores["fit"] -= 1.25
            evidence["risk"].append("map travel requires explicit state transfer")

    if requirements["scalePerformance"]["value"] == "very_high":
        if "mass" in pattern_ids:
            scores["fit"] += 2.0
            scores["performance"] = 5.0
            scores["testability"] += 0.5
            scores["migration"] += 2.0
            scores["risk"] -= 1.5
            evidence["fit"].append("very-high entity count favors Mass data-oriented processing")
            evidence["performance"].append("Mass avoids per-Actor UObject overhead at scale")
            evidence["testability"].append(
                "fragment/processor boundaries permit focused data-oriented tests"
            )
            evidence["migration"].append(
                "declared scale justifies the Mass migration cost"
            )
            evidence["risk"].append(
                "scale alignment reduces the risk of choosing the wrong runtime model"
            )
        elif primary["id"] in {"actor_component", "gas"}:
            scores["fit"] -= 1.5
            scores["performance"] -= 1.5
            scores["risk"] += 1.0
            evidence["risk"].append("per-Actor/UObject cost conflicts with declared entity scale")

    static_tuning = requirements["designerData"]["value"] == "static_tuning"
    if static_tuning:
        if "data_asset_config" in pattern_ids:
            scores["fit"] += 1.5
            scores["testability"] += 0.3
            evidence["fit"].append("static designer tuning belongs in DataAsset/config")
        else:
            scores["fit"] -= 0.6
            evidence["migration"].append("designer data would remain embedded in runtime code")
    if requirements["blueprintExposure"]["value"] == "required":
        if any(
            pattern_id in {"actor_component", "gas", "data_asset_config"}
            for pattern_id in pattern_ids
        ):
            scores["fit"] += 0.5
            evidence["fit"].append("pattern has a natural Blueprint/designer surface")
        else:
            scores["risk"] += 0.5

    boundary = requirements["threadRuntimeEditorBoundary"]["value"]
    if boundary in {"module", "editor_runtime"}:
        if "module_boundary_service" in pattern_ids:
            scores["fit"] += 1.0
            evidence["fit"].append(f"{boundary} boundary favors an interface/provider seam")
        elif primary["id"] == "engine_subsystem" and boundary == "editor_runtime":
            scores["risk"] += 1.0
            evidence["risk"].append("engine singleton can blur editor/runtime module ownership")

    if len(pattern_ids) > 1:
        scores["complexity"] += 0.8
        scores["migration"] -= 0.5
        evidence["complexity"].append(
            "composition adds an explicit ownership/data or module seam"
        )
    if owner_evidence:
        scores["migration"] += 0.7
        scores["risk"] -= 0.4
        evidence["migration"].append("matching source owner evidence reduces migration distance")
    else:
        scores["migration"] -= 0.7
        scores["risk"] += 0.8
        evidence["risk"].append("owner choice lacks matching project source evidence")
    if "source_dependency_cycle" in risk_signals:
        if "module_boundary_service" in pattern_ids:
            scores["fit"] += 0.5
        else:
            scores["risk"] += 0.5
    if "incomplete_source_graph" in risk_signals or "unmatched_focus_symbol" in risk_signals:
        scores["risk"] += 0.8
    return (
        {field: _clamp_score(value) for field, value in scores.items()},
        evidence,
    )


def _adaptive_utility(scores: dict[str, float]) -> float:
    normalized = {
        "fit": scores["fit"] / 5.0,
        "testability": scores["testability"] / 5.0,
        "migration": scores["migration"] / 5.0,
        "complexity": (6.0 - scores["complexity"]) / 5.0,
        "risk": (6.0 - scores["risk"]) / 5.0,
        "performance": scores["performance"] / 5.0,
    }
    return round(
        sum(normalized[field] * SCORE_WEIGHTS[field] for field in ADAPTIVE_SCORE_FIELDS)
        * 100,
        2,
    )


def _candidate_compositions(requirements: dict[str, Any], risks: list[str]) -> list[tuple[str, ...]]:
    compositions: list[tuple[str, ...]] = [(item["id"],) for item in PATTERN_CATALOG]
    if requirements["prediction"]["value"] == "required":
        compositions.append(("gas", "actor_component"))
    if requirements["scalePerformance"]["value"] == "very_high":
        compositions.append(("mass", "world_subsystem"))
    if requirements["designerData"]["value"] == "static_tuning":
        for runtime_pattern in (
            "actor_component",
            "world_subsystem",
            "game_instance_subsystem",
            "local_player_subsystem",
            "gas",
            "mass",
        ):
            compositions.append((runtime_pattern, "data_asset_config"))
    if (
        requirements["threadRuntimeEditorBoundary"]["value"] in {"module", "editor_runtime"}
        or "source_dependency_cycle" in risks
    ):
        for runtime_pattern in (
            "actor_component",
            "world_subsystem",
            "game_instance_subsystem",
            "owned_uobject_service",
        ):
            compositions.append((runtime_pattern, "module_boundary_service"))
    return list(dict.fromkeys(compositions))


def generate_architecture_portfolio(
    analysis: dict[str, Any],
    *,
    objective: str = "",
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    """Search a bounded Unreal pattern catalog using requirements and source evidence."""

    owners = _owner_candidates(analysis)
    risks = _risk_signals(analysis)
    constraints_clean = _clean_strings(constraints or [])
    requirements = infer_architecture_requirements(
        analysis,
        objective=objective,
        constraints=constraints_clean,
    )
    source_records = _source_records(analysis)
    evaluated: list[dict[str, Any]] = []
    eliminated: list[dict[str, Any]] = []
    for pattern_ids in _candidate_compositions(requirements, risks):
        primary = _CATALOG_BY_ID[pattern_ids[0]]
        contradictions = _hard_contradictions(primary["id"], requirements)
        if len(pattern_ids) > 1:
            if "gas" in pattern_ids and "actor_component" in pattern_ids:
                contradictions = [
                    item
                    for item in contradictions
                    if "prediction" not in item.casefold()
                ]
            if "data_asset_config" in pattern_ids:
                contradictions = [
                    item
                    for item in contradictions
                    if "static tuning" not in item.casefold()
                ]
        if contradictions:
            eliminated.append(
                {
                    "patternIds": list(pattern_ids),
                    "name": " + ".join(_CATALOG_BY_ID[item]["label"] for item in pattern_ids),
                    "hardContradictions": contradictions,
                }
            )
            continue
        owner_evidence = _pattern_source_evidence(primary, source_records)
        scores, score_evidence = _score_pattern(
            pattern_ids,
            requirements,
            owner_evidence,
            risks,
        )
        known_dimensions = sum(
            value.get("value") not in {"unknown", "not_declared"}
            for key, value in requirements.items()
            if isinstance(value, dict) and key != "proofBoundary"
        )
        confidence = min(
            0.96,
            0.35 + known_dimensions * 0.045 + (0.20 if owner_evidence else 0.0),
        )
        candidate = {
            "name": " + ".join(_CATALOG_BY_ID[item]["label"] for item in pattern_ids),
            "strategy": "compose_unreal_patterns" if len(pattern_ids) > 1 else "select_unreal_pattern",
            "patternIds": list(pattern_ids),
            "ownerCandidate": (
                owner_evidence[0]["label"]
                if owner_evidence
                else f"new {primary['label']} owner requires source proof"
            ),
            "ownerEvidence": {
                "required": True,
                "satisfied": bool(owner_evidence),
                "evidence": owner_evidence,
                "missingAction": (
                    ""
                    if owner_evidence
                    else "identify the concrete source owner, lifecycle entry/exit, and mutation API"
                ),
            },
            "rationale": " ".join(
                [
                    primary["notes"],
                    *(
                        _CATALOG_BY_ID[item]["notes"]
                        for item in pattern_ids[1:]
                    ),
                ]
            ),
            "scores": scores,
            "scoreEvidence": score_evidence,
            "utilityScore": _adaptive_utility(scores),
            "confidence": {
                "value": round(confidence, 2),
                "basis": (
                    "requirement dimensions plus matching source owner evidence"
                    if owner_evidence
                    else "requirement dimensions only; owner evidence is still missing"
                ),
            },
            "riskSignals": risks,
            "hardContradictions": [],
            "requiredEvidence": [
                "read the concrete lifecycle and state owner definitions",
                "verify cardinality and creation/destruction paths",
                "verify authority, replication, and prediction behavior",
                "verify module and editor/runtime dependency direction",
                "build and targeted runtime regression evidence",
            ],
            "migrationShape": [
                "introduce the selected owner without switching all consumers",
                "migrate bounded callsites and data ownership slices",
                "validate lifecycle, network, asset, and rollback behavior",
                "remove superseded ownership only after runtime evidence",
            ],
            "selectionEligible": bool(owner_evidence) and scores["fit"] >= MINIMUM_RECOMMENDATION_FIT,
            "implementationReady": False,
            "proofLevel": "Proposed",
        }
        evaluated.append(candidate)

    ranked_all = sorted(
        evaluated,
        key=lambda item: (
            -float(item["utilityScore"]),
            -float(item["scores"]["fit"]),
            str(item["name"]).casefold(),
        ),
    )
    selected_candidates = ranked_all[:5]
    if len(selected_candidates) < 3:
        selected_candidates = ranked_all
    provisional = selected_candidates[0] if selected_candidates else None
    margin = (
        round(
            float(selected_candidates[0]["utilityScore"])
            - float(selected_candidates[1]["utilityScore"]),
            2,
        )
        if len(selected_candidates) >= 2
        else 0.0
    )
    ambiguous = len(selected_candidates) >= 2 and margin < MINIMUM_SELECTION_MARGIN
    recommended = (
        provisional
        if provisional
        and provisional["selectionEligible"]
        and provisional["ownerEvidence"]["satisfied"]
        else None
    )
    return {
        "version": PORTFOLIO_VERSION,
        "catalogVersion": "unreal-pattern-catalog-v1",
        "objective": str(objective or "").strip(),
        "constraints": constraints_clean,
        "requirementDimensions": requirements,
        "existingOwnerCandidates": owners,
        "sourceEvidenceRecordCount": len(source_records),
        "riskSignals": risks,
        "candidates": selected_candidates,
        "candidateCount": len(selected_candidates),
        "eliminatedPatterns": eliminated,
        "ranking": [item["name"] for item in selected_candidates],
        "provisionalRecommendation": provisional["name"] if provisional else "",
        "recommendedCandidate": recommended["name"] if recommended else "",
        "selectionMargin": margin,
        "ambiguous": ambiguous,
        "portfolioStatus": (
            "ranked_candidates" if selected_candidates else "no_viable_candidate"
        ),
        "requirementConflict": not selected_candidates,
        "implementationReady": False,
        "nextAction": (
            "resolve_or_partition_conflicting_requirements"
            if not selected_candidates
            else (
                "collect_source_evidence_for_owner_choice"
                if not recommended
                else (
                    "resolve_ambiguous_candidates_with_rationale"
                    if ambiguous
                    else "review_ranked_candidates_and_select"
                )
            )
        ),
        "proofBoundary": (
            "Schema v2 performs bounded catalog/composition search and eliminates declared "
            "hard contradictions. Scores remain Proposed. A recommendation requires matching "
            "project source evidence for the concrete owner, and implementation still requires "
            "direct reads, build/test/runtime proof, migration checks, and explicit selection. "
            "If no viable candidate remains, requirements must be corrected or partitioned; "
            "the search does not reintroduce a contradictory candidate."
        ),
    }


def _normalized_scores(value: Any) -> tuple[dict[str, float], list[str], str]:
    scores = value if isinstance(value, dict) else {}
    normalized: dict[str, float] = {}
    issues: list[str] = []
    adaptive_mode = any(
        field in scores for field in ("fit", "testability", "migration")
    )
    required = ADAPTIVE_SCORE_FIELDS if adaptive_mode else LEGACY_SCORE_FIELDS
    for field in required:
        score = scores.get(field)
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 1 <= float(score) <= 5
        ):
            issues.append(f"scores.{field} must be a number from 1 to 5")
            continue
        normalized[field] = float(score)
    if not issues and not adaptive_mode:
        normalized = {
            "fit": normalized["maintainability"],
            "testability": normalized["maintainability"],
            "migration": 6.0 - normalized["complexity"],
            "complexity": normalized["complexity"],
            "risk": normalized["risk"],
            "performance": normalized["performance"],
            "maintainability": normalized["maintainability"],
        }
    return normalized, issues, "adaptive" if adaptive_mode else "legacy"


def _comparison_owner_evidence(item: dict[str, Any]) -> list[Any]:
    evidence = item.get("ownerEvidence")
    if isinstance(evidence, dict):
        rows = evidence.get("evidence")
        return rows if isinstance(rows, list) else []
    return evidence if isinstance(evidence, list) else []


def compare_architecture_alternatives(
    alternatives: Any,
    *,
    selected_alternative: str = "",
    selection_rationale: str = "",
) -> dict[str, Any]:
    """Compare alternatives while preserving legacy four-score proposals."""

    rows: list[dict[str, Any]] = []
    raw = alternatives if isinstance(alternatives, list) else []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            rows.append(
                {
                    "index": index,
                    "name": item.strip(),
                    "eligible": False,
                    "issues": ["structured scores are required for comparison"],
                    "utilityScore": None,
                }
            )
            continue
        if not isinstance(item, dict):
            rows.append(
                {
                    "index": index,
                    "name": "",
                    "eligible": False,
                    "issues": ["alternative must be a string or object"],
                    "utilityScore": None,
                }
            )
            continue
        name = str(item.get("name") or "").strip()
        scores, issues, score_mode = _normalized_scores(item.get("scores"))
        owner_evidence = _comparison_owner_evidence(item)
        if not name:
            issues.append("name is required")
        if score_mode == "adaptive" and not owner_evidence:
            issues.append(
                "ownerEvidence is required for adaptive architecture alternatives"
            )
        rows.append(
            {
                "index": index,
                "name": name,
                "eligible": not issues,
                "issues": issues,
                "scores": scores,
                "scoreMode": score_mode,
                "ownerEvidence": owner_evidence,
                "utilityScore": _adaptive_utility(scores) if not issues else None,
                "rationale": str(item.get("rationale") or "").strip(),
            }
        )

    ranked = sorted(
        (row for row in rows if row["eligible"]),
        key=lambda row: (-float(row["utilityScore"]), str(row["name"]).lower()),
    )
    recommended = str(ranked[0]["name"]) if ranked else ""
    margin = (
        round(float(ranked[0]["utilityScore"]) - float(ranked[1]["utilityScore"]), 2)
        if len(ranked) >= 2
        else 0.0
    )
    ambiguous = len(ranked) >= 2 and margin < MINIMUM_SELECTION_MARGIN
    selected = str(selected_alternative or "").strip()
    selected_row = next((row for row in ranked if row["name"] == selected), None)
    selection_issues: list[str] = []
    if len(ranked) < 2:
        selection_issues.append("at least two fully scored alternatives are required")
    if not selected:
        selection_issues.append("selectedAlternative is required")
    elif selected_row is None:
        selection_issues.append("selectedAlternative must name an eligible alternative")
    if selected and recommended and selected != recommended and not str(
        selection_rationale or ""
    ).strip():
        selection_issues.append(
            "selectionRationale is required when overriding the recommended alternative"
        )
    if ambiguous and not str(selection_rationale or "").strip():
        selection_issues.append(
            "selectionRationale is required when candidate scores are ambiguous"
        )
    return {
        "version": PORTFOLIO_VERSION,
        "alternatives": rows,
        "eligibleCount": len(ranked),
        "ranking": [row["name"] for row in ranked],
        "recommendedAlternative": recommended,
        "selectedAlternative": selected,
        "selectionMargin": margin,
        "minimumSelectionMargin": MINIMUM_SELECTION_MARGIN,
        "ambiguous": ambiguous,
        "selectionValid": not selection_issues,
        "selectionIssues": selection_issues,
        "proofBoundary": (
            "Adaptive utility compares declared fit/testability/migration/complexity/"
            "risk/performance evidence. Legacy four-score proposals remain accepted, but "
            "new adaptive alternatives require source owner evidence. Scores never replace "
            "build, test, runtime, asset, or migration proof."
        ),
    }
