#!/usr/bin/env python
"""Architecture decision schema, risk score, and question fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArchitectureDecision:
    decision_id: str
    question_fingerprint: str
    risk_score: float
    ownership: str = ""
    lifetime: str = ""
    authority: str = ""
    project_path: str = ""
    scope_hash: str = ""
    affected_file_hash: str = ""
    plan_revision: str = ""
    approved: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def question_fingerprint(questions: list[str]) -> str:
    normalized = [str(q).strip().lower() for q in questions if str(q).strip()]
    raw = json.dumps(sorted(normalized), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compute_architecture_risk(ambiguity_gate: dict[str, Any]) -> float:
    score = float(ambiguity_gate.get("ambiguityScore") or 0)
    action = str(ambiguity_gate.get("recommendedAction") or "")
    if action == "human_approval":
        score = max(score, 0.85)
    elif action == "ask_user_once":
        score = max(score, 0.7)
    return round(min(score, 1.0), 2)


def build_architecture_decision(
    *,
    ambiguity_gate: dict[str, Any],
    ownership: str = "",
    lifetime: str = "",
    authority: str = "",
    project_path: str = "",
    scope_hash: str = "",
    affected_file_hash: str = "",
    plan_revision: str = "",
) -> ArchitectureDecision:
    questions = list(ambiguity_gate.get("clarificationQuestions") or [])
    fp = question_fingerprint(questions)
    risk = compute_architecture_risk(ambiguity_gate)
    decision_material = ":".join(
        (fp, str(risk), ownership, lifetime, authority, project_path, scope_hash, affected_file_hash, plan_revision)
    )
    decision_id = hashlib.sha256(decision_material.encode()).hexdigest()[:20]
    return ArchitectureDecision(
        decision_id=decision_id,
        question_fingerprint=fp,
        risk_score=risk,
        ownership=ownership,
        lifetime=lifetime,
        authority=authority,
        project_path=project_path,
        scope_hash=scope_hash,
        affected_file_hash=affected_file_hash,
        plan_revision=plan_revision,
        approved=False,
    )


def load_approval_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"decisions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"decisions": {}}
    return data if isinstance(data, dict) else {"decisions": {}}


def persist_approval(path: Path, decision: ArchitectureDecision) -> None:
    store = load_approval_store(path)
    decisions = dict(store.get("decisions") or {})
    payload = decision.to_dict()
    payload["approved"] = True
    decisions[decision.decision_id] = payload
    store["decisions"] = decisions
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def approval_is_valid(path: Path, decision: ArchitectureDecision) -> bool:
    stored = (load_approval_store(path).get("decisions") or {}).get(decision.decision_id)
    if not isinstance(stored, dict) or stored.get("approved") is not True:
        return False
    expected = decision.to_dict()
    for key in ("project_path", "scope_hash", "affected_file_hash", "plan_revision", "authority"):
        if str(stored.get(key) or "") != str(expected.get(key) or ""):
            return False
    return True
