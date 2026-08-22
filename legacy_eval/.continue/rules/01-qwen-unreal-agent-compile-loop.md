---
name: Qwen Unreal Agent Compile Loop
alwaysApply: true
description: Stabilize local Qwen/LM Studio Unreal C++ agent work with RAG, minimal edits, and mandatory compile feedback.
---

# Qwen Unreal Agent Compile Loop

- Work as an agent, not as a plain answer generator. Use tools to read files, search the codebase, edit files, and run terminal commands.
- For Unreal C++ or project architecture questions, call `unreal_rag_search` before proposing APIs, includes, reflection macros, Build.cs dependencies, or compile fixes.
- For implementation requests that can be validated in a generated or copied Unreal project, prefer `unreal_generate_compile_loop` so the request goes through RAG, static validation, UnrealBuildTool, and retry feedback.
- Before editing, inspect the current target files and current diff. Treat the filesystem and latest user message as authoritative.
- Make minimal patches only. Do not rewrite an entire file unless the user explicitly asks for a full rewrite.
- Do not add C++ namespaces unless the existing project/API clearly requires one.
- Do not re-create a header after writing a cpp implementation. If a declaration already exists in a header, only add the missing implementation in the cpp.
- Do not duplicate includes, declarations, delegates, timer handles, helper functions, module dependencies, input bindings, or config entries.
- Keep Unreal `*.generated.h` as the last include in a reflected header.
- Do not put Unreal reflected types or `UCLASS`/`USTRUCT`/`UINTERFACE` declarations inside a namespace.
- After every code edit, run the relevant build/test command from the terminal. For Unreal C++, use UnrealBuildTool or the project's existing build task.
- Do not claim the code compiles unless the build command was actually run and its output was checked.
- If the build fails, read the compiler/UHT/linker output, collect the smallest relevant error, search RAG in `compile_fix`, `module_fix`, or `reflection_fix` mode, then patch and rebuild.
- If the build command is unknown, discover it from `.uproject`, `*.Target.cs`, `.vscode/tasks.json`, Rider settings, or README. If it still cannot be discovered, ask for the exact build command instead of finishing.
- RAG context is evidence, not a substitute for compilation. A response is not complete after an implementation edit until validation has been attempted.
- When uncertain about an Unreal API signature or version-specific behavior, say it needs verification instead of presenting compile-ready code.
- Enforce Unreal accuracy gates before writing code: direct base-class include, generated.h last, no reflected namespaces, no undeclared .cpp members, RPC `_Implementation` definitions, `CreateDefaultSubobject` only in constructors, no constructor `SpawnActor`, explicit `NewObject` Outer for retained objects, correct component TimerManager usage, and direct includes for GameplayStatics, ConstructorHelpers, DOREPLIFETIME, TimerManager, and GameplayTags.
