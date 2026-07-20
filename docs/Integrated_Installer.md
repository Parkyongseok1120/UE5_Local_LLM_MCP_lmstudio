# Integrated cross-platform installer

The repository has one canonical installer for the portable evidence-first reasoning layer, LM Studio MCP integration, and optional Unreal adapters.

## Requirements

- SAFE: Python 3.10+ and LM Studio 0.4+ for native MCP API use.
- STANDARD: SAFE requirements plus Node.js 20+.
- FULL: STANDARD requirements plus npm and the LM Studio `lms` CLI.
- RAG index generation is a separate opt-in action. Some Unreal/Editor indexing helpers require PowerShell and an installed Unreal Engine.

## Start

```text
Windows: INSTALL.bat
Linux/macOS: ./install.sh
Any OS:  python3 install.py
```

Without `--yes`, the installer asks for a profile and optional components. If the Unreal adapter is included, it then shows a numbered authority selector:

```text
1. SAFE (recommended: analysis only; no writes, commands, or builds)
2. AGENT (allows project writes, commands, and Unreal builds)
```

AGENT requires a second confirmation. Declining that confirmation continues safely in read-only mode instead of failing the installation. A final summary displays the profile, components, authority, and RAG choice before any installation work starts.

| Profile | Installed components | Runtime authority |
|---|---|---|
| SAFE | Codex skill, LM Studio preset, read-only evidence-first MCP | No project adapter; known unsafe legacy Unreal flags are normalized to off |
| STANDARD | SAFE plus Unreal RAG/agent adapters | Read-only |
| FULL | STANDARD plus LM Studio context compactor | Read-only |
| CUSTOM | Only explicitly selected components | Read-only by default |

Install profile and RAG indexing depth are independent. Use `--index-tier lite|standard|full`; selecting FULL does not select full indexing and never builds an index unless `--build-rag` is also supplied.

Project writes, commands, and Unreal builds require both flags:

```text
python3 install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

SAFE rejects agent mode. FULL alone never enables it.

`INSTALL.bat` and `install.sh` only select the host shell. Both launch the same `install.py`; `installer/` contains only its manifest and an explanation. Advanced maintenance tools are separated under `scripts/installer_support/`.

## Automation and recovery

```text
python3 install.py --profile safe --yes
python3 install.py --profile standard --yes --skip-deps --workspace-root /path/to/projects
python3 install.py --profile full --yes --skip-context-compactor
python3 install.py --rollback
```

Managed skill/config files are journaled and can be restored by `--rollback`. External package-manager/plugin actions and generated indexes are reported separately and are not claimed as transactionally reversible.

## Portable package

```text
python3 scripts/build_integrated_package.py --output /safe/output/Evidence-First-Integrated --zip /safe/output/Evidence-First-Integrated.zip
```

The package contains Windows, Linux, and macOS launchers plus a deterministic SHA-256 inventory. It excludes user configuration, machine paths, caches, dependencies, tests, and RAG indexes by default. `--include-index` is explicit.

## LM Studio runtime proof and paired measurement

After installation, restart the LM Studio server so it reloads `mcp.json`. A native MCP proof must use `/api/v1/chat`; an OpenAI-compatible fallback is not accepted as MCP evidence.

```text
python3 scripts/eval_evidence_first_benchmark.py --live --require-mcp --model MODEL_ID --url http://localhost:1234 --output evidence-first-live.json
```

The report records MCP tool-call provenance and separates skill-OFF from skill-ON scores by causal bug analysis, framework semantics, data flow, state transitions, architecture, and code generation. Small local runs are labeled exploratory rather than presented as general model-quality guarantees.
