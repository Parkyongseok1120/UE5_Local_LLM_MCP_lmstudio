# Material Graph Porting Workflow

## Purpose

Use this workflow when a user asks to move a post-process shader, plugin shader, `.usf`, or `.ush` rendering path into Unreal Material Graph, Material Functions, Material Layers, Material Instances, or Material Parameter Collections. The goal is not a 1:1 rewrite. The goal is to separate what can run per surface from what only works after the scene has been rendered.

## Post Process To Material Boundary

Classify every effect before proposing a graph structure:

1. `Directly portable`: math that only needs BaseColor, NormalWS, ViewDirWS, LightDirWS, roughness, metallic, AO, vertex color, texture masks, or camera distance.
2. `Portable with approximation`: effects that can be rebuilt with Fresnel, camera distance, per-material masks, MPC parameters, or material instance scalars but will not match screen-space behavior exactly.
3. `Post-process only`: effects that require final SceneColor, GBuffer reads, CustomStencil classification, CustomDepth neighborhood tests, SceneDepth neighborhood tests, ResolvedView internals, or full-screen zone compositing.
4. `Keep hybrid`: effects that should remain in post-process while per-material functions provide authoring-side support.

## Unreal Material Graph Guardrails

- Do not describe a surface material as reading and rewriting final `SceneColor` unless the material domain is explicitly Post Process.
- Do not treat `ResolvedView.PreExposure` as a normal Material Graph input. Avoid PreExposure conversion in surface material designs unless there is engine-level evidence for the exact node or shader path.
- Do not use `WorldPosition.Z` as camera distance. Use distance between `AbsoluteWorldPosition` and `CameraPositionWS`, or a documented engine node that returns camera depth/distance.
- Do not claim a surface material can automatically read the active directional light direction. Prefer a `Material Parameter Collection` value such as `KeyLightDirectionWS` unless project code proves another path.
- Do not claim a surface material can sample GBuffer, CustomStencil, CustomDepth, or neighborhood SceneDepth like a post-process shader. Mark those features as post-process only or approximated.
- Do not invent Unreal APIs such as `GetAttribute("DirectionalLightDirection")`, `SceneColorExposure`, or `Standard Node output SceneColor` without exact engine or project evidence.
- When using Custom nodes, prefer existing project `.ush` helpers intended for Material Graph, such as files named `*MaterialGraphCommon.ush`, before inventing new helper APIs.

## Material Porting Response Contract

Answer in this order:

1. `portable_to_material_functions`: effects and helper functions that can move directly, with required inputs.
2. `approximate_in_material`: effects that can be approximated, including visual differences from the post-process version.
3. `keep_in_post_process`: effects that rely on screen-space buffers, stencil/depth compositing, or final scene color.
4. `recommended_asset_structure`: Master Material, Material Functions, Material Instances, Material Parameter Collection, and optional remaining post-process material/shader.
5. `parameter_mapping`: original shader parameters mapped to MPC, material instance parameters, static switches, vertex colors, or texture masks.
6. `risk_checks`: exact Unreal constraints or project files that should be verified before implementation.

## Recommended Asset Structure

Prefer this structure unless the project already has a stronger pattern:

- `MPC_GlobalStyle`: global light direction, environment grade parameters, haze distances, rim colors, debug toggles.
- `MF_TSE_BaseColorGrade`: desaturation, contrast compression, cool/warm shift.
- `MF_TSE_CelShadow`: normal/light banding using `NormalWS` and `KeyLightDirectionWS`.
- `MF_TSE_Rim`: Fresnel or directional rim using `NormalWS`, `ViewDirWS`, and optional light mask.
- `MF_TSE_HazeApprox`: camera-distance tint/fade approximation.
- `MF_TSE_NormalFlatten`: tangent-space normal flattening by distance.
- `MI_Terrain`, `MI_IndustrialMetal`, `MI_Concrete`, `MI_VFX`: material-class tuning via scalar/static switch parameters.

## Failure Patterns

Flag and correct these phrases when they appear in a porting answer:

- `ResolvedView.PreExposure` as a Material Graph input.
- `WorldPosition.Z` as camera distance.
- `GetAttribute("DirectionalLightDirection")` or automatic main light direction in surface material.
- `SceneColor -> StyleColor -> SceneColor` for ordinary surface materials.
- GBuffer, CustomStencil, or CustomDepth sampling from a surface material without an explicit post-process/material-domain caveat.
