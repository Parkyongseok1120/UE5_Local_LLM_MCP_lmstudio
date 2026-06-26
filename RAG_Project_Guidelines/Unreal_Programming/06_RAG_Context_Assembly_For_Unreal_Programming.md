# RAG Context Assembly For Unreal Programming

## Keywords

27B model, local LLM, Unreal programming assistant, agent_edit context, codegen context, compile_fix context, runtime_debug context, api_lookup context, module_fix context, reflection_fix context, retrieval order, build feedback loop, UBT loop, UHT loop, top-5 regression

Korean query aliases: 27B 모델 언리얼 프로그래밍, 코드 생성 컨텍스트 조립, 에러 수정 컨텍스트, 디버깅 컨텍스트, 조언용 RAG, 빌드 피드백 루프, top-5 회귀 테스트

## Purpose

Use this document to decide what evidence should be placed into a prompt for a 27B-class local model. The goal is not to give the model more text. The goal is to give it the smallest set of Unreal-specific facts that prevent stale edits, compile errors, and bad debugging guesses.

## Agent-Edit Context Order

For `agent_edit`, assemble context in this order:

1. Global file edit rules and stop conditions.
2. Current project state summary or project profile.
3. Existing local files related to the request.
4. Target Unreal symbols and declarations.
5. Include owner and module dependency evidence.
6. Static validation or build feedback from the previous attempt.
7. Codegen recipe or compile-fix playbook.

Prompt instruction:

- Tell the model that current files and latest diff are authoritative.
- Tell the model not to replay stale plans or duplicate existing declarations.
- Ask for a complete file bundle only for files whose final content changes.
- Accept an empty file bundle only when the request is already satisfied, and require evidence in the answer.
- If validation feedback is specific, apply the smallest repair instead of restarting the whole implementation.

## Codegen Context Order

For `codegen`, assemble context in this order:

1. The target Unreal type symbol: class, struct, interface, enum, function, property.
2. Required include path.
3. Owning module and Build.cs dependency.
4. A local project example with similar style if a project exists.
5. A codegen recipe from `Unreal_Programming`.
6. A related error playbook for the most likely failure.

Prompt instruction:

- Ask the model to output files changed, code blocks, required Build.cs modules, and validation steps.
- Ask the model to avoid inventing APIs when the retrieved context does not show them.
- Ask the model to mark uncertain project-specific names as placeholders.

## Compile-Fix Context Order

For `compile_fix`, assemble context in this order:

1. Exact error record from collected build logs.
2. `error_code`, `error_file`, and nearby log message.
3. Matching symbol declaration and definition.
4. Build.cs module symbol for the consuming module.
5. Error fix playbook.

Prompt instruction:

- Classify the error first.
- Give the smallest likely fix.
- List which file to inspect next if the fix is uncertain.
- Do not suggest deleting generated folders unless the user approves cleanup.

## Module-Fix Context Order

For `module_fix`, assemble context in this order:

1. Missing include or missing type.
2. Include map record.
3. Owner module from Build.cs or engine source.
4. Current project module Build.cs.
5. Public vs Private dependency rule.

Decision rule:

- Public header uses the type: public dependency.
- Private `.cpp` uses the type: private dependency.
- Editor-only type: Editor module only.

## Reflection-Fix Context Order

For `reflection_fix`, assemble context in this order:

1. UHT or generated.h error line.
2. Header containing `UCLASS`, `USTRUCT`, `UINTERFACE`, `UENUM`, `UFUNCTION`, or `UPROPERTY`.
3. Include order and generated header location.
4. Reflected type definitions used by UPROPERTY/UFUNCTION.
5. Build.cs modules for those reflected types.

Decision rule:

- UHT needs full reflected definitions more often than the C++ compiler does.
- A forward declaration may compile for C++ but still fail for UHT.

## Runtime-Debug Context Order

For `runtime_debug`, assemble context in this order:

1. Fatal/assert/ensure line.
2. Project callstack frame.
3. Actor/component/UObject lifecycle function.
4. Ownership and garbage collection evidence.
5. Runtime debugging playbook.

Prompt instruction:

- The model should explain what evidence supports the suspected cause.
- The model should propose logging or breakpoint checks before large refactors.

## API-Lookup Context Order

For `api_lookup`, assemble context in this order:

1. Exact symbol record.
2. Function signature or class declaration.
3. Include path.
4. Module dependency.
5. A short local usage example if present.

Prompt instruction:

- If the retrieved source does not prove an API exists, the model should say it needs confirmation.
- Prefer direct symbol/include/module evidence over memory.

## Feedback Loop

Recommended loop:

1. Retrieve with `codegen`, `api_lookup`, or a fix mode.
2. Generate a minimal patch.
3. Run UnrealBuildTool.
4. Collect build logs with `collect-build-logs`.
5. Rebuild the RAG index.
6. Retrieve with `compile_fix`, `module_fix`, or `reflection_fix`.
7. Apply the smallest fix and repeat.

This loop is more important for 27B models than adding a larger prompt. A smaller model can perform well when the retrieval layer consistently puts the correct symbol, include, module, and error playbook in the top results.
