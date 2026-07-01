# Edit Verification Proof Levels

## Purpose

Use this workflow for C++, Blueprint, Material, shader, config, and asset-facing work. It keeps answers precise about what was proposed, patched, built, and actually verified in Unreal Editor or PIE.

## Proof Levels

Report the highest proof level reached:

- `Proposed`: plan only; no file or asset mutation.
- `Patched`: text files were changed, or an Editor-side command reported an attempted asset change.
- `StaticChecked`: syntax/static validators passed, but Unreal Build Tool or Editor did not run.
- `Built`: Unreal Build Tool completed successfully for the target.
- `ShaderCompiled`: shader compile or editor shader recompile completed without reported errors.
- `EditorVerified`: Unreal Editor loaded or saved the relevant asset and metadata was re-exported or validated.
- `PIEVerified`: PIE/runtime smoke test or relevant log check passed.

## Required Wording

- `Edited` does not mean `Built`.
- `Built` does not mean runtime behavior is correct.
- `Runtime log clean` does not mean gameplay design is correct.
- `Metadata exported` does not mean graph behavior is correct unless node links/pins are present.
- `.uasset` work is only `Applied` when an Editor-side command reports save.
- `.uasset` work is only `Verified` when Editor validation, metadata re-export, or PIE/runtime evidence confirms it.

## Asset Mutation Boundary

Filesystem tools may edit C++, config, text assets, scripts, and shader text files. Blueprint and Material graph mutation must run inside Unreal Editor through Editor Python, Editor Utility, commandlet, or a dedicated plugin command. Repository-side tools may plan asset changes, but must not report them as applied without Editor-side evidence.

## Response Contract

When answering after edits or verification, include:

1. `proof_level`: the highest level reached.
2. `changed_surface`: files/assets touched or planned.
3. `verification`: exact build, shader compile, editor export, PIE, or log evidence.
4. `remaining_risk`: what has not been checked.
5. `next_check`: the smallest command/export/test that would raise the proof level.