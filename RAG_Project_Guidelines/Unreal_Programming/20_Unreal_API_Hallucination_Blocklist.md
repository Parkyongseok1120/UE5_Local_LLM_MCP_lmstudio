# Unreal API Hallucination Blocklist

## Purpose

Use this checklist when reviewing or generating Unreal Engine answers with small local models. The goal is to stop plausible but unevidenced Unreal API claims before they become code, Blueprint plans, or material/shader instructions.

## Blocked Unless Exact Evidence Exists

Do not claim these are available unless a cited project file, engine symbol, official doc chunk, or exported metadata proves the exact path:

- Arbitrary `GetAttribute("...")` accessors in C++, shaders, or Material Graph.
- Automatic main directional light direction inside ordinary surface materials.
- Reading or rewriting final `SceneColor` from an ordinary surface material.
- `ResolvedView.PreExposure` as a normal Material Graph value.
- `WorldPosition.Z` as camera distance.
- Surface material access to GBuffer, CustomStencil, CustomDepth, or neighbor SceneDepth like a post-process shader.
- Direct `.uasset` Blueprint or Material graph mutation from filesystem write tools.
- Blueprint node execution or pin links inferred only from asset/class/variable names.
- Adding Build.cs module dependencies without include-owner evidence, symbol evidence, or a real compile/link/UHT error.
- Claiming Editor, PIE, shader compile, or UBT success without the corresponding log or tool result.
- Sequencer/MovieScene invented APIs: `bRestoreState` as a public player field, `SetRestoreState(...)`, `SetBindingTag(...)`, `AddBindingOverride(...)`, or treating `AActor::Tags` as a Sequencer binding tag. Restore-on-finish is a sequence/section Completion Mode (Restore State vs Keep State) plus `FMovieSceneSequencePlaybackSettings`; binding overrides use `FMovieSceneObjectBindingID`. Verify the exact symbol before use (see `23_Sequencer_Binding_And_Playback_Playbook.md`).
- Tick ordering guesses: asserting a `TickGroup` value, prerequisite API (`AddTickPrerequisiteActor/Component`), or tick-enable call without confirming the enum/function name (see `24_Tick_Ordering_And_Lifecycle_Contract.md`).

## Rewrite Pattern

When a blocked claim appears, rewrite it into one of these forms:

- `Evidence-backed`: cite the exact file, symbol, metadata export, or log line.
- `Approximation`: explain what can be approximated and how behavior differs.
- `Needs Editor export`: ask for Blueprint/Material metadata or screenshot evidence.
- `Post-process only`: keep the feature in post-process/global shader code.
- `Parameter-driven`: expose the missing runtime value through a Material Parameter Collection, DataAsset, config, or C++ binding.

## Small Model Response Rule

If evidence is missing, say what is missing. Do not fill the gap with a likely Unreal API name.