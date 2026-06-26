# Agentic Unreal Edit Operating Protocol

## Keywords

agent_edit, agentic edit, coding agent, Unreal wrapper, current state contract, minimal file bundle, no-op detection, duplicate edit prevention, build feedback loop, static validation, UBT retry, MCP tool, VS Code agent, local 27B model

Korean query aliases: 에이전트화, 코딩 에이전트, 언리얼 자동 수정, 현재 파일 상태 계약, 중복 수정 방지, 무변경 패치 방지, 최소 파일 번들, 빌드 피드백 루프, VS Code 에이전트, 로컬 모델 에이전트

## Purpose

Use this document when a local model is acting as an Unreal C++ editing agent rather than a one-shot answer generator.

The goal is not to make the model more confident. The goal is to keep the model grounded in the current files, make the smallest safe change, and stop instead of replaying stale plans.

## Agent-Edit Context Order

For `agent_edit`, assemble context in this order:

1. Global file edit rules and stop conditions.
2. Current project state summary or project profile.
3. Existing local files related to the request.
4. Target Unreal symbols and declarations.
5. Include owner and module dependency evidence.
6. Static validation or build feedback from the previous attempt.
7. Codegen recipe or compile-fix playbook only after the current-state evidence.

## Current State Contract

The current filesystem and latest diff are authoritative.

If the current files already contain a function, property, include, module dependency, input binding, delegate, timer handle, config key, or plugin descriptor entry, the agent must not add a duplicate.

If an earlier plan says to add something but the current file already contains it, the current file wins.

## Edit Loop

1. Identify the exact files that may need changes.
2. Read the current content of those files.
3. Compare the requested behavior against what already exists.
4. Produce a complete file bundle only for files whose final content changes.
5. Run static Unreal readiness validation.
6. Run UBT when available.
7. If validation or UBT fails, retrieve failure-specific RAG context and apply the smallest repair.
8. If no effective change is needed, return no file edits with evidence.

## Stop Conditions

The agent must stop without new file edits when:

- the requested change is already present;
- the model would only rewrite formatting without changing behavior;
- the next step requires missing project context, asset names, input actions, or plugin availability;
- the previous attempt produced the same final file content;
- the validation feedback asks for a specific missing item and that item is already present.

## Failure Feedback Format

When an attempt fails, give the next attempt:

- exact file path;
- exact validation or build error code;
- whether the failure is header/CPP consistency, reflection, module dependency, API signature, or runtime lifecycle;
- the smallest required repair;
- a reminder not to replay the full original plan.

## Local 27B Model Guardrails

For local 27B-class models:

- prefer lower temperature;
- require JSON file bundles or precise patches;
- include a current project state summary on every attempt;
- reject no-op file bundles that rewrite existing content;
- prioritize wrapper validation over the model's self-assessment;
- do not rely on the model to remember previous edits across VS Code turns.
