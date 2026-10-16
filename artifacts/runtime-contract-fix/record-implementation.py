"""Fingerprint only this task's implementation diff; exclude user binary changes."""
import hashlib
import json
import pathlib
import subprocess

out = pathlib.Path(__file__).parent
repo = out.parent.parent
base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
patch = subprocess.check_output(["git", "diff", "--binary", "--", ".", ":!artifacts", ":!unity-symbol-worker/bin/Release/net8.0"], cwd=repo)
(out / "implementation.patch").write_bytes(patch)
added = "lmstudio-context-compactor-plugin/test/tool-round-lifecycle.test.cjs"
parts = {"baseSha": base, "trackedDiffSha256": hashlib.sha256(patch).hexdigest(),
    "newFiles": {added: hashlib.sha256((repo / added).read_bytes()).hexdigest()}}
digest = hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
result = {**parts, "implementationDiffHash": digest,
    "definition": "SHA256 of canonical JSON (sorted keys, compact separators) containing baseSha, trackedDiffSha256, newFiles.",
    "excludes": ["artifacts/**", "unity-symbol-worker/bin/Release/net8.0/**"]}
(out / "implementation-diff.json").write_text(json.dumps(result, indent=2), encoding="utf8")
print(json.dumps(result))
