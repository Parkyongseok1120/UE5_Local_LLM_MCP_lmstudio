# Internal installer support

Do not start installation from this directory.

The supported user entry points are intentionally limited to:

- Windows: `INSTALL.bat`
- Ubuntu Linux and macOS: `install.sh`
- Any operating system or automation: `install.py`

`install.py` is the only installer implementation. Modules in this directory
bootstrap pinned runtimes and construct the staged Python-only Direct RAG build;
they are support code, not alternative installation choices. Optional Unreal
maintenance and verification wrappers live under `scripts/installer_support/`.

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

After installation, select the actual LLM you want to use in LM Studio and enable
`codex/unreal-context-compactor` in the chat's plugin panel. The plugin compacts the
selected model's history transparently; it is not selected from the model dropdown.
Install/pin makes the plugin available but does not prove that it is enabled for a
specific chat, so confirm the chat-level state in LM Studio.

On every supported host, `install.py --build-rag` invokes the managed Python
collectors directly. It does not require PowerShell, start Unreal Editor, execute
Editor exporters, copy a project plugin, or mutate a `.uproject`. Engine discovery
uses host-native common locations and accepts `UNREAL_ENGINE_ROOT` or
`--engine-root` for source/custom installs. Unreal project builds use the host
`Build.sh` (with the UBT DLL through `dotnet` as fallback), while Windows keeps its
existing UBT/Build.bat path.

PowerShell 7 (`pwsh`) is only for optional, manually invoked `rag.ps1` maintenance:

```text
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope project_source
```

The launcher needs Python 3.10+ to start, then the installer establishes managed
Python 3.12. Node/npm is bootstrapped only for Unreal or context-compactor
components. All pinned runtime archives are SHA-256 verified and safely extracted.
The Linux runtime baseline is Ubuntu 22.04/24.04 with glibc; musl/Alpine is rejected
with an actionable error.
