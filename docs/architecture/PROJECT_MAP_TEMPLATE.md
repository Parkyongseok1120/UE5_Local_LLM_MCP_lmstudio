# Project Map Template

Generated architecture hints. Review before treating as project truth.

## Project

- Name:
- Project file:
- Source root:
- Plugins:

## Modules

| Module | Classification | Build.cs | Public deps | Private deps |
|---|---|---|---|---|
|  |  |  |  |  |

## System / Type Clusters

- Actor / pawn / controller:
- Components:
- Subsystems:
- Widgets:
- Data assets:
- Interfaces:
- Other reflected types:

## Blueprint-Facing Contracts

- UPROPERTY surfaces:
- UFUNCTION surfaces:
- BlueprintNativeEvent / BlueprintImplementableEvent:
- Migration-sensitive names:

## Safe Refactor Zones

- Internal private helper code with no reflected or asset-facing contract.
- Implementation-only changes that preserve reflected names, signatures, and serialized fields.

## Unsafe Refactor Zones

- Reflected UPROPERTY/UFUNCTION names and signatures.
- Serialized asset references such as montages, sequences, textures, and DataAssets.
- Runtime code touching editor-only APIs.
- Missing or uncertain header/cpp pair surfaces.

## Missing / Uncertain Data

- Blueprint graph references:
- Material graph references:
- Asset registry/reference validation:
- Editor/runtime module intent:

## Required Validation For Risky Changes

- UBT compile validation.
- Blueprint reference validation.
- Asset reference validation.
- Editor/runtime boundary review.
