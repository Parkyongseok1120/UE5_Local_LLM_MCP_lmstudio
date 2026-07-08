#!/usr/bin/env python3
"""Probe whether holdout fixtures compile after golden-like Build.cs fixes (clean temp copy)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))
from workspace_paths import resolve_ubt_path  # noqa: E402
from ubt_utils import ubt_subprocess_env  # noqa: E402

DEFAULT_UBT = resolve_ubt_path()
SKIP_DIRS = {"Intermediate", "Binaries", "Saved", "DerivedDataCache", ".vs", "golden", "request.txt"}


def run_ubt(ubt: Path, project: Path, target: str, timeout: int) -> tuple[int, str]:
    target_name, platform, configuration = target.split()
    cmd = [
        str(ubt),
        project.stem,
        platform,
        configuration,
        f"-Project={project}",
        f"-Target={target_name}",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(project.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env=ubt_subprocess_env(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def copy_fixture_clean(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_DIRS:
            continue
        out = dest / item.name
        if item.is_dir():
            if out.exists():
                shutil.rmtree(out)
            shutil.copytree(item, out)
        else:
            shutil.copy2(item, out)


def patch_build_cs(work_dir: Path, modules: list[str]) -> None:
    path = work_dir / "Source" / "HoldoutFixture" / "HoldoutFixture.Build.cs"
    text = path.read_text(encoding="utf-8")
    joined = ", ".join(f'"{name}"' for name in modules)
    old = 'PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });'
    new = f"PublicDependencyModuleNames.AddRange(new string[] {{ {joined} }});"
    if old not in text:
        raise RuntimeError(f"unexpected Build.cs template in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def probe_case(case_id: str, modules: list[str], ubt: Path, timeout: int) -> dict:
    src = ROOT / "data" / "local_holdout_fixtures" / case_id
    if not src.is_dir():
        return {"case": case_id, "error": f"missing fixture: {src}"}
    with tempfile.TemporaryDirectory(prefix=f"probe_{case_id}_") as tmp:
        work = Path(tmp)
        copy_fixture_clean(src, work)
        patch_build_cs(work, modules)
        rc, out = run_ubt(ubt, work / "HoldoutFixture.uproject", "HoldoutFixtureEditor Win64 Development", timeout)
        tail = out[-1500:]
        ok = rc == 0
        return {"case": case_id, "modules": modules, "ok": ok, "returncode": rc, "tail": tail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ubt-path", type=Path, default=DEFAULT_UBT)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    probes = [
        ("local_gameplaytags_missing_module", ["Core", "CoreUObject", "Engine", "GameplayTags"]),
        ("local_enhanced_input_missing_module", ["Core", "CoreUObject", "Engine", "EnhancedInput"]),
        (
            "local_enhanced_input_missing_module+InputCore",
            ["Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput"],
        ),
    ]
    for case_id, modules in probes:
        if case_id.endswith("+InputCore"):
            real_case = case_id.replace("+InputCore", "")
        else:
            real_case = case_id
        result = probe_case(real_case, modules, args.ubt_path, args.timeout)
        result["label"] = case_id
        print(f"=== {case_id} ===")
        print(f"ok={result.get('ok')} rc={result.get('returncode')}")
        if result.get("error"):
            print(result["error"])
        else:
            tail = str(result.get("tail", ""))
            try:
                print(tail)
            except UnicodeEncodeError:
                print(tail.encode("ascii", errors="replace").decode("ascii"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
