#!/usr/bin/env python
"""Validate post-process-to-Material-Graph porting plans for common Unreal hallucinations."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any


REJECT_PATTERNS = [
    (
        "worldposition_z_camera_distance",
        re.compile(r"\bWorld\s*Position\s*\.\s*Z\b|\bWorldPosition\.Z\b", re.IGNORECASE),
        "Do not use WorldPosition.Z as camera distance. Use Distance(AbsoluteWorldPosition, CameraPositionWS) or an engine-proven depth/distance node.",
    ),
    (
        "preexposure_surface_material",
        re.compile(r"ResolvedView\.PreExposure|\bPreExposure\b.*\bMaterial Graph\b|\bPreExposure\b.*\bsurface material\b", re.IGNORECASE),
        "Do not treat ResolvedView.PreExposure as an ordinary surface Material Graph input without exact engine evidence.",
    ),
    (
        "scene_color_surface_rewrite",
        re.compile(r"SceneColor\s*(?:->|→).*StyleColor.*(?:->|→).*SceneColor|final\s+SceneColor|Standard Node.*SceneColor", re.IGNORECASE | re.DOTALL),
        "Ordinary surface materials do not rewrite final SceneColor. Use material inputs or keep full-screen work in post-process.",
    ),
    (
        "gbuffer_or_stencil_surface_material",
        re.compile(r"\bGBuffer\b|CustomStencil|CustomDepth|neighbor(?:hood)?\s+SceneDepth", re.IGNORECASE),
        "GBuffer, CustomStencil, CustomDepth, and neighborhood depth reads are post-process/global-shader territory unless exact material-domain evidence exists.",
    ),
    (
        "automatic_directional_light",
        re.compile(r"DirectionalLightDirection|GetAttribute\(\s*[\"']DirectionalLightDirection[\"']\s*\)|active directional light direction", re.IGNORECASE),
        "Do not claim ordinary surface materials can automatically read the active directional light direction. Prefer an MPC parameter such as KeyLightDirectionWS.",
    ),
    (
        "invented_getattribute",
        re.compile(r"GetAttribute\(\s*[\"'][^\"']+[\"']\s*\)", re.IGNORECASE),
        "Do not invent generic GetAttribute(...) accessors without exact project or engine evidence.",
    ),
]

WARN_SECTIONS = [
    ("portable_to_material_functions", re.compile(r"portable_to_material_functions|directly portable", re.IGNORECASE)),
    ("approximate_in_material", re.compile(r"approximate_in_material|approximation|approximate", re.IGNORECASE)),
    ("keep_in_post_process", re.compile(r"keep_in_post_process|post-process only|post process only", re.IGNORECASE)),
    ("parameter_mapping", re.compile(r"parameter_mapping|MPC|Material Parameter Collection|parameter mapping", re.IGNORECASE)),
    ("risk_checks", re.compile(r"risk_checks|risk checks|next checks|verify", re.IGNORECASE)),
]


def validate_material_porting_plan(plan_text: str) -> dict[str, Any]:
    text = str(plan_text or "")
    findings: list[dict[str, Any]] = []
    for code, pattern, message in REJECT_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                {
                    "severity": "reject",
                    "code": code,
                    "message": message,
                    "matchedText": match.group(0)[:160],
                }
            )

    for section, pattern in WARN_SECTIONS:
        if not pattern.search(text):
            findings.append(
                {
                    "severity": "warn",
                    "code": f"missing_{section}",
                    "message": f"Porting answer should include or clearly cover `{section}`.",
                }
            )

    reject_count = sum(1 for f in findings if f["severity"] == "reject")
    warn_count = sum(1 for f in findings if f["severity"] == "warn")
    verdict = "reject" if reject_count else "warn" if warn_count else "pass"
    return {
        "ok": reject_count == 0,
        "verdict": verdict,
        "rejectCount": reject_count,
        "warnCount": warn_count,
        "findings": findings,
        "requiredShape": [section for section, _ in WARN_SECTIONS],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Material Graph porting plan.")
    parser.add_argument("--plan", default="", help="Plan text to validate")
    parser.add_argument("--plan-file", default="", help="UTF-8 text file containing the plan")
    args = parser.parse_args()
    text = args.plan
    if args.plan_file:
        from pathlib import Path

        text = Path(args.plan_file).read_text(encoding="utf-8-sig", errors="replace")
    payload = validate_material_porting_plan(text)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
