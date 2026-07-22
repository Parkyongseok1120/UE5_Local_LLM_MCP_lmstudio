# Integrated cross-platform installer

The repository has one canonical installer for the portable evidence-first reasoning layer, LM Studio MCP integration, and optional Unreal adapters.

## Requirements

- SAFE: Python 3.10+ and LM Studio 0.4+ for native MCP API use.
- STANDARD: SAFE requirements plus Node.js 20+.
- FULL: STANDARD requirements plus npm and the LM Studio `lms` CLI.
- RAG index generation is a separate opt-in action. Lite/Standard/Full indexing requires PowerShell Core (`pwsh`) and an installed Unreal Engine on Linux/macOS.

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

Interactive Unreal installs first restore the project-indexing picker. Choose a `.uproject` in the native file explorer to set the active project, or choose one or more folders to add project search roots. No typed path is required.

The installer then shows a separate RAG indexing selector: **Skip** (default), **Lite**, **Standard** (recommended), or **Full**. Choosing Lite, Standard, or Full runs the complete tier-aware collection pipeline before building: Standard refreshes project text, active-project symbols/profile/architecture, engine API symbols, and the module graph; Full additionally refreshes the complete `Engine/Source` text input. For non-interactive use, the equivalents are:

```text
python3 install.py --profile standard --yes --build-rag
python3 install.py --profile standard --yes --build-rag --index-tier full
python3 install.py --profile standard --yes --build-rag --active-project /path/to/Game.uproject
python3 install.py --profile standard --yes --engine-root /path/to/UnrealEngine
```

The Unreal Engine root is saved automatically when found. Windows scans Epic Games under Program Files; macOS scans `/Users/Shared/Epic Games` and `/Applications/Epic Games`; Linux checks `~/UnrealEngine`, `~/Epic Games`, `/opt/UnrealEngine`, and `/opt/Epic Games`. For a source build in another location, set `UNREAL_ENGINE_ROOT` before running the installer or pass `--engine-root`; the resolved path is persisted into the shared workspace and MCP configuration.

Native builds use `Build.bat`/UBT `.exe` on Windows, `Mac/Build.sh` on macOS, and `Linux/Build.sh` on Linux. `dotnet UnrealBuildTool.dll` is the Unix fallback. Automatic Editor metadata export resolves both macOS app-bundle and direct binary layouts under `Engine/Binaries/Mac`, plus the Linux editor under `Engine/Binaries/Linux`.

Selecting the optional `portable_rule` component no longer asks for an output path. It saves the rule to `<state-home>/portable-rules/evidence-first-code-audit.md` by default; use `--rule-path` only when an agent requires a specific rules-file location.

Selecting the optional `cline` component likewise patches Cline's conventional per-user MCP file, `~/.cline/data/settings/cline_mcp_settings.json`, without a path prompt. Use `--cline-settings` only for a non-standard Cline installation.

Project writes, commands, and Unreal builds require both flags:

```text
python3 install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

SAFE rejects agent mode. FULL alone never enables it.

`INSTALL.bat` and `install.sh` only select the host shell. Both launch the same `install.py`; `installer/` contains only its manifest and an explanation. Advanced maintenance tools are separated under `scripts/installer_support/`.

`install.sh` is POSIX `sh`, resolves its own directory safely, and launches the same installer with `python3`. The packaged launcher is copied from this canonical file and retains executable permissions.

On Windows, `INSTALL.bat` keeps the console open after success or failure and waits for a key press. Set `INSTALL_NO_PAUSE=1` only for scripted automation that invokes the batch launcher.

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
