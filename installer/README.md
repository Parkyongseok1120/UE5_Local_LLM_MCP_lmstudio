# Internal installer support

Do not start installation from this directory.

The supported user entry points are intentionally limited to:

- Windows: `INSTALL.bat`
- Linux and macOS: `install.sh`
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
project search roots. The authority choice is:

1. SAFE (read-only, recommended)
2. AGENT (project writes, commands, and Unreal builds)

AGENT authority requires a second confirmation and the final install summary shows
the selected authority before any installation work starts.

On macOS and Linux, Standard/Full indexing requires `pwsh`. Engine discovery uses
host-native common locations and accepts `UNREAL_ENGINE_ROOT` or `--engine-root`
for source/custom installs. Unreal builds use the host `Build.sh` (with the UBT DLL
through `dotnet` as fallback), while Windows keeps its existing UBT/Build.bat path.
