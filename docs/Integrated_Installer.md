# Integrated cross-platform installer

The repository has one canonical installer for the portable evidence-first reasoning layer, LM Studio MCP integration, and optional Unreal adapters.

Product release label: **1.3.1**. The installer reports the same value with `python3 install.py --version`; the immutable stable `v1.3.0` snapshot uses portable manifest `2.1.8`, stable `v1.3.1` uses `2.1.11`, and current `main` uses post-release portable manifest `2.1.13` for the Python-free launcher seed path plus the compactor/evidence-contract repair. `portablePackage.releaseReady: true` records automated release/package readiness, not universal physical-install certification.

## Requirements

- `INSTALL.bat` and `install.sh` use an existing host **Python 3.10+** when available. If none is usable, they automatically download the pinned uv asset for the host, verify its SHA-256, install managed **Python 3.12** under the selected user state-home, and launch the same `install.py`. No system-wide Python registration or PATH mutation is performed. Direct `python3 install.py` invocation still requires host Python 3.10+; `PYTHON=/path/to/python3.12 ./install.sh` remains available for an interpreter outside PATH.
- After the pre-Python bridge, the installer keeps managed **Python 3.12** as the supported execution runtime. It downloads **Node.js 20+/npm** for LM Studio context-compactor installs (required whenever LM Studio/Unreal components are selected). Direct `--build-rag` indexing runs the managed Python collectors directly and does not require or bootstrap PowerShell.
- Runtime archives are pinned by version and SHA-256 for x64/arm64 on Windows, Apple Silicon macOS, and Ubuntu/glibc. [`installer/runtime-manifest.json`](../installer/runtime-manifest.json) is the SSOT for URL, filename, platform, architecture, checksum, executable, and probe metadata. Extraction rejects traversal, unsafe links, encrypted ZIP members, special files, and archive bombs before writing the runtime cache.
- **Intel macOS:** LM Studio / Unreal / context-compactor installs abort early. Custom Codex / portable_rule / Cline-only installs are allowed.
- **Apple Silicon macOS:** physical FULL install verified on darwin-arm64 (runtimes, Context Compactor 45/45, LM Studio plugin installation/pinning, UE 5.8 auto-discovery, full RAG 88,829 chunks, evidence-first MCP smoke, installer `ok: true`). Chat-level activation was not durably proven. Separate limitations: Unreal Editor asset metadata headless export **FAIL**; LM Studio API server connectivity **UNVERIFIED** when the API server was not running; installer signing/notarization is **not claimed**. The repository release notes retain the detailed historical record.
- **Windows:** automated fixture/CI installer and Direct MCP paths are exercised. A prior native Windows LM Studio GUI session used the RAG/MCP tools and reached a real UBT invocation against a local Unreal project. That is runtime workflow evidence, not proof of a clean-machine physical installer lifecycle; universal project, engine, and plugin compatibility is not claimed.
- Every supported LM Studio/Unreal profile needs LM Studio 0.4+ and its `lms`
  CLI because the context-compactor files are installed as a bundled component.
  Installation does not mean chat activation: the top-level chat-plugin switch
  is host-owned and is never enabled by the installer; verify it remains OFF in
  each chat. Only the explicitly unsupported emergency bypass can
  omit the files. Intel macOS cannot install these components.
- RAG index generation is a separate opt-in action. Lite requires a project search root or active project; Standard and Full additionally require a resolvable Unreal Engine source tree.

### Host baseline

| Host | Supported bootstrap baseline | Host-specific behavior |
|---|---|---|
| Windows 10/11 | x64 or arm64; Windows PowerShell for a Python-free first launch | `INSTALL.bat` automatically seeds managed Python when necessary. Direct indexing runs through managed Python. Epic Launcher manifests and Program Files locations are scanned. |
| macOS | Apple Silicon or Intel; POSIX `sh`, `curl`/`wget`, `tar`, and a SHA-256 utility for a Python-free first launch | Detects Apple Silicon even under Rosetta, selects a native runtime archive, clears quarantine on the managed runtime, and validates it by execution. |
| Ubuntu Linux | Ubuntu 22.04/24.04, glibc, x64 or arm64 | This is the Linux baseline. musl/Alpine fails early because the pinned GNU runtimes are incompatible; other glibc distributions are best-effort and are identified during bootstrap. |

Ubuntu needs CA certificates and the standard download/archive utilities when no
usable Python is already present. A normal desktop installation generally has
these; minimal images can install them with:

```text
sudo apt-get update
sudo apt-get install -y ca-certificates curl tar coreutils
```

PowerShell 7 is not an installer or `--build-rag` dependency. It is needed only
when an operator chooses the optional `rag.ps1` maintenance wrapper, for example:

```sh
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope project_source
```

These are implemented and fixture-tested paths. The repository release notes retain the Apple Silicon physical FULL-install record and the native Windows LM Studio GUI/RAG/UBT workflow record. Release readiness means the final source, package, installer, safety, and cross-platform automation gates pass. It does not erase the listed Apple Silicon limitations or claim a clean-machine physical installer lifecycle on every host.

## Start

