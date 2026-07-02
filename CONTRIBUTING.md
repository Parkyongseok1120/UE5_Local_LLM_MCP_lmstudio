# Contributing

Thank you for your interest in contributing to UE5_Local_LLM_MCP_lmstudio.

## Getting Started

1. Fork the repository and clone your fork
2. Follow the install steps in [README.md](README.md)
3. Run the test suite: `pytest --tb=short -q`

## Files You Must Never Commit

The following files are **gitignored** and contain machine-specific or personal data. Never use `git add -f` on them:

| File | Contents |
|---|---|
| `config/workspace.json` | Your local engine path, index path, project roots |
| `lmstudio-unreal-agent-mcp/config/agent-mcp.json` | Your local project search roots |
| `PORTABLE_ROOT.txt` | Your username, Python path, install timestamp |
| `data/` | RAG indexes (may contain Epic source excerpts — see EPIC_NOTICE.md) |
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

- [ ] `pytest --tb=short -q` passes (no new failures)
- [ ] No personal paths (`C:\Users\<name>\...`) in committed files
- [ ] No Epic Engine source content in committed files (see [EPIC_NOTICE.md](EPIC_NOTICE.md))
- [ ] README or relevant docs updated if the change affects setup or usage

## Reporting Security Issues

See [SECURITY.md](SECURITY.md).
