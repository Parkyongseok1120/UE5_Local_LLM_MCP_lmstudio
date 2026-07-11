# Editor vs Runtime Module Recipe

## Rules

- Runtime modules must not include editor-only headers (`UnrealEd.h`, `Kismet2/`, `GEditor`, `FEditorDelegates`).
- Editor-only types belong in `*Editor` modules or `#if WITH_EDITOR` **implementation** files — not public runtime headers.
- Use `WITH_EDITORONLY_DATA` for editor-only UPROPERTY fields; keep `UCLASS`/`UPROPERTY` macros unconditional.

## Validator link

- `EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE`

See `20_Unreal_API_Hallucination_Blocklist.md` for invented editor APIs.
