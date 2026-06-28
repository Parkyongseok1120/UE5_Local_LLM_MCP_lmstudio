#!/usr/bin/env python
"""Build unified project graph JSON (Phase 21)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl_metadata(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield row.get("metadata") or row


def add_dependency_edges(edges: list[dict[str, Any]], source_id: str, dependencies: list[Any]) -> None:
    for dep in dependencies[:80]:
        dep_path = str(dep)
        if dep_path:
            edges.append({"from": source_id, "to": f"asset:{dep_path}", "kind": "depends_on_asset"})


def build_graph(workspace: Path, project_root: Path, project_name: str) -> dict[str, Any]:
    index_dir = workspace / "data" / "unreal58"
    pab_path = index_dir / "project_architecture.json"
    pab = load_json(pab_path) if isinstance(load_json(pab_path), dict) else {}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for mod in pab.get("modules") or []:
        nodes.append({"id": f"module:{mod.get('name')}", "type": "module", "label": mod.get("name")})
        for dep in (mod.get("dependencies") or {}).get("public") or []:
            edges.append({"from": f"module:{mod.get('name')}", "to": f"module:{dep}", "kind": "depends_on"})

    for cls in pab.get("classes") or []:
        nid = f"class:{cls.get('name')}"
        nodes.append({"id": nid, "type": "class", "path": cls.get("path"), "macro": cls.get("macro")})
        if cls.get("meta", {}).get("parent"):
            edges.append({"from": nid, "to": f"class:{cls['meta']['parent']}", "kind": "inherits"})

    for sub in pab.get("subsystems") or []:
        nodes.append({"id": f"subsystem:{sub.get('name')}", "type": "subsystem", "path": sub.get("path")})

    for comp in pab.get("components") or []:
        nodes.append({"id": f"component:{comp.get('name')}", "type": "component", "path": comp.get("path")})

    for da in pab.get("dataAssets") or []:
        nodes.append({"id": f"dataasset:{da.get('name')}", "type": "data_asset", "path": da.get("path")})

    bp_raw = index_dir / "raw_blueprint_metadata.jsonl"
    for meta in read_jsonl_metadata(bp_raw) or []:
        ap = meta.get("asset_path")
        gen = meta.get("generated_class")
        parent = meta.get("parent_class")
        if ap:
            nid = f"bp:{gen or ap}"
            nodes.append({"id": nid, "type": "blueprint", "assetPath": ap, "generatedClass": gen})
            if parent:
                edges.append({"from": nid, "to": f"class:{parent}", "kind": "inherits"})
            add_dependency_edges(edges, nid, meta.get("dependencies") or [])

    material_raw = index_dir / "raw_material_metadata.jsonl"
    for meta in read_jsonl_metadata(material_raw) or []:
        ap = meta.get("asset_path")
        asset_type = meta.get("asset_type")
        parent = meta.get("parent_material")
        if ap:
            nid = f"material:{ap}"
            nodes.append({"id": nid, "type": "material", "assetPath": ap, "assetType": asset_type})
            if parent:
                edges.append({"from": nid, "to": f"material:{parent}", "kind": "material_parent"})
            add_dependency_edges(edges, nid, meta.get("dependencies") or [])

    animation_raws = (
        index_dir / "raw_animation_metadata.jsonl",
        index_dir / "raw_skeletal_mesh_metadata.jsonl",
        index_dir / "raw_anim_blueprint_metadata.jsonl",
        index_dir / "raw_anim_montage_metadata.jsonl",
        index_dir / "raw_sequencer_metadata.jsonl",
    )
    animation_type_map = {
        "SkeletalMesh": "skeletal_mesh",
        "AnimBlueprint": "anim_blueprint",
        "AnimSequence": "animation",
        "AnimMontage": "anim_montage",
        "AnimNotify": "anim_notify",
        "AnimNotifyState": "anim_notify_state",
        "LevelSequence": "sequencer",
    }
    for raw_path in animation_raws:
        for meta in read_jsonl_metadata(raw_path) or []:
            ap = meta.get("asset_path")
            asset_type = meta.get("asset_type") or "AnimationAsset"
            if not ap:
                continue
            node_type = animation_type_map.get(str(asset_type), "animation")
            nid = f"{node_type}:{ap}"
            nodes.append({"id": nid, "type": node_type, "assetPath": ap, "assetType": asset_type})
            if meta.get("skeleton"):
                edges.append({"from": nid, "to": f"skeleton:{meta['skeleton']}", "kind": "uses_skeleton"})
            if meta.get("physics_asset"):
                edges.append({"from": nid, "to": f"physics_asset:{meta['physics_asset']}", "kind": "uses_physics_asset"})
            for material in meta.get("materials") or []:
                material_name = material.get("material") if isinstance(material, dict) else str(material)
                if material_name:
                    edges.append({"from": nid, "to": f"material:{material_name}", "kind": "uses_material"})
            add_dependency_edges(edges, nid, meta.get("dependencies") or [])

    graph = {
        "project": project_name,
        "projectRoot": str(project_root),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "classCount": len(pab.get("classes") or []),
            "blueprintCount": sum(1 for n in nodes if n.get("type") == "blueprint"),
            "materialCount": sum(1 for n in nodes if n.get("type") == "material"),
            "animationAssetCount": sum(
                1
                for n in nodes
                if n.get("type")
                in {"skeletal_mesh", "anim_blueprint", "animation", "anim_montage", "anim_notify", "anim_notify_state", "sequencer"}
            ),
        },
    }
    return graph


def query_graph(graph: dict[str, Any], *, node_type: str = "", name_contains: str = "") -> list[dict[str, Any]]:
    nodes = graph.get("nodes") or []
    out = []
    for node in nodes:
        if node_type and node.get("type") != node_type:
            continue
        label = str(node.get("label") or node.get("id") or "")
        if name_contains and name_contains.lower() not in label.lower():
            if name_contains.lower() not in str(node.get("path", "")).lower():
                continue
        out.append(node)
    return out[:40]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build project graph JSON.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--project-name", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    workspace = Path(__file__).resolve().parent.parent
    project_root = Path(args.project_root).resolve()
    project_name = args.project_name or project_root.name
    graph = build_graph(workspace, project_root, project_name)

    out_dir = workspace / "data" / "unreal_projects"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"{project_name}_project_graph.json"
    out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    jsonl_path = out_dir / f"{project_name}_project_graph.jsonl"
    jsonl_path.write_text(
        json.dumps({"source": "project_graph", "project": project_name, "graph": graph}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({graph['summary']['nodeCount']} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
