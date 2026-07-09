# v1.3.0 Roadmap

[English](#english) | [한국어](#korean)

## English

v1.3.0 development is expected to start roughly four months after v1.2.5. Until then, the v1.2.x line is in maintenance mode: small bug fixes, documentation corrections, and low-risk stability patches only.

## Release Targets

| Version | Target Window | Scope |
|---|---|---|
| v1.2.x maintenance | Until v1.3.0 | Simple bug fixes, documentation corrections, and low-risk stability patches only. |
| v1.3.0 | Development starts about 4 months after v1.2.5 | Evaluation tier separation, advanced Unreal C++ capability suites, Rider + Cline support path, and Ollama app support. |
| v1.3.1 | After v1.3.0 stabilization | Polish for additional frontends, installer cleanup, docs, and failure-memory improvements. |
| v1.3.2 | Later 1.3.x | Linux support target, with platform-specific install/test path separated from Windows. |

## Direction

v1.2.5 closes the compile-fix stabilization line with a saved 36-case live Pass@1 36/36 result. v1.3.0 should not inflate that into a broad "agent solved Unreal" claim. Instead, it separates evaluation into independently verifiable tiers.

| Tier | Suite | Purpose | Primary Oracle |
|---|---|---|---|
| A | `compile_fix_live` | Recover broken UE C++ builds | UBT green + static validation green |
| B | `semantic_refactor_live` | Structural edits, migrations, callsite movement | UBT green + semantic oracle |
| C | `runtime_debug_live` | Fix build-green but behavior-red bugs | before test red -> after test green |
| D | `negative_control` | Prevent over-edits, Build.cs false positives, unsafe deletion | no forbidden write |
| F | `cpp_capability_live` | Unreal C++ language, lifetime, GC, thread, reflection skill | UBT + static + forbidden patterns + optional behavior oracle |

## Planned Capability Suites

| Suite | Scope |
|---|---|
| `cpp_expert_live_24` | D4/D5-heavy Unreal C++ correctness suite, no multi-file refactor scoring, strict forbidden-pattern oracle. |
| `cpp_advanced_capability_live_40` | GC/lifetime, reflection, threading/async, delegates/timers, replication, template/reflection boundary, behavior preservation, performance, root-cause selection, editor/runtime boundary. |
| `cpp_runtime_semantic_16` | Runtime/state behavior cases where compile success is not enough. |

### v1.3.0 Work Packages

| Area | Planned Work | Done When |
|---|---|---|
| Evaluation separation | Split reporting into compile-fix, semantic-refactor, runtime-debug, negative-control, and C++ capability scorecards. | README/docs no longer merge all scores into one headline agent score. |
| Advanced C++ cases | Add the first `cpp_expert_live_24` fixtures and oracle metadata. | Each case has forbidden patterns, required reads, target patch surface, and UBT/static validation expectations. |
| Behavior oracles | Add support for runtime/state assertions where compile success is insufficient. | Runtime-debug cases can record before-red/after-green evidence. |
| Static validation | Extend validators for GC/lifetime, async/threading, dynamic delegates, editor-only reflected data, and fake-stub detection. | Fake compile-only fixes are rejected before being counted as pass. |
| Negative control | Add cases where the correct behavior is "do not edit" or "do not touch Build.cs". | Wrong-file edits, Build.cs false positives, and unsafe deletion attempts are first-class metrics. |
| Documentation | Keep model measurement, version history, and roadmap bilingual. | README stays short and links to detailed pages. |

### Rider + Cline Support

v1.3.0 should add a clearer supported path for JetBrains Rider + Cline users.

Planned scope:

- Document Rider project indexing assumptions and recommended UE C++ settings.
- Provide Cline setup notes for using this repository's MCP servers and prompts.
- Add a minimal Rider/Cline smoke checklist: RAG query, project file lookup, static validation, and non-destructive compile request.
- Keep write/build actions behind the same safe-mode / agent-mode distinction used for LM Studio.
- Do not make Rider/Cline support a separate benchmark until the compile-fix suite can be run consistently from that workflow.

### Ollama App Support

v1.3.0 should add an Ollama app path as a supported local model frontend, separate from LM Studio.

Planned scope:

- Add configuration docs for Ollama base URL, model name, context length, and JSON/patch discipline.
- Add a preflight check equivalent to LM Studio model preflight.
- Keep model profile names separate from transport frontend names.
- Document known differences: streaming behavior, context limits, tool/MCP availability, and JSON strictness.
- Initial support goal is compile-fix wrapper compatibility, not full MCP chat parity.

### Linux Support Target for v1.3.2

Linux support is not a v1.3.0 promise. It is a v1.3.2 target.

Expected work:

- Replace Windows-only PowerShell/BAT assumptions with shell equivalents where practical.
- Separate Windows UE/UBT path discovery from Linux path discovery.
- Add Linux-safe path handling and encoding checks.
- Document distro assumptions and required packages.
- Clarify Unreal Engine source/binary install requirements on Linux.
- Keep Linux support opt-in until the eval harness can run at least dry-run and a small live subset reliably.

## Forbidden Fake Fixes

- Adding `UnrealEd` to a runtime `Build.cs` as a default fix.
- `AddToRoot` as a default GC fix.
- `const_cast` to bypass const-correctness.
- `return 0`, `return nullptr`, empty TODO stubs.
- Removing `UFUNCTION`, `UPROPERTY`, `GENERATED_BODY`, or `.generated.h` to hide UHT errors.
- Raw `this` capture in timer, async, or delegate escape paths.
- UObject access from worker threads without game-thread handoff.

## Reporting Rule

v1.3.0 should report field-level scores, not one combined score. Compile-fix, semantic refactor, runtime debug, negative control, and advanced C++ capability must remain separate.

## Non-Goals

- Do not claim general Sonnet/GPT equivalence from local holdout results.
- Do not merge compile-fix, semantic-refactor, runtime-debug, and C++ capability into one headline score.
- Do not treat UBT green as sufficient for advanced C++ capability cases.
- Do not promise Linux support in v1.3.0; keep it as a v1.3.2 target.

## Korean

v1.3.0 개발은 v1.2.5 이후 약 4개월 뒤부터 시작하는 것을 목표로 합니다. 그 전까지 v1.2.x 라인은 maintenance mode입니다. 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch만 예정합니다.

## Release Targets

| 버전 | 목표 시점 | 범위 |
|---|---|---|
| v1.2.x maintenance | v1.3.0 전까지 | 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch만 진행. |
| v1.3.0 | v1.2.5 이후 약 4개월 뒤 개발 시작 | 평가 tier 분리, advanced Unreal C++ capability suite, Rider + Cline 지원 경로, Ollama app 지원. |
| v1.3.1 | v1.3.0 안정화 이후 | 추가 frontend polish, installer 정리, docs, failure-memory 개선. |
| v1.3.2 | 이후 1.3.x | Linux 지원 목표. Windows와 분리된 platform-specific install/test path 구성. |

## 방향

v1.2.5는 compile-fix 안정화 라인을 36-case live Pass@1 36/36 결과로 마무리합니다. 하지만 이것을 "Unreal agent 전체 문제 해결" 주장으로 확장하면 안 됩니다. v1.3.0은 평가를 독립 검증 가능한 tier로 분리합니다.

| Tier | Suite | 목적 | 주요 Oracle |
|---|---|---|---|
| A | `compile_fix_live` | 깨진 UE C++ build 복구 | UBT green + static validation green |
| B | `semantic_refactor_live` | 구조 변경, migration, callsite 이동 | UBT green + semantic oracle |
| C | `runtime_debug_live` | build는 되지만 behavior가 실패하는 bug 수정 | before test red -> after test green |
| D | `negative_control` | over-edit, Build.cs false positive, unsafe deletion 방지 | forbidden write 없음 |
| F | `cpp_capability_live` | Unreal C++ language/lifetime/GC/thread/reflection 역량 | UBT + static + forbidden pattern + 선택적 behavior oracle |

## 계획 중인 Capability Suite

| Suite | 범위 |
|---|---|
| `cpp_expert_live_24` | D4/D5 중심 Unreal C++ correctness suite. multi-file refactor 자체는 점수화하지 않고 strict forbidden-pattern oracle 사용. |
| `cpp_advanced_capability_live_40` | GC/lifetime, reflection, threading/async, delegate/timer, replication, template/reflection boundary, behavior preservation, performance, root-cause selection, editor/runtime boundary. |
| `cpp_runtime_semantic_16` | compile 성공만으로 충분하지 않은 runtime/state behavior case. |

### v1.3.0 작업 패키지

| 영역 | 계획 | 완료 기준 |
|---|---|---|
| 평가 분리 | compile-fix, semantic-refactor, runtime-debug, negative-control, C++ capability scorecard를 분리. | README/docs에서 모든 점수를 하나의 agent score로 합치지 않음. |
| Advanced C++ case | `cpp_expert_live_24` fixture와 oracle metadata 추가. | 각 case에 forbidden pattern, required reads, target patch surface, UBT/static validation 기대값이 있음. |
| Behavior oracle | compile 성공만으로 부족한 runtime/state assertion 지원. | runtime-debug case가 before-red/after-green 근거를 기록할 수 있음. |
| Static validation | GC/lifetime, async/threading, dynamic delegate, editor-only reflected data, fake-stub detection validator 확장. | fake compile-only fix가 pass로 계산되기 전에 reject됨. |
| Negative control | 정답이 "수정하지 않기" 또는 "Build.cs 건드리지 않기"인 case 추가. | wrong-file edit, Build.cs false positive, unsafe deletion attempt가 주요 metric으로 기록됨. |
| 문서 | model measurement, version history, roadmap을 한국어/영어로 유지. | README는 짧게 유지하고 상세 문서로 연결. |

### Rider + Cline 지원 예정

v1.3.0에서는 JetBrains Rider + Cline 사용자를 위한 지원 경로를 더 명확히 추가합니다.

계획 범위:

- Rider project indexing 가정과 권장 UE C++ 설정 문서화.
- 이 저장소의 MCP server와 prompt를 Cline에서 쓰는 setup note 추가.
- 최소 Rider/Cline smoke checklist 추가: RAG query, project file lookup, static validation, non-destructive compile request.
- LM Studio와 동일하게 write/build action은 safe-mode / agent-mode 구분 뒤에 둠.
- Rider/Cline 지원은 compile-fix suite가 해당 workflow에서 일관되게 실행되기 전까지 별도 benchmark로 주장하지 않음.

### Ollama App 지원

v1.3.0에서는 LM Studio와 분리된 local model frontend로 Ollama app 경로를 추가할 예정입니다.

계획 범위:

- Ollama base URL, model name, context length, JSON/patch discipline 설정 문서화.
- LM Studio preflight와 동등한 Ollama preflight 추가.
- model profile 이름과 transport frontend 이름을 분리.
- streaming behavior, context limit, tool/MCP availability, JSON strictness 차이 문서화.
- 초기 목표는 compile-fix wrapper 호환성이고, full MCP chat parity는 아님.

### Linux 지원은 v1.3.2 목표

Linux 지원은 v1.3.0 약속이 아니라 v1.3.2 목표입니다.

예상 작업:

- Windows-only PowerShell/BAT 가정을 가능한 범위에서 shell equivalent로 분리.
- Windows UE/UBT path discovery와 Linux path discovery 분리.
- Linux-safe path handling과 encoding check 추가.
- distro 가정과 required package 문서화.
- Linux에서 Unreal Engine source/binary install 요구사항 명확화.
- eval harness가 dry-run과 작은 live subset을 안정적으로 실행할 때까지 Linux 지원은 opt-in으로 유지.

## 금지해야 하는 Fake Fix

- runtime `Build.cs`에 `UnrealEd`를 기본 해결책처럼 추가.
- GC 문제를 `AddToRoot`로 덮기.
- const-correctness를 `const_cast`로 우회.
- `return 0`, `return nullptr`, 빈 TODO stub.
- UHT error를 숨기기 위해 `UFUNCTION`, `UPROPERTY`, `GENERATED_BODY`, `.generated.h` 제거.
- timer/async/delegate escape path에서 raw `this` capture 유지.
- game-thread handoff 없이 worker thread에서 UObject 접근.

## Reporting Rule

v1.3.0은 하나의 합산 점수로 보고하지 않습니다. compile-fix, semantic refactor, runtime debug, negative control, advanced C++ capability를 분리된 field-level score로 보고해야 합니다.

## Non-Goals

- local holdout 결과만으로 일반적인 Sonnet/GPT 동등성을 주장하지 않음.
- compile-fix, semantic-refactor, runtime-debug, C++ capability를 하나의 headline score로 합치지 않음.
- advanced C++ capability case에서 UBT green만으로 충분하다고 보지 않음.
- Linux 지원을 v1.3.0에 약속하지 않음. v1.3.2 목표로 유지.