```text
Windows: INSTALL.bat
Ubuntu Linux/macOS: ./install.sh
Any OS:  python3 install.py
```

The first two commands are the recommended clean-machine entry points because
they can establish Python themselves. The third is an automation/developer entry
point and therefore presumes Python 3.10+ already exists.

Without `--yes`, the installer asks for a profile and optional components. If the Unreal adapter is included, it then shows a numbered authority selector:

```text
1. SAFE (recommended: analysis only; no writes, commands, or builds)
2. AGENT (allows project writes, commands, and Unreal builds)
```

AGENT requires a second confirmation. Declining that confirmation continues safely in read-only mode instead of failing the installation. A final summary displays the profile, components, authority, and RAG choice before any installation work starts.

| Profile | Installed components | Runtime authority |
|---|---|---|
| SAFE | Codex skill, LM Studio preset, context compactor installed/pinned but not chat-activated | No project adapter; known unsafe legacy Unreal flags are normalized to off |
| STANDARD | SAFE plus Unreal RAG/agent adapters (same compactor policy) | Read-only |
| FULL | Same required components as STANDARD (kept for compatibility) | Read-only |
| CUSTOM | Explicit components; LM Studio/Unreal selections still force context compactor | Read-only by default |

Install profile and RAG indexing depth are independent. Use `--index-tier lite|standard|full`; selecting FULL does not select full indexing and never builds an index unless `--build-rag` is also supplied. Installed RAG data is owned by the stable `<state-home>/indexes/<namespace>/` directory (default `~/.evidence-first/indexes/`), never by a versioned package directory. An upgrade reuses a ready managed index or migrates the newest query-ready prior package index with hard links when possible and copies otherwise. Each generation is bound to one engine version or custom association. Standard/Full builds may include multiple compatible projects for that engine; incompatible projects use sibling namespaces, and one query never merges different engine shards.

Every LM Studio / Unreal profile installs and pins the transparent context-compactor plugin, but does not enable it for a chat. The top-level `codex/unreal-context-compactor` switch is host-owned and must be verified OFF per chat by default. For a long chat that needs bounded continuity, enable that single switch; invoking the handler activates compaction, and `Observe only` remains available for measurement without history rewriting. The plugin does not proxy the model, select tools, or grant write/build authority. The selected real model becomes write-capable only after AGENT authority is explicitly enabled. Cline, CLI, Ollama, custom, and remote frontends require their own continuity policy.

The context-compactor **installation component** is required for every LM Studio / Unreal install profile. Interactive installs no longer offer an opt-out. `--skip-context-compactor` is blocked unless paired with `--allow-skip-context-compactor` (unsupported emergency bypass). On Windows, macOS, and Linux the installer resolves the `lms` CLI in this order: `LMSTUDIO_CLI`, `<lmstudio-home>/bin`, OS app/PATH candidates; runs `lms dev --install -y`; ensures the plugin exists under the managed `extensions/plugins/codex/unreal-context-compactor` (syncing from the host default LM Studio home or materializing from the repo when `lms` wrote elsewhere); and pins `codex/unreal-context-compactor` with `developer.allowDevelopmentPlugins=true` in that home's `settings.json`. Pinning only keeps the shortcut visible and is not activation.

> **Important — select the real LLM and keep the compactor OFF by default.**
> Installing/pinning the plugin only makes it available.
> 1. Load and select the actual instruction/tool-calling model in LM Studio's **model dropdown**. Qwen 3.8 27B is the current primary validated recommendation; Muse Glimmer is under testing only.
> 2. Create or open the chat and leave the top-level **`codex/unreal-context-compactor`** switch **OFF** in that chat's **plugin panel**. Existing chats retain their own state, so turn it off manually where necessary.
> 3. Start Local Server and enable the default `unreal-rag` and `unreal-agent` MCP entries.
> For a long chat that needs compaction, enable that single top-level switch for the chat. The installer does not rewrite LM Studio's private per-chat conversation storage.

Interactive Unreal installs first restore the project-indexing picker. Choose a `.uproject` in the native file explorer to set the active project, or choose one or more folders to add project search roots. No typed path is required.

Interactive Unreal installs without an explicit `--engine-root` or `UNREAL_ENGINE_ROOT` then ask how to resolve the engine: choose **Epic Games Launcher auto-detection**, or choose a **custom/source engine folder** in the native folder picker. The selected custom folder must contain a usable Unreal Engine layout. Explicit `--engine-root` and `UNREAL_ENGINE_ROOT` values remain authoritative.

The installer then shows a separate RAG indexing selector: **Skip** (default), **Lite**, **Standard** (recommended), or **Full**. Choosing Lite, Standard, or Full first refreshes portable project guidelines and game-design inputs, then runs the tier-aware collection pipeline before building. Standard also refreshes project text, active-project symbols/profile/architecture, and engine API symbols; Full additionally refreshes the complete `Engine/Source` text input. For non-interactive use, the equivalents are:

```text
python3 install.py --profile standard --yes --build-rag
python3 install.py --profile standard --yes --build-rag --index-tier full
python3 install.py --profile standard --yes --build-rag --active-project /path/to/Game.uproject
python3 install.py --profile standard --yes --engine-root /path/to/UnrealEngine
```

