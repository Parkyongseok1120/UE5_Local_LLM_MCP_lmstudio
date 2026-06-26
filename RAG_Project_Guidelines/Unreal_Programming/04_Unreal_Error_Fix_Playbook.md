# Unreal Error Fix Playbook

## Keywords

Unreal compile fix, error detection, C1083, LNK2019, LNK2001, generated.h error, UHT error, UnrealHeaderTool failed, unrecognized type, Unable to find module, Build.cs missing dependency, unresolved external symbol, API macro, MinimalAPI, duplicate symbol, Live Coding stale build

Korean query aliases: 컴파일 에러 수정, 빌드 실패, generated.h 오류, UHT 타입 인식 실패, Build.cs 누락, 모듈 dependency 누락, include 오류, LNK2019, unresolved external, 링크 에러, 에러 검출

## Purpose

Use this document when a user pastes an Unreal build error or asks for compile-fix advice. The model should classify the error, retrieve the related symbol/include/module/build-log chunks, then propose the smallest fix and a rebuild command.

## Error Route Table

| Error signal | Search mode | First evidence to retrieve |
|---|---|---|
| `C1083`, `Cannot open include file` | `module_fix` | include map, Build.cs module symbol, file path |
| `LNK2019`, `LNK2001`, unresolved external | `compile_fix` | function declaration/definition symbol, module symbol, build log |
| `*.generated.h` cannot be opened | `reflection_fix` | header include order, generated header path, UCLASS/USTRUCT symbol |
| UHT unrecognized type | `reflection_fix` | reflected type symbol, include, module dependency |
| `Unable to find module` | `module_fix` | `.uproject`, `.uplugin`, Target.cs, Build.cs |
| Runtime crash, assert, ensure | `runtime_debug` | editor log, callstack, owning UObject symbol |

## Playbook: C1083 Cannot Open Include File

Likely causes:

- Include path is wrong.
- The header belongs to a module not listed in Build.cs.
- A public header includes a type from a module listed only in `PrivateDependencyModuleNames`.
- The file is generated or plugin-provided and the plugin/module is disabled.

Fix sequence:

1. Identify the missing header and the source file that included it.
2. Search the symbol/include map for the header owner module.
3. If the include is in a public header, add the owner module to `PublicDependencyModuleNames`.
4. If the include is only in `.cpp`, add it to `PrivateDependencyModuleNames`.
5. Rebuild with UnrealBuildTool and re-index new logs if it fails again.

## Playbook: LNK2019 Unresolved External Symbol

Likely causes:

- Function declared in `.h` but missing `.cpp` definition.
- Definition signature differs from declaration.
- Static function/member variable declared but not defined.
- Required module is missing from Build.cs.
- API macro/export is missing across module boundary.
- The source file is not inside a compiled module folder.

Fix sequence:

1. Extract the unresolved symbol and demangle mentally to the class/function.
2. Search for `symbol_name` in collected Unreal/project symbols.
3. Verify declaration and definition both exist and signatures match exactly.
4. Verify the defining module is a dependency of the consuming module.
5. For cross-module public classes, verify the class uses `<MODULE_API>` unless intentionally `MinimalAPI`.

## Playbook: generated.h Error

Likely causes:

- `"MyType.generated.h"` is not the last include in the header.
- The file name and generated include name do not match.
- The header has a syntax error before `GENERATED_BODY`.
- UHT could not parse an included reflected type.
- Intermediate files are stale after file moves or class renames.

Fix sequence:

1. Open the header named in the error.
2. Confirm the generated header is the final include.
3. Confirm the class/struct/interface has the matching `UCLASS`, `USTRUCT`, `UINTERFACE`, or `UENUM` macro.
4. Fix earlier syntax errors before changing generated output.
5. If files were renamed, regenerate project files or clean `Intermediate` only when the user approves destructive cleanup.

## Playbook: UHT Type Recognition Failure

Likely causes:

- UPROPERTY uses a USTRUCT by value but the struct definition is not included.
- UFUNCTION parameter uses a reflected type UHT cannot see.
- A template or container contains a type UHT cannot reflect.
- The owner module does not depend on the module that defines the type.
- A forward declaration is used where UHT needs the full reflected definition.

Fix sequence:

1. Find the property/function line UHT complains about.
2. Check whether the type is UCLASS, USTRUCT, UENUM, or plain C++.
3. Include the type definition in the header if UHT needs it.
4. Add the defining module to Build.cs.
5. Replace unsupported reflected signatures with supported wrappers or USTRUCT types.

## Playbook: Build.cs Missing Dependency

Likely causes:

- The code includes a header from another module.
- The header exposes that type publicly.
- The dependency was added to the wrong Public/Private list.
- Plugin module is not enabled in `.uproject` or `.uplugin`.

Fix sequence:

1. Identify the module that owns the header or symbol.
2. Public header use means public dependency.
3. Private `.cpp` only use means private dependency.
4. Editor-only dependencies must stay in Editor modules.
5. Rebuild and inspect the next error rather than adding broad module lists.

## Playbook: Live Coding Or Stale Intermediate Confusion

Likely causes:

- Live Coding kept an old object file.
- Class/file rename left stale generated files.
- Editor is running while module layout changed.

Fix sequence:

1. Prefer closing the Editor for module, reflection, or generated file changes.
2. Run a full UBT build.
3. Only clean `Binaries`, `Intermediate`, or generated files when the user explicitly approves cleanup.

