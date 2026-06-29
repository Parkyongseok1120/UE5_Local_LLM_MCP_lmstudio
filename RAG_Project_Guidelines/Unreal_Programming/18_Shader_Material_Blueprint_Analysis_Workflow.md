# Shader, Material, and Blueprint Analysis Workflow

## Purpose

Use this workflow for Unreal rendering, shader, material graph, material screenshot, and Blueprint graph analysis tasks. The goal is evidence-first analysis, not asset mutation from guesses.

## Shader Context Order

1. Identify whether the request is engine shader knowledge, project shader code, plugin setup, or a compile/runtime rendering error.
2. Retrieve local project `.usf`, `.ush`, `.cpp`, `.h`, `.Build.cs`, `.uplugin`, and plugin files before editing.
3. Retrieve Unreal symbols and module graph evidence for rendering APIs and modules.
4. Only mention module dependencies when evidence points to the file or error. Common rendering module names include `RenderCore`, `RHI`, `Renderer`, `Projects`, and `ShaderCore`, but do not add them blindly.
5. For plugin shaders, verify virtual shader path mapping, plugin loading phase, shader directory layout, and shader registration code before suggesting changes.
6. For RDG/global shader code, verify parameter structs, shader permutation domain, dispatch path, resource lifetime, and render thread boundaries.

## Shader C++ Stability Gate

- Do not edit `.Build.cs` just because shader code exists.
- Do not invent include paths or module ownership. Use symbol lookup, module graph, or the actual compiler error.
- Separate runtime module code from editor-only tooling.
- Treat render-thread work, game-thread UObject access, and asset loading as separate hazards.
- After C++ or shader registration edits, require a build. After `.usf` or `.ush` edits, report that an editor shader recompile or restart may also be required.

## Material Graph Analysis Order

When analyzing a material asset, material instance, or screenshot:

1. List visible or exported material identity: asset path, asset type, parent material, blend mode, shading model.
2. List scalar, vector, texture, and static switch parameters from metadata.
3. List texture assets and defaults when exported.
4. List visible nodes or exported expressions by class/title, then group them by purpose: UVs, masks, color, normal, roughness/metallic/specular, opacity, emissive, world position offset.
5. Mark unknown or unreadable screenshot nodes as `unknown`, not as facts.
6. Compare screenshot claims against `unreal_material_metadata` when available.
7. If metadata is missing, ask for an Editor export or a clearer screenshot instead of guessing.

## Material Screenshot Response Contract

For image-based material analysis, answer in this order:

1. `visible_facts`: nodes/textures/parameters that can be read from the image.
2. `likely_graph_roles`: cautious interpretation of what the graph appears to do.
3. `parameters`: scalar/vector/texture/static switch inventory.
4. `textures`: texture asset names or visible texture slots.
5. `unknowns`: unreadable nodes, cropped wires, missing details.
6. `next_checks`: exact metadata export or project file to inspect.

Do not claim exact values, texture assets, or hidden connections from a screenshot unless they are readable or exported.

## Blueprint Graph Analysis Order

When analyzing Blueprint variables, function calls, or graph screenshots:

1. Retrieve `unreal_blueprint_metadata` for asset path, generated class, parent class, variables, functions, interfaces, graphs, nodes, pins, and dependencies.
2. List graph names and node titles before interpreting behavior.
3. For function calls, cite the node title or `function_reference` when exported.
4. For variables, cite exported variable names or `variable_reference` when exported.
5. Use C++ symbols only to explain callable API contracts; do not assume a Blueprint calls a C++ function without metadata or visible node evidence.
6. If node links or pin defaults are missing, report what needs a re-export or screenshot crop.

## Blueprint Mutation Boundary

Repository-side tools may plan and validate Blueprint changes, but direct `.uasset` graph mutation must run inside Unreal Editor through Editor Python, Editor Utility, or a dedicated plugin command.

Proof levels:

- `Proposed`: plan only, no asset mutation.
- `Applied`: Editor-side command reports save.
- `Verified`: Editor validation, PIE smoke, or build check passed.

Never report Blueprint, Material, or shader asset work as complete unless the proof level is clear.

## Cross-Domain Debugging

For issues involving C++ + shader + material + Blueprint:

1. Start with the failing symptom: compile error, shader compile error, render artifact, missing texture, Blueprint runtime error, or visual mismatch.
2. Retrieve logs first when there is a failure.
3. Retrieve asset metadata second.
4. Retrieve C++ and shader files third.
5. Apply the smallest change that addresses the proven failure.
6. Rebuild or re-export metadata after changes before making further claims.
