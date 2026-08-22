"""Classify collected documents for searchable RAG metadata."""

from __future__ import annotations


def infer_doc_type(source: str, metadata: dict) -> str:
    if source == "project_guideline":
        return "guideline"
    if source == "game_design_doc":
        return "game_design"
    if source == "unreal_symbol":
        if str(metadata.get("scope") or "") == "project":
            return (
                "project_module_symbol"
                if str(metadata.get("symbol_kind") or "") == "module"
                else "project_symbol"
            )
        symbol_kind = str(metadata.get("symbol_kind") or "")
        if symbol_kind == "module":
            return "module_symbol"
        if symbol_kind in {"class", "struct", "interface", "enum"}:
            return "type_symbol"
        if symbol_kind in {"function", "function_definition"}:
            return "function_symbol"
        if symbol_kind == "include_map":
            return "include_symbol"
        return "symbol"
    direct_types = {
        "project_profile": "project_profile",
        "project_architecture": "project_architecture",
        "build_log": "build_error",
        "epic_docs": "official_doc",
        "unreal_source": "source_code",
        "unreal_project_text": "project_text",
        "unreal_project_asset_path": "asset_path",
        "unreal_blueprint_metadata": "blueprint_metadata",
        "unreal_material_metadata": "material_metadata",
        "unreal_animation_metadata": "animation_metadata",
        "unreal_skeletal_mesh_metadata": "skeletal_mesh_metadata",
        "unreal_anim_blueprint_metadata": "anim_blueprint_metadata",
        "unreal_anim_montage_metadata": "anim_montage_metadata",
        "unreal_sequencer_metadata": "sequencer_metadata",
        "unreal_asset_registry": "asset_registry",
        "unreal_project_settings": "project_settings",
        "unreal_level_metadata": "level_metadata",
    }
    return direct_types.get(source, source or "unknown")


def infer_layer(source: str, title: str, metadata: dict) -> str:
    direct_layers = {
        "epic_docs": "official_docs",
        "unreal_source": "unreal_source",
        "unreal_project_text": "project_text",
        "unreal_project_asset_path": "project_asset_path",
        "unreal_blueprint_metadata": "project_architecture",
        "unreal_material_metadata": "project_architecture",
        "unreal_animation_metadata": "project_architecture",
        "unreal_skeletal_mesh_metadata": "project_architecture",
        "unreal_anim_blueprint_metadata": "project_architecture",
        "unreal_anim_montage_metadata": "project_architecture",
        "unreal_sequencer_metadata": "project_architecture",
        "unreal_asset_registry": "project_architecture",
        "unreal_project_settings": "project_architecture",
        "unreal_level_metadata": "project_architecture",
        "game_design_doc": "game_design",
        "unreal_symbol": "unreal_symbol",
        "project_profile": "project_profile",
        "project_architecture": "project_architecture",
    }
    if source in direct_layers:
        return direct_layers[source]
    if source == "build_log":
        return str(metadata.get("error_kind") or "build_log")
    if source != "project_guideline":
        return "unknown"

    relative_path = str(metadata.get("relative_path") or "").replace("\\", "/")
    prefixes = {
        "Planning/": "planning",
        "Genre_Gameplay/": "genre",
        "Core_Architecture/": "core_architecture",
    }
    for prefix, layer in prefixes.items():
        if relative_path.startswith(prefix):
            return layer
    lowered = f"{title} {relative_path}".lower()
    if any(marker in lowered for marker in ("unreal", "damage", "implementation")):
        return "unreal_domain"
    if any(marker in lowered for marker in ("response", "review", "process")):
        return "core_architecture"
    return "project_rule"


def infer_genre(title: str, metadata: dict) -> str:
    value = f"{title} {metadata.get('relative_path') or ''}".lower()
    genre_markers = {
        "action_combat": ("action combat", "combat", "soulslike", "dmc"),
        "shooter": ("shooter", "fps", "tps", "hitscan", "projectile"),
        "battle_royale_extraction": ("battle royale", "extraction"),
        "platformer": ("platformer",),
        "puzzle": ("puzzle",),
        "survival_crafting": ("survival", "crafting"),
        "roguelike": ("roguelike",),
        "deckbuilder": ("deckbuilder",),
        "management_sim": ("management", "simulation"),
        "strategy_tactics": ("strategy", "tactics"),
        "stealth": ("stealth",),
        "horror": ("horror",),
        "narrative": ("narrative",),
        "rhythm": ("rhythm",),
        "racing": ("racing",),
        "tower_defense": ("tower defense",),
    }
    for genre, markers in genre_markers.items():
        if any(marker in value for marker in markers):
            return genre
    return ""
