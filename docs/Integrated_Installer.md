# Integrated cross-platform installer

The repository has one canonical installer for the portable evidence-first reasoning layer, LM Studio MCP integration, and optional Unreal adapters.

Product release label: **1.3.0 RC3** (GitHub prerelease; `portablePackage.releaseReady` is `false` until Windows physical install validation and remaining release gates complete). The installer reports the same value with `python3 install.py --version`; the RC3 portable manifest is `2.1.5` (the immutable `v1.3.0-rc2` snapshot remains `2.1.3`).

## Requirements

- A host **Python 3.10+** is required to start `install.py` / `./install.sh`. On a clean Mac without 3.10+, the launcher exits with recovery instructions instead of starting. Set `PYTHON=/path/to/python3.12` when the interpreter is outside PATH.
- The installer establishes managed **Python 3.12** first. It downloads **Node.js 20+/npm** for LM Studio context-compactor installs (required whenever LM Studio/Unreal components are selected) and **PowerShell 7 (`pwsh`)** only when `--build-rag` is selected, reducing SAFE-profile failure surface.
- Runtime archives are pinned by version and SHA-256 for x64/arm64 on Windows, Apple Silicon macOS, and Ubuntu/glibc. [`installer/runtime-manifest.json`](../installer/runtime-manifest.json) is the SSOT for URL, filename, platform, architecture, checksum, executable, and probe metadata. Extraction rejects traversal, unsafe links, encrypted ZIP members, special files, and archive bombs before writing the runtime cache.
- **Intel macOS:** LM Studio / Unreal / context-compactor installs abort early. Custom Codex / portable_rule / Cline-only installs are allowed.
- **Apple Silicon macOS:** physical FULL install verified on darwin-arm64 (runtimes, Context Compactor 45/45, LM Studio plugin install/activation, UE 5.8 auto-discovery, full RAG 88,829 chunks, evidence-first MCP smoke, installer `ok: true`). Separate limitations: Unreal Editor asset metadata headless export **FAIL**; LM Studio API server connectivity **UNVERIFIED** when the API server was not running; installer signing/notarization is **not claimed**. See [RC3 notes](Release_Notes_1_3_0_RC3.md).
- **Windows:** fixture/CI install paths are exercised; physical Windows install is **not yet verified** and keeps `releaseReady` false.
- SAFE also needs LM Studio 0.4+ for native MCP API use (unsupported on Intel Mac).
- FULL context compaction additionally needs the LM Studio `lms` CLI.
- RAG index generation is a separate opt-in action and uses the bootstrapped `pwsh` plus an installed Unreal Engine.

### Host baseline

| Host | Supported bootstrap baseline | Host-specific behavior |
|---|---|---|
| Windows 10/11 | x64 or arm64, Python 3.10+ launcher | Uses `INSTALL.bat`; requires PowerShell 7 (`pwsh`) for indexing rather than silently falling back to Windows PowerShell 5.1. Epic Launcher manifests and Program Files locations are scanned. |
| macOS | Apple Silicon or Intel, Python 3.10+ launcher | Detects Apple Silicon even under Rosetta, selects a native runtime archive, clears quarantine on the managed runtime, and validates it by execution. |
| Ubuntu Linux | Ubuntu 22.04/24.04, glibc, x64 or arm64 | This is the Linux baseline. musl/Alpine fails early because the pinned GNU runtimes are incompatible; other glibc distributions are best-effort and are identified during bootstrap. |

Ubuntu bootstrap prerequisite:

```text
sudo apt-get update
sudo apt-get install -y python3 ca-certificates
```

If the downloaded PowerShell binary cannot start because host libraries are missing:

```text
sudo apt-get install -y libicu-dev libssl3 zlib1g
```

These are implemented and fixture-tested paths. Apple Silicon physical FULL install is recorded in [RC3 notes](Release_Notes_1_3_0_RC3.md); Windows physical certification and the listed Apple Silicon limitations still block `releaseReady`.

## Start

