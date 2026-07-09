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

def test_write_file_create_only_blocks_all_existing_extensions(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    (project / "Config").mkdir(parents=True)
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    header = project / "Source" / "Demo" / "Public" / "Existing.h"
    header.parent.mkdir(parents=True)
    header.write_text("x", encoding="utf-8")
    doc = project / "Notes.md"
    doc.write_text("x", encoding="utf-8")
    data = project / "data.json"
    data.write_text("{}", encoding="utf-8")
    new_doc = project / "Fresh.md"
    script = f"""
const fs = require('fs');
const guards = require('./src/write-guards.js');
(async () => {{
  const workspace = {json.dumps(str(tmp_path))};
  const activeProjectPath = {json.dumps(str(project / 'Demo.uproject'))};
  const call = (target, allowExistingWrite) => guards.validateWriteTarget({{
    targetAbsPath: target,
    workspaceRoot: workspace,
    activeProjectPath,
    createDirs: false,
    fileExists: async (p) => fs.existsSync(p),
    allowExistingWrite,
  }});
  const existingHeader = await call({json.dumps(str(header))}, false);
  const existingMd = await call({json.dumps(str(doc))}, false);
  const existingJson = await call({json.dumps(str(data))}, false);
  const newMd = await call({json.dumps(str(new_doc))}, false);
  const overrideJson = await call({json.dumps(str(data))}, true);
  console.log(JSON.stringify({{
    existingHeader: existingHeader.ok,
    existingMd: existingMd.ok,
    existingMdMsg: existingMd.message,
    existingJson: existingJson.ok,
    newMd: newMd.ok,
    overrideJson: overrideJson.ok,
  }}));
}})();
"""
    payload = _run_node(script)
    assert payload["existingHeader"] is False
    assert payload["existingMd"] is False
    assert payload["existingJson"] is False
    assert payload["newMd"] is True
    assert payload["overrideJson"] is True
    assert "already exists" in payload["existingMdMsg"]
    assert "replace_in_file" in payload["existingMdMsg"]


def test_should_rollback_only_when_content_matches_own_write() -> None:
    script = """
const guards = require('./src/write-guards.js');
console.log(JSON.stringify({
  match: guards.shouldRollback('abc', 'abc'),
  conflict: guards.shouldRollback('newer content', 'abc'),
  nullCurrent: guards.shouldRollback(null, 'abc'),
}));
"""
    payload = _run_node(script)
    assert payload == {"match": True, "conflict": False, "nullCurrent": False}


def test_write_locks_single_flight_per_path() -> None:
    script = """
const locks = require('./src/write-locks.js');
const a1 = locks.tryAcquirePathLock('/tmp/one', 'write_file');
const a2 = locks.tryAcquirePathLock('/tmp/one', 'replace_in_file');
const lockedDuring = locks.isPathLocked('/tmp/one');
const other = locks.tryAcquirePathLock('/tmp/two', 'write_file');
locks.releasePathLock('/tmp/one');
const a3 = locks.tryAcquirePathLock('/tmp/one', 'write_file');
console.log(JSON.stringify({
  a1: a1.ok,
  a2: a2.ok,
  lockedDuring,
  other: other.ok,
  a3: a3.ok,
}));
"""
    payload = _run_node(script)
    assert payload == {
        "a1": True,
        "a2": False,
        "lockedDuring": True,
        "other": True,
        "a3": True,
    }


def test_with_path_lock_reports_conflict_and_releases() -> None:
    script = """
const locks = require('./src/write-locks.js');
(async () => {
  let innerRan = false;
  const outer = locks.withPathLock('/tmp/lockme', 'write_file', async () => {
    const nested = await locks.withPathLock('/tmp/lockme', 'write_file', async () => {
      innerRan = true;
      return 'inner';
    });
    return nested;
  });
  const outerResult = await outer;
  const afterRelease = locks.tryAcquirePathLock('/tmp/lockme', 'write_file');
  locks.releasePathLock('/tmp/lockme');
  console.log(JSON.stringify({
    innerLocked: outerResult.result.locked,
    innerRan,
    afterRelease: afterRelease.ok,
  }));
})();
"""
    payload = _run_node(script)
    assert payload["innerLocked"] is True
    assert payload["innerRan"] is False
    assert payload["afterRelease"] is True


def test_resolve_validate_on_write_timeout_ms_default_and_env() -> None:
    script = """
const validateWrite = require('./src/validate-write.js');
delete process.env.VALIDATE_ON_WRITE_TIMEOUT_MS;
const defaultMs = validateWrite.resolveValidateOnWriteTimeoutMs();
process.env.VALIDATE_ON_WRITE_TIMEOUT_MS = '12000';
const customMs = validateWrite.resolveValidateOnWriteTimeoutMs();
process.env.VALIDATE_ON_WRITE_TIMEOUT_MS = '-5';
const invalidMs = validateWrite.resolveValidateOnWriteTimeoutMs();
console.log(JSON.stringify({ defaultMs, customMs, invalidMs }));
"""
    payload = _run_node(script)
    assert payload["defaultMs"] == 45000
    assert payload["customMs"] == 12000
    assert payload["invalidMs"] == 45000


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

