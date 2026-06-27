# Run inside Unreal Editor Python
# export_project_settings(r'C:\export\project_settings.jsonl')

import json
from pathlib import Path


def export_project_settings(out_path: str) -> None:
    import unreal

    project_dir = Path(unreal.Paths.project_dir())
    rows = []
    for ini_name in ("DefaultGame.ini", "DefaultEngine.ini", "DefaultInput.ini"):
        ini_path = project_dir / "Config" / ini_name
        if not ini_path.is_file():
            continue
        text = ini_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(";") or line.startswith("["):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                rows.append(
                    {
                        "path": f"Config/{ini_name}",
                        "setting": key.strip(),
                        "value": value.strip(),
                        "title": f"{ini_name}: {key.strip()}",
                    }
                )
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} project settings rows to {out_path}")