```text
Windows: INSTALL.bat
Ubuntu Linux/macOS: ./install.sh
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
| SAFE | Codex skill, LM Studio preset, **required** context compactor | No project adapter; known unsafe legacy Unreal flags are normalized to off |
| STANDARD | SAFE plus Unreal RAG/agent adapters (**context compactor required**) | Read-only |
| FULL | Same required components as STANDARD (kept for compatibility) | Read-only |
| CUSTOM | Explicit components; LM Studio/Unreal selections still force context compactor | Read-only by default |

Install profile and RAG indexing depth are independent. Use `--index-tier lite|standard|full`; selecting FULL does not select full indexing and never builds an index unless `--build-rag` is also supplied.

FULL installs the LM Studio context proxy in advisory mode. Direct Qwen/GPT selection remains write-capable after AGENT authority is explicitly enabled. Strict proxy evidence is an administrator opt-in and applies only when `MCP_FRONTEND=lmstudio` matches `MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS`; Cline, CLI, Ollama, custom, and remote frontends require their own continuity policy.

The context compactor is **required** for every LM Studio / Unreal install profile. Interactive installs no longer offer an opt-out. `--skip-context-compactor` is blocked unless paired with `--allow-skip-context-compactor` (unsupported emergency bypass). On Windows, macOS, and Linux the installer resolves the `lms` CLI in this order: `LMSTUDIO_CLI`, `<lmstudio-home>/bin`, OS app/PATH candidates; runs `lms dev --install -y`; ensures the plugin exists under the managed `extensions/plugins/codex/unreal-context-compactor` (syncing from the host default LM Studio home or materializing from the repo when `lms` wrote elsewhere); and pins `codex/unreal-context-compactor` with `developer.allowDevelopmentPlugins=true` in that home's `settings.json`.

> **Important — chat model selection (not optional if you want multi-turn compaction)**  
> Installing/pinning the plugin is not enough.  
> 1. Load the underlying LLM (e.g. Qwen) once and leave it loaded.  
> 2. **Open a new chat** — existing chats keep their previous model.  
> 3. In the chat **model dropdown**, select **`unreal-context-compactor`**.  
> Selecting Qwen/GPT directly bypasses the proxy. Mid-chat goal switches and long tool histories will not be compacted.

Interactive Unreal installs first restore the project-indexing picker. Choose a `.uproject` in the native file explorer to set the active project, or choose one or more folders to add project search roots. No typed path is required.

Interactive Unreal installs without an explicit `--engine-root` or `UNREAL_ENGINE_ROOT` then ask how to resolve the engine: choose **Epic Games Launcher auto-detection**, or choose a **custom/source engine folder** in the native folder picker. The selected custom folder must contain a usable Unreal Engine layout. Explicit `--engine-root` and `UNREAL_ENGINE_ROOT` values remain authoritative.

The installer then shows a separate RAG indexing selector: **Skip** (default), **Lite**, **Standard** (recommended), or **Full**. Choosing Lite, Standard, or Full first refreshes portable project guidelines and game-design inputs, then runs the tier-aware collection pipeline before building. Standard also refreshes project text, active-project symbols/profile/architecture, engine API symbols, and the module graph; Full additionally refreshes the complete `Engine/Source` text input. For non-interactive use, the equivalents are:

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
python3 install.py --profile full --yes
python3 install.py --rollback
```

Managed skill/config files are journaled and can be restored by `--rollback`. External package-manager/plugin actions and generated indexes are reported separately and are not claimed as transactionally reversible.

Runtime bootstrap and managed installation use separate process locks. A Python re-exec retains the bootstrap ownership token, while unrelated concurrent installers fail before sharing or replacing a partially extracted cache. Managed file and journal replacements are flushed before atomic rename where the host supports it.

Before changing a runtime version or checksum, update the runtime manifest as one unit and run:

```text
python3 scripts/manage_runtime_manifest.py validate
python3 scripts/manage_runtime_manifest.py list
# after independently verifying upstream checksum files:
python3 scripts/manage_runtime_manifest.py update-checksums checksums.json
```

The validator rejects missing platform/architecture rows, duplicate assets, non-HTTPS URL templates, malformed SHA-256 values, and missing executable probes.

## Portable package

```text
python3 scripts/build_integrated_package.py --output /safe/output/Evidence-First-Integrated --zip /safe/output/Evidence-First-Integrated.zip
```

The package contains Windows, Ubuntu Linux, and macOS launchers plus a deterministic SHA-256 inventory. It excludes user configuration, machine paths, caches, dependencies, tests, and RAG indexes by default. `--include-index` is explicit.

Package-builder status JSON is ASCII-safe so successful and failed builds remain machine-readable when a Windows runner or legacy console uses `cp1252`. Package contents and manifests remain UTF-8.

## LM Studio runtime proof and paired measurement

After installation, restart the LM Studio server so it reloads `mcp.json`. A native MCP proof must use `/api/v1/chat`; an OpenAI-compatible fallback is not accepted as MCP evidence.

```text
python3 scripts/eval_evidence_first_benchmark.py --live --require-mcp --model MODEL_ID --url http://localhost:1234 --output evidence-first-live.json
```

The report records MCP tool-call provenance and separates skill-OFF from skill-ON scores by causal bug analysis, framework semantics, data flow, state transitions, architecture, and code generation. Small local runs are labeled exploratory rather than presented as general model-quality guarantees.
