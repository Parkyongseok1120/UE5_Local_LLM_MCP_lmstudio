# Contributing

Thank you for your interest in contributing to UE5_Local_LLM_MCP_lmstudio.

## Getting Started

1. Fork the repository and clone your fork
2. Follow the install steps in [README.md](README.md)
3. Use Python 3.12 and Node.js 20, then run all gates below

## Required gates

Invoke pytest through the selected interpreter so the repository root remains on
the import path:

```powershell
python -m pip install -r requirements-dev.txt ruff
python -m compileall -q install.py installer scripts skills tools
python -m pytest -q --tb=short
python scripts/verify_encoding.py
ruff check scripts/ tests/ --select=E,F,W --ignore=E501,E402,F401
```

Run both Node suites from clean lockfile installs, including the compactor build
performed by its `npm test` script:

```powershell
node --test scripts/chat_history_trim.test.js scripts/stage_campaign_verify.test.js

Set-Location lmstudio-unreal-agent-mcp
npm ci --no-fund --no-audit
npm test
Set-Location ..

Set-Location lmstudio-context-compactor-plugin
npm ci --no-fund --no-audit
npm test
Set-Location ..
```

Finally run the public-source hygiene gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\installer_support\Verify-Oss-Ready.ps1
```

The cross-platform CI also repeats installer/package, Direct RAG, exact-project,
engine-shard, and safety fixtures on Windows, Ubuntu, and macOS. Passing fixtures
proves those bounded contracts; it is not evidence of a physical install on every
host/engine/project/plugin combination. Label physical validation separately and
include the exact host, engine, project, and observed command/result.

## Files You Must Never Commit

The following files are **gitignored** and contain machine-specific or personal data. Never use `git add -f` on them:

| File | Contents |
|---|---|
| `config/workspace.json` | Your local engine path, index path, project roots |
| `lmstudio-unreal-agent-mcp/config/agent-mcp.json` | Your local project search roots |
| `PORTABLE_ROOT.txt` | Your username, Python path, install timestamp |
| Generated content under `data/` | Local RAG indexes and raw exports (may contain Epic/private source). Existing tracked non-proprietary `data/baseline` fixtures are the only exception. |
| `*.sqlite` | RAG database files |
| `Reports/` | Evaluation results |

If you accidentally stage any of these, run:

```powershell
git restore --staged config/workspace.json
git restore --staged PORTABLE_ROOT.txt
```

## Code Style

- Python: standard library preferred; no new external runtime dependencies without discussion
- PowerShell: use `$ErrorActionPreference = "Stop"` in all scripts; test on PowerShell 5.1+
- Node.js: CommonJS (`require`), no transpilation

## Pull Request Checklist

- [ ] `python -m pytest -q --tb=short` passes (no new failures)
- [ ] Agent MCP `npm test` and context-compactor `npm test` pass after `npm ci`
- [ ] Python compile/encoding/ruff and root Node contract gates pass
- [ ] Project-scoped fixtures assert canonical root + descriptor-stem ownership; same-name clones and legacy ambiguity fail closed
- [ ] Engine-bound tests do not merge sibling shards or combine cross-engine project selections
- [ ] No personal home-directory paths in committed files (Windows user profiles, non-Shared macOS user homes, Linux home directories)
- [ ] No Epic Engine source content in committed files (see [EPIC_NOTICE.md](EPIC_NOTICE.md))
- [ ] README or relevant docs updated if the change affects setup or usage

## Reporting Security Issues

See [SECURITY.md](SECURITY.md).
