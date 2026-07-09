# LNK2019 Missing Definition Recipe

## Keywords

LNK2019, unresolved external symbol, CPP_DEFINITION_MISSING, declared but not defined, missing .cpp stub, header cpp mismatch, link error, linker error

Korean query aliases: LNK2019, 정의 없음, 선언만 있고 정의 없음, 링커 에러, 헤더에만 있고 cpp에 없음, unresolved external symbol

## Purpose

Use this document when a header declares a method (or the model is about to add one) and there is no matching `.cpp` definition — the exact situation the static validator's `CPP_DEFINITION_MISSING` finding and UBT's `LNK2019: unresolved external symbol` both report. This is one of the most common local-model mistakes: writing a plausible header and stopping before the `.cpp` half exists.

## The fix is always the same shape

1. Read the header declaration exactly as written: return type, `const`, reference/pointer qualifiers, default arguments (drop defaults in the definition), and any `override`/`virtual` keyword (drop `virtual`/`override` in the definition; `const` and reference qualifiers must match exactly — see `26_Header_Cpp_Signature_Alignment.md`).
2. Add `ClassName::FunctionName(...)` to the matching `.cpp` file — the one already paired with that header (`Private/<Same>.cpp`), not a new file.
3. Write a real body, not a stub that silently does nothing, unless the user explicitly asked for a stub. A `(void)Param;` line to silence an unused-parameter warning is fine; an empty body that discards required behavior is not.
4. Do **not** touch `Build.cs` to "fix" a missing definition. `LNK2019` for a symbol that should exist in the same module is a missing `.cpp` definition, not a missing module dependency. Editing `Build.cs` first is a wrong first move that wastes a turn and does not address the actual error.

## Exceptions: definitions that are expected to be missing from a plain grep

These do **not** need a hand-written `ClassName::FunctionName` definition, because something else generates or calls them:

| Declaration | What actually defines/calls it |
|---|---|
| Constructor / destructor | Compiler-generated unless declared and customized; if declared, still needs a body |
| `UFUNCTION(Server)` / `UFUNCTION(Client)` / `UFUNCTION(NetMulticast)` | UHT generates the RPC dispatch; you define `FunctionName_Implementation`, not `FunctionName` |
| `BlueprintNativeEvent` | UHT generates the dispatch; you define `FunctionName_Implementation`, not `FunctionName` |
| `BlueprintImplementableEvent` | UHT generates the full implementation; do **not** define anything, not even `_Implementation` |
| Pure virtual (`= 0`) | Only concrete derived classes need a definition; the pure-virtual declaration itself does not |

If a finding or link error names one of these patterns, check which exception applies before writing a definition — see `02_Codegen_Recipes_Core_Types.md` for the BlueprintNativeEvent vs BlueprintImplementableEvent decision table.

## Multi-turn header-then-cpp flow

Writing a header with a new method declaration and the matching `.cpp` definition in two separate tool calls is normal and expected — it is not itself an error. `CPP_DEFINITION_MISSING` found on the header you just wrote is treated as a deferred/advisory finding at write time (not a rollback trigger) precisely so this flow works; it becomes a real problem only if the `.cpp` half is never written before the turn ends. See `has_blocking_write_errors` in `scripts/unreal_static_validate.py` for the exact scoping rule.

## Response contract

1. Name the exact class and function that needs a definition.
2. Give the full `.cpp` definition with a matching signature (return type, `const`, qualifiers).
3. State the target file: the existing `.cpp` paired with the header, not a new file.
4. Do not propose a `Build.cs` change unless the error is actually a missing-module symbol (a type from another module, not a same-module method).
