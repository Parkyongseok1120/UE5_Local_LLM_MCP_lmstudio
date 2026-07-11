from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
RESOLVER = ROOT / "lmstudio-unreal-agent-mcp/src/read-path-resolver.js"


def test_active_project_outside_workspace_read_schemes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = tmp_path / "external" / "Demo"
    source = project / "Source" / "Demo" / "Private"
    workspace.mkdir()
    source.mkdir(parents=True)
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    target = source / "Demo.cpp"
    target.write_text("void Demo() {}", encoding="utf-8")
    script = f"""
const r=require({json.dumps(str(RESOLVER))});
(async()=>{{
 const opts={{workspaceRoot:{json.dumps(str(workspace))},activeProject:{json.dumps(str(project / 'Demo.uproject'))}}};
 const project=await r.resolveReadPath('project://Source/Demo/Private/Demo.cpp',opts);
 const corrected=await r.resolveReadPath('Demo/Source/Demo/Private/Demo.cpp',opts);
 const workspacePath=await r.resolveReadPath('workspace://',opts);
 let escaped=false;
 try {{ await r.resolveReadPath('project://../outside.txt',opts); }} catch {{ escaped=true; }}
 console.log(JSON.stringify({{project:r.pathMetadata(project),corrected:r.pathMetadata(corrected),workspace:r.pathMetadata(workspacePath),escaped}}));
}})();
"""
    proc = subprocess.run([str(NODE), "-e", script], text=True, capture_output=True, check=True)
    payload = json.loads(proc.stdout)
    assert payload["project"]["resolvedRootType"] == "active_project"
    assert payload["project"]["projectRelativePath"].endswith("Demo.cpp")
    assert payload["corrected"]["projectRelativePath"] == "Source/Demo/Private/Demo.cpp"
    assert payload["workspace"]["resolvedRootType"] == "workspace"
    assert payload["escaped"] is True


def test_junction_escape_is_blocked_when_supported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir(); project.mkdir(); outside.mkdir()
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    script = f"""
const fs=require('fs'); const path=require('path'); const r=require({json.dumps(str(RESOLVER))});
(async()=>{{
 const link=path.join({json.dumps(str(project))},'Source');
 let supported=true; try {{ fs.symlinkSync({json.dumps(str(outside))},link,'junction'); }} catch {{ supported=false; }}
 let blocked=false;
 if(supported) {{ try {{ await r.resolveReadPath('project://Source/file.cpp',{{workspaceRoot:{json.dumps(str(workspace))},activeProject:{json.dumps(str(project / 'Demo.uproject'))}}}); }} catch {{ blocked=true; }} }}
 console.log(JSON.stringify({{supported,blocked}}));
}})();
"""
    proc = subprocess.run([str(NODE), "-e", script], text=True, capture_output=True, check=True)
    payload = json.loads(proc.stdout)
    if payload["supported"]:
        assert payload["blocked"] is True
