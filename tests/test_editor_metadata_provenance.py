from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_editor_metadata import merge_export_into_raw, row_to_chunk  # noqa: E402
from editor_metadata_status import editor_metadata_status  # noqa: E402
from editor_sync_decision import (  # noqa: E402
    export_dir_summary,
    exports_newer_than_raw,
    raw_newest_mtime,
)
from ingest_editor_exports import discover_exports  # noqa: E402


def _write_export(path: Path, rows: list[dict], mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


def test_merged_raw_freshness_is_scoped_to_each_project_row_provenance(
    tmp_path: Path,
) -> None:
    now = time.time()
    project_a = tmp_path / "ProjectA"
    project_b = tmp_path / "ProjectB"
    project_a.mkdir()
    asset_b = project_b / "Content" / "Materials" / "M_B.uasset"
    asset_b.parent.mkdir(parents=True)
    asset_b.write_bytes(b"asset")
    os.utime(asset_b, (now - 250.0, now - 250.0))
    index = tmp_path / "index"
    index.mkdir()
    raw = index / "raw_material_metadata.jsonl"

    export_b = tmp_path / "exports-b-old" / "materials.jsonl"
    _write_export(
        export_b,
        [{"asset_path": "/Game/Materials/M_B", "asset_type": "Material"}],
        now - 300.0,
    )
    merge_export_into_raw(
        export_b,
        "material",
        "ProjectB",
        raw,
        project_root=str(project_b),
    )

    export_a = tmp_path / "exports-a" / "materials.jsonl"
    _write_export(
        export_a,
        [{"asset_path": "/Game/Materials/M_A", "asset_type": "Material"}],
        now - 200.0,
    )
    merge_export_into_raw(
        export_a,
        "material",
        "ProjectA",
        raw,
        project_root=str(project_a),
    )

    next_export_b = tmp_path / "exports-b-new" / "materials.jsonl"
    _write_export(
        next_export_b,
        [{"asset_path": "/Game/Materials/M_B", "asset_type": "Material"}],
        now - 100.0,
    )
    assert raw.stat().st_mtime > next_export_b.stat().st_mtime
    assert raw_newest_mtime(
        index,
        kinds=("material",),
        project_root=project_b,
    ) == pytest.approx(now - 300.0)
    assert raw_newest_mtime(
        index,
        kinds=("material",),
        project_root=project_a,
    ) == pytest.approx(now - 200.0)
    assert exports_newer_than_raw(
        index,
        export_dir_summary(next_export_b.parent),
        project_b,
    ) is True

    material_status = editor_metadata_status(
        index,
        project_b,
        stale_after_hours=24.0,
    )["files"]["material"]
    assert material_status["captureProvenanceKnown"] is True
    assert material_status["mtime"] == pytest.approx(now - 300.0)
    assert material_status["fileMtime"] == pytest.approx(raw.stat().st_mtime)
    assert material_status["olderThanRelevantSource"] is True


def test_same_root_descriptors_keep_independent_editor_rows_and_provenance(
    tmp_path: Path,
) -> None:
    now = time.time()
    shared = tmp_path / "Shared"
    shared.mkdir()
    project_a = shared / "GameA.uproject"
    project_b = shared / "GameB.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    raw = index / "raw_material_metadata.jsonl"
    export_a = tmp_path / "export-a" / "materials.jsonl"
    export_b = tmp_path / "export-b" / "materials.jsonl"
    _write_export(
        export_a,
        [{"asset_path": "/Game/Materials/M_Shared", "value": "a-old"}],
        now - 300.0,
    )
    _write_export(
        export_b,
        [{"asset_path": "/Game/Materials/M_Shared", "value": "b-old"}],
        now - 200.0,
    )

    merge_export_into_raw(
        export_a, "material", project_a.stem, raw, project_root=str(shared)
    )
    merge_export_into_raw(
        export_b, "material", project_b.stem, raw, project_root=str(shared)
    )
    _write_export(
        export_b,
        [{"asset_path": "/Game/Materials/M_Shared", "value": "b-new"}],
        now - 100.0,
    )
    ingested, replaced = merge_export_into_raw(
        export_b, "material", project_b.stem, raw, project_root=str(shared)
    )

    chunks = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    by_project = {row["metadata"]["project"]: row for row in chunks}
    assert (ingested, replaced) == (1, 1)
    assert len(chunks) == 2
    assert by_project["GameA"]["metadata"]["value"] == "a-old"
    assert by_project["GameB"]["metadata"]["value"] == "b-new"
    assert by_project["GameA"]["id"] != by_project["GameB"]["id"]
    assert raw_newest_mtime(
        index, kinds=("material",), project_root=project_a
    ) == pytest.approx(now - 300.0)
    assert raw_newest_mtime(
        index, kinds=("material",), project_root=project_b
    ) == pytest.approx(now - 100.0)
    assert editor_metadata_status(index, project_a, 24.0)["files"]["material"][
        "rowCount"
    ] == 1


def test_no_replace_preserves_other_rows_and_upserts_exact_identity(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "Shared"
    shared.mkdir()
    project_a = shared / "GameA.uproject"
    project_b = shared / "GameB.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    raw = tmp_path / "raw_material_metadata.jsonl"
    export_a = tmp_path / "a.jsonl"
    export_b = tmp_path / "b.jsonl"
    _write_export(
        export_a,
        [{"asset_path": "/Game/M_Shared", "value": "a"}],
        time.time() - 2.0,
    )
    _write_export(
        export_b,
        [{"asset_path": "/Game/M_Shared", "value": "b-old"}],
        time.time() - 1.0,
    )
    merge_export_into_raw(
        export_a, "material", project_a.stem, raw, project_root=str(shared)
    )
    merge_export_into_raw(
        export_b, "material", project_b.stem, raw, project_root=str(shared)
    )

    _write_export(
        export_b,
        [
            {"asset_path": "/Game/M_Shared", "value": "b-new"},
            {"asset_path": "/Game/M_Added", "value": "added"},
        ],
        time.time(),
    )
    ingested, replaced = merge_export_into_raw(
        export_b,
        "material",
        project_b.stem,
        raw,
        project_root=str(shared),
        replace_project=False,
    )

    chunks = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    by_identity = {
        (row["metadata"]["project"], row["metadata"]["asset_path"]): row
        for row in chunks
    }
    assert (ingested, replaced) == (2, 1)
    assert len(chunks) == 3
    assert by_identity[("GameA", "/Game/M_Shared")]["metadata"]["value"] == "a"
    assert by_identity[("GameB", "/Game/M_Shared")]["metadata"]["value"] == "b-new"
    assert by_identity[("GameB", "/Game/M_Added")]["metadata"]["value"] == "added"


def test_malformed_existing_raw_fails_without_overwriting_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw_material_metadata.jsonl"
    original = b'{"id":"valid","metadata":{"project":"Game"}}\n{broken\n'
    raw.write_bytes(original)
    export = tmp_path / "materials.jsonl"
    _write_export(
        export,
        [{"asset_path": "/Game/M_New", "asset_type": "Material"}],
        time.time(),
    )

    with pytest.raises(ValueError, match="Invalid JSONL object"):
        merge_export_into_raw(export, "material", "Game", raw)

    assert raw.read_bytes() == original


def test_unstamped_selected_project_rows_are_unknown_and_stale(tmp_path: Path) -> None:
    now = time.time()
    project = tmp_path / "LegacyProject"
    project.mkdir()
    index = tmp_path / "index"
    index.mkdir()
    raw = index / "raw_material_metadata.jsonl"
    legacy_chunk = row_to_chunk(
        "unreal_material_metadata",
        {"asset_path": "/Game/Materials/M_Legacy", "asset_type": "Material"},
        "LegacyProject",
        str(project),
    )
    raw.write_text(json.dumps(legacy_chunk) + "\n", encoding="utf-8")

    candidate = tmp_path / "candidate" / "materials.jsonl"
    _write_export(candidate, [{"asset_path": "/Game/Materials/M_Legacy"}], now - 60.0)
    assert raw.stat().st_mtime > candidate.stat().st_mtime
    assert raw_newest_mtime(
        index,
        kinds=("material",),
        project_root=project,
    ) is None
    assert exports_newer_than_raw(
        index,
        export_dir_summary(candidate.parent),
        project,
    ) is True

    status = editor_metadata_status(index, project, stale_after_hours=24.0)
    material = status["files"]["material"]
    assert material["exists"] is True
    assert material["captureProvenanceKnown"] is False
    assert material["freshnessUnknown"] is True
    assert material["ageHours"] is None
    assert material["olderThanRelevantSource"] is None
    assert "material" in status["staleKinds"]


def test_project_settings_with_one_ini_path_keep_distinct_setting_rows(
    tmp_path: Path,
) -> None:
    project = tmp_path / "SettingsProject"
    project.mkdir()
    raw = tmp_path / "raw_project_settings.jsonl"
    export = tmp_path / "project_settings.jsonl"
    rows = [
        {
            "path": "Config/DefaultGame.ini",
            "setting": "ProjectID",
            "title": "DefaultGame.ini: ProjectID",
            "value": "old-id",
        },
        {
            "path": "Config/DefaultGame.ini",
            "setting": "ProjectName",
            "title": "DefaultGame.ini: ProjectName",
            "value": "old-name",
        },
    ]
    _write_export(export, rows, time.time() - 10.0)
    ingested, replaced = merge_export_into_raw(
        export,
        "project_settings",
        "SettingsProject",
        raw,
        project_root=str(project),
    )
    assert (ingested, replaced) == (2, 0)
    assert len(raw.read_text(encoding="utf-8").splitlines()) == 2

    for row in rows:
        row["value"] = "new-" + row["setting"]
    _write_export(export, rows, time.time())
    ingested, replaced = merge_export_into_raw(
        export,
        "project_settings",
        "SettingsProject",
        raw,
        project_root=str(project),
    )
    chunks = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert (ingested, replaced) == (2, 2)
    assert len(chunks) == 2
    assert {chunk["metadata"]["setting"] for chunk in chunks} == {
        "ProjectID",
        "ProjectName",
    }
    assert all(chunk["metadata"]["value"].startswith("new-") for chunk in chunks)


def test_repeated_ini_array_key_uses_section_and_ordinal_identity(tmp_path: Path) -> None:
    project = tmp_path / "SettingsProject"
    project.mkdir()
    raw = tmp_path / "raw_project_settings.jsonl"
    export = tmp_path / "project_settings.jsonl"
    rows = [
        {
            "path": "Config/DefaultGame.ini",
            "section": "/Script/Game.Profiles",
            "setting": "+Profiles",
            "ordinal": ordinal,
            "title": f"Profile #{ordinal}",
            "value": value,
        }
        for ordinal, value in enumerate(("Alpha", "Bravo", "Charlie"))
    ]
    _write_export(export, rows, time.time())
    merge_export_into_raw(
        export,
        "project_settings",
        project.name,
        raw,
        project_root=str(project),
    )
    first = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert len(first) == 3
    first_ids = {row["id"] for row in first}

    for row in rows:
        row["value"] += "-updated"
    _write_export(export, rows, time.time() + 1)
    ingested, replaced = merge_export_into_raw(
        export,
        "project_settings",
        project.name,
        raw,
        project_root=str(project),
    )
    second = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert (ingested, replaced) == (3, 3)
    assert len(second) == 3
    assert {row["id"] for row in second} == first_ids


def test_discover_exports_assigns_overlapping_level_sequence_once(
    tmp_path: Path,
) -> None:
    sequence = tmp_path / "level_sequence_city.jsonl"
    level = tmp_path / "level_city.jsonl"
    sequence.write_text("{}\n", encoding="utf-8")
    level.write_text("{}\n", encoding="utf-8")

    discovered = discover_exports(tmp_path)

    assert discovered.count((sequence.resolve(), "sequencer")) == 1
    assert (sequence.resolve(), "level") not in discovered
    assert discovered.count((level.resolve(), "level")) == 1
