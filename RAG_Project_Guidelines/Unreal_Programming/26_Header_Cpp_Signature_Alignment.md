# Header/Cpp Signature Alignment

## Keywords

signature mismatch, const mismatch, return type mismatch, parameter type mismatch, CPP_RETURN_TYPE_MISMATCH, CPP_FUNCTION_SIGNATURE_MISMATCH, header cpp drift, C2511, C2555

Korean query aliases: 시그니처 불일치, const 불일치, 반환형 불일치, 파라미터 불일치, 헤더 cpp 시그니처, C2511, C2555

## Purpose

Use this document whenever a `.cpp` function definition must match a header declaration exactly, or when a static-validation finding names a signature drift (`CPP_RETURN_TYPE_MISMATCH`, `CPP_FUNCTION_SIGNATURE_MISMATCH`, `CALLBACK_FUNCTION_POINTER_MISMATCH`, `INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH`). A definition and its declaration are not "close enough" — the compiler treats a small mismatch as a completely different, undefined function, which is exactly how `LNK2019` shows up after code that otherwise looks correct.

## What must match exactly

| Element | Rule |
|---|---|
| Return type | Identical, including `const`, pointer/reference, and template arguments. `TArray<AActor*>` and `TArray<const AActor*>` are different types. |
| `const` on the method | A `const` member function declared in the header must be defined `... FunctionName(...) const` in the `.cpp`. Dropping `const` in the definition (or adding it when the header doesn't have it) is a mismatch, not a style choice. |
| Parameter types | Match exactly, including reference (`&`), pointer (`*`), and `const`. `FVector` vs `const FVector&` are different parameters even though both "take a vector." |
| Parameter count | Every declared parameter must appear in the definition. Default arguments live only in the header declaration — repeating `= DefaultValue` in the `.cpp` definition is a compile error, not just redundant. |
| `virtual` / `override` | These keywords belong on the header declaration. Do not repeat `virtual` on the `.cpp` definition; `override` never appears on a definition either. |
| Delegate/callback signatures | A bound `UFUNCTION` handler's parameter list must match the delegate's declared payload exactly (see `DECLARE_DYNAMIC_MULTICAST_DELEGATE_*` arity). A `Broadcast()` call must pass exactly as many arguments as the delegate declares. |

## Multi-file drift pattern

When a header method's signature changes (return type, parameter type, or delegate payload), every one of these must be updated together in the same turn, not just the header:

1. The header declaration itself.
2. The matching `.cpp` definition.
3. Any interface/base-class declaration this method implements or overrides.
4. Every call site that invokes the function or binds it as a delegate handler.

Updating only the header and leaving the `.cpp` (or a call site) on the old signature is the most common cause of `CPP_FUNCTION_SIGNATURE_MISMATCH` / `MULTIFILE_CALLSITE_DRIFT` findings and of `LNK2019` at link time (the linker sees two different symbols, not one changed one).

## Fast self-check before claiming a signature edit is done

1. Copy the header declaration's return type, name, and parameter list character-for-character into the `.cpp` definition, then remove only `virtual`, `override`, and default-argument values — nothing else.
2. If this method implements an interface or overrides a base class, open that declaration too and repeat the same character-for-character comparison.
3. Search the project for every call site and delegate binding of this function name; update any that still pass the old parameter list or count.
4. Re-run static validation before claiming the signature is aligned — see `25_LNK2019_Missing_Definition_Recipe.md` for the matching missing-definition case.

## Response contract

1. Quote the exact header declaration and the exact `.cpp` definition side by side when reporting or fixing a mismatch.
2. Name every file that needs to change, not just the one with the finding.
3. State the proof level per `21_Edit_Verification_Proof_Levels.md` — a signature edit is only `Patched` until static validation or UBT raises it to `StaticChecked` / `Built`.
