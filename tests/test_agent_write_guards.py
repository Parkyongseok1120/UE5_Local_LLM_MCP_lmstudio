from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDS = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "write-guards.js"


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "-e", script],
        cwd=str(ROOT / "lmstudio-unreal-agent-mcp"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_find_source_basename_collisions(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    first = project / "Source" / "Demo" / "Public" / "A" / "HealthComponent.h"
    second = project / "Source" / "Demo" / "Public" / "B" / "HealthComponent.h"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    target = project / "Source" / "Demo" / "Public" / "C" / "HealthComponent.h"
    script = f"""
const path = require('path');
const guards = require('./src/write-guards.js');
(async () => {{
  const workspace = {json.dumps(str(tmp_path))};
  const projectDir = {json.dumps(str(project))};
  const target = {json.dumps(str(target))};
  const hits = await guards.findSourceBasenameCollisions(target, workspace, projectDir);
  console.log(JSON.stringify(hits));
}})();
"""
    payload = _run_node(script)
    assert len(payload) == 2


def test_is_delete_allowed_only_under_source(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    allowed = project / "Source" / "Demo" / "Private" / "Old.cpp"
    denied = project / "Content" / "Old.cpp"
    allowed.parent.mkdir(parents=True)
    denied.parent.mkdir(parents=True)
    allowed.write_text("x", encoding="utf-8")
    denied.write_text("x", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    script = f"""
const guards = require('./src/write-guards.js');
const allowed = guards.isDeleteAllowedPath({json.dumps(str(allowed))}, {json.dumps(str(tmp_path))}, {json.dumps(str(uproject))});
const denied = guards.isDeleteAllowedPath({json.dumps(str(denied))}, {json.dumps(str(tmp_path))}, {json.dumps(str(uproject))});
console.log(JSON.stringify({{ allowed: allowed.ok, denied: denied.ok }}));
"""
    payload = _run_node(script)
    assert payload["allowed"] is True
    assert payload["denied"] is False

def test_validate_write_target_allows_new_source_but_blocks_existing_source(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Private" / "NewActor.cpp"
    target.parent.mkdir(parents=True)
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    script = f"""
const fs = require('fs');
const guards = require('./src/write-guards.js');
(async () => {{
  const common = {{
    targetAbsPath: {json.dumps(str(target))},
    workspaceRoot: {json.dumps(str(tmp_path))},
    activeProjectPath: {json.dumps(str(project / 'Demo.uproject'))},
    createDirs: false,
    fileExists: async (p) => fs.existsSync(p),
  }};
  const before = await guards.validateWriteTarget(common);
  fs.writeFileSync({json.dumps(str(target))}, 'x');
  const after = await guards.validateWriteTarget(common);
  console.log(JSON.stringify({{ before: before.ok, after: after.ok, afterMessage: after.message }}));
}})();
"""
    payload = _run_node(script)
    assert payload["before"] is True
    assert payload["after"] is False
    assert "Use replace_in_file instead" in payload["afterMessage"]

def test_resolve_validate_on_write_reads_current_allow_write_env() -> None:
    script = """
const validateWrite = require('./src/validate-write.js');
delete process.env.VALIDATE_ON_WRITE;
process.env.ALLOW_WRITE = '1';
const defaultOn = validateWrite.resolveValidateOnWrite();
process.env.VALIDATE_ON_WRITE = '0';
const explicitOff = validateWrite.resolveValidateOnWrite();
process.env.VALIDATE_ON_WRITE = 'yes';
const explicitOn = validateWrite.resolveValidateOnWrite();
console.log(JSON.stringify({ defaultOn, explicitOff, explicitOn }));
"""
    payload = _run_node(script)
    assert payload == {
        "defaultOn": True,
        "explicitOff": False,
        "explicitOn": True,
    }

