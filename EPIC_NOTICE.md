# Epic Games / Unreal Engine Notice

This repository contains **tooling only** for building a local retrieval index from **your own** licensed Unreal Engine installation.

## What you may index locally

You may run the collect/build scripts against Unreal Engine source, documentation, and project files that you are entitled to use under the [Unreal Engine EULA](https://www.unrealengine.com/eula).

## What must not be redistributed

Do **not** commit, publish, or share:

- Pre-built `rag.sqlite` indexes containing Epic Engine source chunks
- `data/unreal*/raw_source.jsonl` or other exports of Epic proprietary source
- Any Epic-owned content extracted from your engine install

Clonees must run `installer/Configure-Knowledge.ps1` (or equivalent `collect-source` + `build`) on **their** machine with **their** UE install.

## Trademarks

Unreal Engine and Epic Games are trademarks of Epic Games, Inc. This project is not affiliated with or endorsed by Epic Games.
