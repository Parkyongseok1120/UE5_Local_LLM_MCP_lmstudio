# Blueprint, Material, Animation Metadata Workflow

Use editor-exported metadata before planning or editing Blueprint, Material, SkeletalMesh, AnimBlueprint, AnimNotify, AnimMontage, or Sequencer work.

## Required evidence order

1. Retrieve `unreal_blueprint_metadata` for Blueprint parent class, generated class, variables, functions, graph names, node titles, and pin link counts.
2. Retrieve `unreal_material_metadata` for Material parent, expression classes, parameter names, blend mode, shading model, and dependencies.
3. Retrieve animation metadata sources for Skeleton, SkeletalMesh material slots, AnimBlueprint skeleton/graphs, montage sections/slots, notifies, and Sequencer bindings/tracks.
4. Retrieve project C++ symbols only after asset metadata identifies the owning class/component/module.
5. If metadata is missing, request or run the Unreal Editor export path instead of guessing `.uasset` internals.

## Implementation boundaries

- Repository-side code may index, search, and reason about `.uasset` metadata.
- Actual Blueprint node rewiring, Material expression rewiring, AnimBP graph mutation, Montage section edits, Notify edits, and Sequencer track edits must run inside Unreal Editor through Editor Python, Editor Utility, or a dedicated plugin command.
- Never claim a `.uasset` node connection was changed unless an Editor-side command actually loaded, modified, saved, and reported the asset.

## Review checks

- Blueprint work must name the target asset path, generated class, parent class, graph, node title, and affected pins when available.
- Material work must name the parent material, parameter names, expression classes, and dependencies when available.
- Animation work must name the skeleton, skeletal mesh, montage sections, slots, notifies, and sequence bindings when available.
- Sequencer work must name the LevelSequence asset path, binding names, and track names when available.
