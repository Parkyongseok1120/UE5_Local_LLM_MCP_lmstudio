# Internal installer support

Do not start installation from this directory.

The supported user entry points are intentionally limited to:

- Windows: `INSTALL.bat`
- Ubuntu Linux and macOS: `install.sh`
- Any operating system or automation: `install.py`

`install.py` is the only installer implementation. `bootstrap_python.ps1` and
`bootstrap_python.sh` only bridge a Python-free host into that implementation;
they contain no profile, component, configuration, RAG, or project workflow.
Other modules in this directory bootstrap pinned runtimes and construct the
staged Python-only Direct RAG build; they are support code, not alternative
installation choices. Optional Unreal maintenance and verification wrappers
live under `scripts/installer_support/`.

During an interactive STANDARD, FULL, or compatible CUSTOM installation, the
installer presents independent RAG-indexing and Unreal-authority choices. RAG
indexing can be skipped, or built at Lite, Standard, or Full depth; it is not
implied by the install profile. Before choosing the tier, the installer restores
the native `.uproject` / folder picker used to configure the active project and
project search roots, then asks whether to auto-detect an Epic Games Launcher
engine or select a custom/source engine folder. The authority choice is:

1. SAFE (read-only, recommended)
2. AGENT (project writes, commands, and Unreal builds)

AGENT authority requires a second confirmation and the final install summary shows
the selected authority before any installation work starts.

Generated RAG indexes default to the installer-owned
`<state-home>/indexes/<namespace>/rag.sqlite` (default state home
`~/.evidence-first`). The installed shared workspace and both Direct MCP entries
receive that absolute path. A deliberately configured nonstandard external index
remains user-managed. A prior package-relative index is reused only after
query-level readiness checks; a broken or incomplete candidate is not promoted as
the managed index.

For every RAG build, the installer resolves a frozen set of exact existing
`.uproject` descriptors; Standard/Full additionally bind engine evidence to one
selected engine. Project rows use
canonical descriptor root plus descriptor stem as ownership, keeping same-name
clones isolated. Incompatible engine projects are reported/excluded, and the
active descriptor may not be silently excluded. Versioned/custom associations
use manifest-bound sibling index namespaces; no build or query merges projects
across engine shards. Ambiguous legacy provenance fails closed rather than being
assigned to the selected clone.

After installation, select the actual LLM you want to use in LM Studio and leave the
top-level `codex/unreal-context-compactor` switch OFF in the chat's plugin panel.
Install/pin only makes the plugin available; it does not enable it for a chat. Chat
activation is owned by LM Studio, so verify the top-level switch is OFF in every new
or existing chat. The nested `Enable
transparent compaction` setting is also OFF by default and is a separate internal
opt-in. Enable both switches only when deliberately testing compaction for one chat.

On every supported host, `install.py --build-rag` invokes the managed Python
collectors directly. It does not require PowerShell, start Unreal Editor, execute
Editor exporters, copy a project plugin, or mutate a `.uproject`. Engine discovery
uses host-native common locations and accepts `UNREAL_ENGINE_ROOT` or
`--engine-root` for source/custom installs. Unreal project builds use the host
`Build.sh` (with the UBT DLL through `dotnet` as fallback), while Windows keeps its
existing UBT/Build.bat path.

Blueprint node/pin graph coverage therefore remains a manual project choice. With
Unreal Editor closed, the user must copy `tools/ue_plugins/LmStudioGraphExporter`
into the exact project's `Plugins` directory and enable it in that `.uproject`.
The installer has no graph-plugin prompt and never performs this mutation.

PowerShell 7 (`pwsh`) is only for optional, manually invoked `rag.ps1` maintenance:

```text
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope project_source
```

The platform launchers use Python 3.10+ when available. On a Python-free
supported host they first verify pinned uv and establish managed Python 3.12 in
the selected user state-home; no system-wide Python or PATH registration is
performed. Direct `python3 install.py` invocation still requires host Python
3.10+. Node/npm is bootstrapped only for Unreal or context-compactor components.
All pinned runtime archives are SHA-256 verified before extraction. The Linux
runtime baseline is Ubuntu 22.04/24.04 with glibc; musl/Alpine is rejected with
an actionable error.

Cross-platform CI exercises installer, package, Direct MCP, collector, and shard
behavior with controlled fixtures. The recorded physical FULL-install pass is
Apple Silicon with UE 5.8 and documented Editor-export, API-connectivity, and
signing/notarization limitations. Windows also has a prior native RAG/MCP/real-UBT
session, but no clean-machine physical installer-lifecycle proof. Linux has
automation/fixture coverage, not a recorded physical install claim. None of this
claims universal compatibility across hosts, engine builds, projects, plugins, or
Editor runtimes.
