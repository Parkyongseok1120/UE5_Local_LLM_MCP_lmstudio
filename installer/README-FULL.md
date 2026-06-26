# Unreal58-RAG Full Portable Backup

This package is the maximum-completeness, offline-friendly backup of the current Unreal58-RAG workspace.

Unlike the slim portable build, this archive keeps the full workspace tree, the full agent folder, and the full mcp-tools folder intact. It is intended for archival, transfer, and air-gapped use when you want the most faithful snapshot possible.

## Included

- The entire `Unreal58-RAG` workspace, including the sample project folders and `data\unreal58\raw_source.jsonl`
- The full `lmstudio-unreal-agent-mcp` folder
- The full `mcp-tools` folder, including bundled `node_modules`
- A root `INSTALL.bat` launcher that runs `Install-UnrealMcp.ps1` with `-PortableRoot "%~dp0" -SkipNpm -SkipPythonDeps`
- A root `README.txt` copy of this document for quick offline access

## Install

1. Extract the ZIP anywhere you want.
2. Run `INSTALL.bat` from the package root.
3. Restart LM Studio and enable the MCP servers.

## Notes

- Installation does not run `npm install` or `pip install`; the package is intended to stay self-contained during setup.
- The Node.js and Python runtimes are still expected to exist on the target machine if the MCP services or install script need them.
- This build is much larger than the slim portable package because it keeps the generated data and dependency trees instead of pruning them.

## When to use it

Use this build when completeness matters more than size: a full workspace snapshot, sample projects, `raw_source.jsonl`, and bundled dependencies in a single ZIP.
