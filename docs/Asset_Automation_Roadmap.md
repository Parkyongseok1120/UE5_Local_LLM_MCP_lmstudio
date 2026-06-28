# Asset Automation Roadmap

This roadmap covers Blueprint BP, Material node graphs, SkeletalMesh, AnimBlueprint, Notify, Montage, and Sequencer work.

## Principle

Repository-side tools can collect metadata, index relationships, build plans, and verify text/code changes. Direct `.uasset` graph mutation must run inside Unreal Editor.

## Stage 1: Metadata Coverage

Current coverage:

- Blueprint parent/generated class, variables, functions, graph/node/pin summaries
- Material parent, blend/shading fields, expressions, parameter names, dependencies
- SkeletalMesh skeleton, physics asset, material slots
- AnimBlueprint generated class, parent class, skeleton, graph names
- AnimSequence/AnimMontage sequence fields, notifies, sections, slots
- LevelSequence bindings and tracks when exposed by Editor Python

Commands:

```powershell
.\rag.ps1 collect-blueprint-metadata -Question C:\export\bp.jsonl -ProjectName MyGame
.\rag.ps1 collect-material-metadata -Question C:\export\materials.jsonl -ProjectName MyGame
.\rag.ps1 collect-animation-metadata -Question C:\export\animation.jsonl -ProjectName MyGame
.\rag.ps1 build-incremental
```

## Stage 2: Plan Before Mutation

Before changing assets, the agent must retrieve:

- target asset path
- parent/generated class or parent material
- graph name
- node/expression title or class
- pin/parameter names
- skeleton/montage/notify/sequence binding names
- owning C++ symbol or module if relevant

If metadata is missing, run the Editor export again instead of guessing.

## Stage 3: Editor-Side Mutation

Required future command shape:

```text
editor_asset_patch
  assetPath: /Game/...
  assetKind: blueprint|material|anim_blueprint|montage|sequencer
  operation: connect_pin|set_parameter|add_notify|set_montage_section|bind_track
  dryRun: true|false
```

The command must:

- load the asset in Unreal Editor
- validate graph/node/pin existence before editing
- apply the mutation
- save the asset
- emit a before/after summary
- report Editor validation status

## Stage 4: Validation

Proof levels:

- Proposed: plan only, no Editor mutation
- Applied: Editor command reported asset save
- Verified: UBT, PIE smoke, or Editor validation command passed

Do not state that Blueprint/Material/Animation implementation is complete unless the result reaches the Verified level.

## Stage 5: Real Project Eval

Use 20 unseen project failures covering:

- plugin/module conflicts
- circular include and generated header failures
- engine API drift
- Blueprint-only C++ references
- DataAsset/AnimMontage/Material missing links
- Editor/Runtime module mixing
- Config/Input/Enhanced Input issues
- mixed unknown failures

Report Pass@1, Pass@3, final pass, and failure categories.
