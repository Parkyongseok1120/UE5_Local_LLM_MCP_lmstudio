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
installer presents one explicit Unreal authority choice:

1. SAFE (read-only, recommended)
2. AGENT (project writes, commands, and Unreal builds)

AGENT authority requires a second confirmation and the final install summary shows
the selected authority before any installation work starts.
