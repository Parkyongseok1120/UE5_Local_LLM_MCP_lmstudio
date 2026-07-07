# Unreal Sequencer Binding And Playback Playbook

## Keywords

Sequencer, LevelSequence, ULevelSequencePlayer, ALevelSequenceActor, MovieScene, UMovieSceneSequence, sequence playback, binding, object binding, possessable, spawnable, dynamic binding, binding override, binding tag, actor tag, completion mode, restore state, keep state, camera cut, FMovieSceneObjectBindingID, FMovieSceneSequencePlaybackSettings, PlayFromStart, restore transform, restore after play

Korean query aliases: 시퀀서, 레벨 시퀀스, 시퀀스 재생, 바인딩, 오브젝트 바인딩, 파서서블, 스포너블, 다이나믹 바인딩, 바인딩 오버라이드, 바인딩 태그, 액터 태그, 컴플리션 모드, 상태 복원, 상태 유지, 끝 위치 유지, 재생 종료 후 되돌아감, 카메라 컷

## Purpose

Use this document for Sequencer / LevelSequence / MovieScene questions, especially "compiles but behaves wrong" cases: an actor that snaps back after a sequence, a binding that does not resolve, or a request to "keep the end position." Small local models frequently pick a familiar teleport/patch pattern and invent APIs instead of reasoning about Sequencer state. This playbook forces concept separation and evidence first.

The answer must be **evidence-first**: cite the exact LevelSequence asset, exported binding/track metadata, or engine header before naming an API. If evidence is missing, say so and request the log/export instead of guessing. Keep proof level at `Proposed` for any code sketch (see `21_Edit_Verification_Proof_Levels.md`).

## Concept Boundaries (do not blur these)

These names look similar but are different systems. Confusing them is a top failure mode.

| Concept | What it actually is | Not to be confused with |
|---------|--------------------|--------------------------|
| **Actor Tag** (`AActor::Tags`) | Gameplay-side `FName` tags on an Actor, used by `GetActorsWithTag` etc. | Sequencer binding tags. Setting an Actor Tag does not affect Sequencer resolution. |
| **Sequencer Binding Tag** | Editor-side tag on a MovieScene binding used to find/override bindings by name. | `AActor::Tags`. Edited in the sequence asset, not via a runtime Actor API. |
| **Possessable** | A binding to an Actor that **already exists** in the level. The sequence animates it, then restores it based on its Restore State setting. | Spawnable. |
| **Spawnable** | An object the sequence **creates** for its duration and destroys when done. There is no pre-existing level actor to restore. | Possessable. |
| **Binding Override** | Replacing which object a binding points to at runtime via `FMovieSceneObjectBindingID` and the player's binding-override API. | Dynamic Binding. |
| **Dynamic Binding** | Data-driven binding resolution defined on the sequence/director (evaluated to pick the object). | A one-off runtime override. |
| **Completion Mode / Restore State** | Per-section / per-sequence setting that decides whether animated state is restored (`RestoreState`) or left as-is (`KeepState`) when playback finishes. | A boolean member you set in C++ named `bRestoreState` — that name does not exist as a public playback flag. Verify the real API. |

## "Keep the end position" — correct decomposition

When the user says "시퀀서가 끝난 뒤 위치를 유지하고 싶다 / keep the end position," the real problem is **not** "teleport the actor after play." Reason about, in order:

1. **What is animating the transform?** The LevelSequence transform track on a Possessable binding. Confirm the binding is Possessable (not Spawnable) and which Actor it resolves to.
2. **Restore behavior.** By default a Possessable's animated state may be restored on finish. "Keep end position" usually means the section/sequence should use **Keep State** completion instead of **Restore State**. This is a Sequencer setting, not a manual teleport.
3. **State that must be preserved.** Actor Transform, and if it is a Character: `CharacterMovement` mode (e.g. flying/none during cinematic → walking after), collision, and control rotation. Restoring transform without restoring movement mode/collision produces the classic "floats or falls through floor" bug.
4. **Restore ordering.** If you must restore in C++: stop/finish the sequence → read the final bound transform → re-apply movement mode and collision → set control rotation. Doing transform first and movement mode last (or vice versa) is a common ordering bug.
5. **Unknowns.** Which completion mode is set on the section? Is the binding Spawnable (then there is nothing to "keep" — the object is destroyed)? Confirm from the exported sequencer metadata or the asset in-editor before proposing code.

Prefer the **Sequencer setting (Keep State)** over a C++ teleport patch. Only write C++ restore logic when the setting cannot express the requirement, and then respect the ordering above.

## API accuracy rules

- Do not name a Sequencer API you have not confirmed. `unreal_symbol_lookup` the type/function first, or read the engine header.
- Known invented names to avoid unless proven: `bRestoreState` as a public player field, `SetRestoreState(...)`, `SetBindingTag(...)`, `AddBindingOverride(...)`. Configure restore behavior through the sequence/section settings and `FMovieSceneSequencePlaybackSettings`; do bindings through `FMovieSceneObjectBindingID` and the player's documented binding APIs.
- MovieScene/LevelSequence live in the `MovieScene` and `LevelSequence` modules. If C++ references them, the owning `*.Build.cs` needs those module dependencies — but only add them when a real include/compile error or symbol evidence proves it (see `20_Unreal_API_Hallucination_Blocklist.md`).
- Blueprint/asset facts (which track, which binding) come from Editor export metadata, not from memory. See `17_Blueprint_Material_Animation_Metadata_Workflow.md`.

## Response contract

1. State whether the target binding is Possessable or Spawnable (or UNKNOWN + how to confirm).
2. Separate the concepts you rely on (tag vs binding tag, restore vs keep state).
3. Name only verified APIs; mark the rest UNKNOWN with the header/export needed.
4. Prefer the Sequencer setting; if proposing C++, give the restore ordering explicitly.
5. Proof level stays `Proposed` until built and PIE-verified.
