# Internal installer support

Do not start installation from this directory.

The supported user entry points are intentionally limited to:

- Windows: `INSTALL.bat`
- Ubuntu Linux and macOS: `install.sh`
- Any operating system or automation: `install.py`

`install.py` is the only installer implementation. Internal Unreal maintenance and
verification tools live under `scripts/installer_support/`; they are not alternative
installation choices. This directory intentionally contains only the manifest and
this explanation.

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

On macOS and Ubuntu Linux, opt-in indexing requires PowerShell 7 (`pwsh`). Engine discovery uses
host-native common locations and accepts `UNREAL_ENGINE_ROOT` or `--engine-root`
for source/custom installs. Unreal builds use the host `Build.sh` (with the UBT DLL
through `dotnet` as fallback), while Windows keeps its existing UBT/Build.bat path.

The launcher needs Python 3.10+ to start, then the installer establishes managed
Python 3.12. Node/npm is bootstrapped only for Unreal or context-compactor
components, and `pwsh` only for `--build-rag`. All pinned runtime archives are
SHA-256 verified and safely extracted. The Linux runtime baseline is Ubuntu
22.04/24.04 with glibc; musl/Alpine is rejected with an actionable error.