These `--build-rag` commands run a staged Python-only collection and index build.
They do not start Unreal Editor, execute Editor export scripts, copy an Editor
plugin, or modify a project's `.uproject`. Editor metadata is a separate,
explicit maintenance operation described in [Editor Metadata Export](Editor_Metadata_Export.md).

The Unreal Engine root is saved automatically when found. Windows scans Epic Games under Program Files; macOS scans `/Users/Shared/Epic Games` and `/Applications/Epic Games`; Linux checks `~/UnrealEngine`, `~/Epic Games`, `/opt/UnrealEngine`, and `/opt/Epic Games`. For a source build in another location, set `UNREAL_ENGINE_ROOT` before running the installer or pass `--engine-root`; the resolved path is persisted into the shared workspace and MCP configuration.

Native project builds use `Build.bat`/UBT `.exe` on Windows, `Mac/Build.sh` on macOS, and `Linux/Build.sh` on Linux. `dotnet UnrealBuildTool.dll` is the Unix fallback. These AGENT-authorized build paths are separate from the Python-only RAG index build and from explicitly requested Editor metadata refresh.

Selecting the optional `portable_rule` component no longer asks for an output path. It saves the rule to `<state-home>/portable-rules/evidence-first-code-audit.md` by default; use `--rule-path` only when an agent requires a specific rules-file location.

Selecting the optional `cline` component likewise patches Cline's conventional per-user MCP file, `~/.cline/data/settings/cline_mcp_settings.json`, without a path prompt. Use `--cline-settings` only for a non-standard Cline installation.

Project writes, commands, and Unreal builds require both flags:

```text
python3 install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

SAFE rejects agent mode. FULL alone never enables it.

`INSTALL.bat` and `install.sh` select the host shell and perform only the minimal pre-Python bridge when needed. The bridge reads (Windows) or is synchronization-tested against (POSIX) the pinned runtime manifest, verifies uv before execution, installs Python in the selected state-home, and dispatches the same `install.py`. Profile, component, configuration, RAG, and project logic remains owned by `install.py`; the seed helpers are not alternate installers. Advanced maintenance tools are separated under `scripts/installer_support/`.

`install.sh` is POSIX `sh`, resolves its own directory safely, and launches the same installer with an existing or automatically seeded Python. The packaged launcher is copied from this canonical file and retains executable permissions.

On Windows, `INSTALL.bat` keeps the console open after success or failure and waits for a key press. Set `INSTALL_NO_PAUSE=1` only for scripted automation that invokes the batch launcher.

## Automation and recovery

```text
python3 install.py --profile safe --yes
python3 install.py --profile standard --yes --skip-deps --workspace-root /path/to/projects
python3 install.py --profile full --yes
python3 install.py --rollback
```

`--skip-deps` only reuses dependencies that are already resolvable from each
component directory. It does not make dependencies optional. An Unreal install
fails before writing LM Studio MCP configuration when
`@modelcontextprotocol/sdk/server/index.js` cannot be resolved; rerun without
`--skip-deps` to execute the pinned `npm ci` installation. Portable ZIPs
intentionally exclude `node_modules`.

`--skip-runtime-bootstrap` is an expert/CI override. On a machine with no usable
Python, the launchers fail instead of downloading a seed when this flag is
present. On a fresh machine, omit it.

For a Portable ZIP, the extracted package directory remains the installed RAG
server and Unreal Agent **code** location referenced by `mcp.json`; it does not
own the installed index. Extract it to a stable directory and retain it while
that release is active. Index data survives package upgrades under
`<state-home>/indexes/`. Do not install runtime code from an operating-system
temporary directory that may be cleaned automatically.

Every Unreal install reports `ragReadiness` separately from component install
success. The readiness probe opens the selected SQLite database read-only and
executes bounded queries against both `chunks` and `chunks_fts`. `--build-rag`
is fail-closed: the install fails if the resulting index is not query-ready.
Without `--build-rag`, a missing index is reported explicitly as degraded so a
configured adapter is never confused with a usable query data plane.

Managed skill/config files are journaled and can be restored by `--rollback`. External package-manager/plugin actions and generated indexes are reported separately and are not claimed as transactionally reversible.

Runtime bootstrap and managed installation use separate process locks. A Python re-exec retains the bootstrap ownership token, while unrelated concurrent installers fail before sharing or replacing a partially extracted cache. Managed file and journal replacements are flushed before atomic rename where the host supports it.

## Installed Direct verification

After installation, restart LM Studio so it reloads `mcp.json`. In the extracted
portable package, verify the current index without changing it:

```text
pwsh -NoProfile -File ./rag.ps1 doctor
```

Then enable the installed `unreal-rag` and `unreal-agent` entries and call
`unreal_rag_health`, `unreal_get_active_project`, and `get_workspace_info` from
LM Studio. These are the supported Direct readiness checks; none creates a
task, route, plan, or write authorization. The portable package intentionally
omits repository release-engineering, package-construction, and model-evaluation
utilities, so installed-product instructions do not depend on source-checkout-only
scripts.
