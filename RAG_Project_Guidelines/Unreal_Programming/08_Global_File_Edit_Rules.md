# Global File Edit Rules

## Keywords

agent editing, current file state, current diff, duplicate edit prevention, stale plan prevention, Unreal file edits, header cpp consistency, Build.cs, Target.cs, config, plugin, test files

Korean query aliases: 파일 반복 수정 방지, 이미 수정한 코드 다시 수정하지 않기, 현재 파일 기준, 현재 diff 확인, 모든 파일 편집 규칙, 헤더 cpp 선언 일치, Build.cs 중복 추가 방지

## Purpose

Use this document whenever an agent edits Unreal project files. The rule is global and is not limited to player character files.

The model must treat the current filesystem, current diff, and latest user request as the source of truth. If an earlier plan conflicts with the current files, the current files win.

## Required Edit Discipline

1. Inspect the current target files before editing.
2. Inspect the current diff or previous attempt feedback before editing again.
3. Do not replay an old implementation plan after a file was already changed.
4. Do not duplicate existing includes, UPROPERTY declarations, UFUNCTION declarations, member variables, input bindings, Build.cs dependencies, delegates, timer handles, helper functions, or config keys.
5. If the user asks for the remaining part of a change, edit only the missing part.
6. Return complete final content only for files that actually need a new change.
7. If the current files already satisfy the request, return no file edits and explain the existing evidence.

## Scope

This applies to:

- `.h`, `.hpp`, `.cpp`, `.c`, `.cc`
- `.Build.cs`, `.Target.cs`
- `.ini`, `.json`, `.uproject`, `.uplugin`
- plugin source and descriptors
- tests and fixtures
- generated scratch project files created by the wrapper

## Unreal Consistency Checks

Before finalizing any file bundle:

1. Every `.cpp` member function must have a matching declaration in the relevant header unless it is a constructor, destructor, static local helper, lambda, or non-member function.
2. Every header declaration that needs a `.cpp` definition must either have that definition or be intentionally inline/pure virtual/BlueprintNativeEvent.
3. Every reflected header must keep `*.generated.h` as the last include.
4. Every new reflected type must have the correct macro and `GENERATED_BODY()`.
5. Every new include from another module must have the right Build.cs dependency.
6. Public headers that expose another module's type should use `PublicDependencyModuleNames`; private `.cpp` usage usually belongs in `PrivateDependencyModuleNames`.
