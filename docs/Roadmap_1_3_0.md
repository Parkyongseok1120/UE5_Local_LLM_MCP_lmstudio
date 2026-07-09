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
| UE API accuracy (version-pinned) | Models invent members (`DisableGravity`), wrong `UWorld` APIs (`GetURL()`), bad `SpawnActor` signatures (`&FTransform`), and use project types without includes (`UCinematicInputLockSubsystem`). Static rules catch only a subset; most surface at UBT. | Expand `20_Unreal_API_Hallucination_Blocklist.md`, engine-symbol allowlist for configured UE version, static warnings for known-bad patterns, and `cpp_expert_live` fixtures seeded from real project failures. |
| Negative control | Add cases where the correct behavior is "do not edit" or "do not touch Build.cs". | Wrong-file edits, Build.cs false positives, and unsafe deletion attempts are first-class metrics. |
| Documentation | Keep model measurement, version history, and roadmap bilingual. | README stays short and links to detailed pages. |

### Carried Over from v1.2.5 (Known Gaps)

v1.2.5 closed the compile-fix stabilization line with stronger write safety, scoped validate-on-write rollback, loop breakers, and expanded static rules. The items below were identified during that work but intentionally deferred because they need broader session/transport changes, not another hotfix.

| Gap | v1.2.5 state | Planned work | Target |
|---|---|---|---|
| Long-session context collapse (~60K tokens) | Tool-call generation becomes unstable; failed outputs accumulate in context with no recovery path. | Session handoff artifact (summary file + resume checklist), risk-triggered checkpoint prompts, context-budget diagnostics in `get_workspace_info`, and explicit fresh-session recommendation when token use is high. | v1.3.0 |
| UBT / build log token bloat | Full compiler/linker logs are returned inline to the model and burn context quickly. | Structured first-error extraction, tail/summary policy for tool responses, log file path reference instead of full dump, wrapper retry feedback uses compact error slices. | v1.3.0 |
| World-context architecture immaturity (`GEngine`, static command maps) | Detected as warnings/anti-patterns only; models still emit fragile dispatcher designs. | Subsystem-first scaffold templates, negative-control cases for static global registries, optional stricter validation on newly created files, RAG recipes for world-scoped dispatch. | v1.3.0–v1.3.1 |
| `INCLUDE_PATH_NOT_FOUND` on plugin / engine public headers | Validator only indexes project `Source/`; plugin includes can false-positive. | Extend include resolution using module graph + engine/plugin public header index; document known limitations until indexed. | v1.3.1 |
| Autonomous long-running design + implement flows | Strong on single compile-fix turns; weak on multi-hour architecture + multi-file feature work. | N-turn planning contract enforcement, phase gates with progress artifacts, scoped task handoff between sessions, separate eval tier for long-horizon tasks (not merged into compile-fix KPI). | v1.3.1 |
| UE version-pinned API hallucination (real-project compile failures) | Models emit plausible but non-existent APIs and wrong signatures; user must iterate through UBT errors manually. Examples: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform, ...)`, subsystem types without `#include`, editor-only map restart APIs in runtime code. | Pre-build static hints for known-bad symbol patterns, RAG blocklist expansion with correct replacements (`GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, `const FTransform&`), include-before-use rule for project `U*` types, failure-memory capture from real UBT logs. | v1.3.0 |

These are UX and transport maturity items, not compile-fix regressions. v1.2.x maintenance should not attempt them without a v1.3.0 design pass.

#### Real-project failure patterns to seed v1.3.0 (from v1.2.5 live use)

| Symptom | Wrong pattern | Preferred direction |
|---|---|---|
| Disable character gravity | `MoveComp->DisableGravity()` | `GravityScale = 0.f` or `SetMovementMode(MOVE_Flying)` on `UCharacterMovementComponent` |
| Restart current level | `World->GetURL()` or editor-only `GetEditorWorldContext().MapName` in runtime | `World->GetMapName()` + `UGameplayStatics::OpenLevel` or `World->ServerTravel` (authority-aware) |
| Spawn actor at transform | `SpawnActor<T>(..., &SpawnTransform, ...)` | `SpawnActor<T>(..., SpawnTransform, Params)` — `const FTransform&`, not pointer |
| Use project subsystem type | `GetSubsystem<UCinematicInputLockSubsystem>()` without include or before type exists | `#include` matching header, or remove stub usage until the type is implemented |
| World access from dev console / dispatcher | `GEngine->GetWorld()` | Pass `UWorld*` from owning `UWorldSubsystem` or caller context |

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
- Invented UE members on real engine types (`DisableGravity()` on `UCharacterMovementComponent`, `GetURL()` on `UWorld`) without version evidence.
- Wrong `SpawnActor` argument types (e.g. `&FTransform` where `const FTransform&` is required).
- Referencing project `U*` / `A*` / `F*` types without a matching `#include` or generated stub in the module.
- Editor-only world/map APIs (`GetEditorWorldContext`, etc.) in runtime game code paths.

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
| UE API 정확도 (버전 고정) | 모델이 멤버를 지어냄(`DisableGravity`), 잘못된 `UWorld` API(`GetURL()`), 잘못된 `SpawnActor` 시그니처(`&FTransform`), include 없는 프로젝트 타입(`UCinematicInputLockSubsystem`) 사용. static rule은 일부만 잡고 대부분 UBT에서 터짐. | `20_Unreal_API_Hallucination_Blocklist.md` 확장, 설정된 UE 버전 engine-symbol allowlist, known-bad pattern static warning, 실프로젝트 실패 기반 `cpp_expert_live` fixture. |
| Negative control | 정답이 "수정하지 않기" 또는 "Build.cs 건드리지 않기"인 case 추가. | wrong-file edit, Build.cs false positive, unsafe deletion attempt가 주요 metric으로 기록됨. |
| 문서 | model measurement, version history, roadmap을 한국어/영어로 유지. | README는 짧게 유지하고 상세 문서로 연결. |

### v1.2.5에서 이월된 항목 (Known Gaps)

v1.2.5는 write safety, validate-on-write 스코핑, loop breaker, static rule 확장으로 compile-fix 안정화 라인을 마쳤습니다. 아래 항목은 그 과정에서 확인됐지만 hotfix 범위를 넘어 세션/transport 수준 변경이 필요해 의도적으로 미뤄 둔 것입니다.

| Gap | v1.2.5 상태 | 계획 작업 | 목표 버전 |
|---|---|---|---|
| 긴 세션 컨텍스트 붕괴 (~60K tokens) | tool-call 생성이 불안정해지고, 실패 출력이 컨텍스트에 누적되며 복구 경로가 없음. | session handoff artifact(요약 파일 + resume checklist), risk-triggered checkpoint prompt, `get_workspace_info`에 context-budget 진단, 토큰 사용량이 높을 때 fresh session 권고. | v1.3.0 |
| UBT / build log 토큰 비대 | 전체 compiler/linker log가 모델에 inline으로 들어가 컨텍스트를 빠르게 소모. | structured first-error 추출, tool response tail/summary 정책, full dump 대신 log file path 참조, wrapper retry feedback은 compact error slice 사용. | v1.3.0 |
| world-context 아키텍처 미숙 (`GEngine`, static command map) | warning/anti-pattern 탐지만 있고, fragile dispatcher 설계를 모델이 여전히 생성. | subsystem-first scaffold template, static global registry negative-control case, 신규 파일에 대한 선택적 강화 validation, world-scoped dispatch RAG recipe. | v1.3.0–v1.3.1 |
| plugin / engine public header의 `INCLUDE_PATH_NOT_FOUND` | validator가 project `Source/`만 인덱싱해서 plugin include가 false positive 가능. | module graph + engine/plugin public header index로 include resolution 확장, 인덱싱 전까지 known limitation 문서화. | v1.3.1 |
| 자율 장시간 설계 + 구현 흐름 | 단일 compile-fix turn은 강하지만, 수 시간짜리 architecture + multi-file feature 작업은 약함. | N-turn planning contract 강제, phase gate + progress artifact, session 간 scoped task handoff, long-horizon task용 별도 eval tier(compile-fix KPI와 합산 금지). | v1.3.1 |
| UE 버전 고정 API 환각 (실프로젝트 compile 실패) | 그럴듯하지만 없는 API·잘못된 시그니처를 생성하고, 사용자가 UBT 에러를 수동 반복해야 함. 예: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform, ...)`, include 없는 subsystem 타입, runtime에 editor 전용 map restart API. | known-bad symbol pattern 사전 static hint, 올바른 대체안 RAG blocklist(`GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, `const FTransform&`), 프로젝트 `U*` 타입 include-before-use, 실 UBT log failure-memory 수집. | v1.3.0 |

이 항목들은 compile-fix 회귀가 아니라 UX·transport 성숙도 과제입니다. v1.2.x maintenance에서 v1.3.0 설계 없이 시도하지 않습니다.

#### v1.3.0에 넣을 실프로젝트 실패 패턴 (v1.2.5 live use)

| 증상 | 잘못된 패턴 | 권장 방향 |
|---|---|---|
| 캐릭터 중력 끄기 | `MoveComp->DisableGravity()` | `UCharacterMovementComponent`에서 `GravityScale = 0.f` 또는 `SetMovementMode(MOVE_Flying)` |
| 현재 레벨 재시작 | `World->GetURL()` 또는 runtime에서 `GetEditorWorldContext().MapName` | `World->GetMapName()` + `UGameplayStatics::OpenLevel` 또는 `World->ServerTravel` (authority 고려) |
| Transform 위치 스폰 | `SpawnActor<T>(..., &SpawnTransform, ...)` | `SpawnActor<T>(..., SpawnTransform, Params)` — `const FTransform&`, 포인터 아님 |
| 프로젝트 subsystem 타입 사용 | include 없이 `GetSubsystem<UCinematicInputLockSubsystem>()` | 매칭 `#include` 또는 타입 구현 전까지 stub 제거 |
| dev console / dispatcher에서 world 접근 | `GEngine->GetWorld()` | 소유 `UWorldSubsystem` 또는 caller context에서 `UWorld*` 전달 |

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
- 실제 engine 타입에 없는 멤버 지어내기 (`UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()` 등).
- 잘못된 `SpawnActor` 인자 타입 (`&FTransform` 등, `const FTransform&` 필요).
- 프로젝트 `U*` / `A*` / `F*` 타입을 matching `#include` 없이 참조.
- runtime game code path에 editor 전용 world/map API (`GetEditorWorldContext` 등) 사용.

## Reporting Rule

v1.3.0은 하나의 합산 점수로 보고하지 않습니다. compile-fix, semantic refactor, runtime debug, negative control, advanced C++ capability를 분리된 field-level score로 보고해야 합니다.

## Non-Goals

- local holdout 결과만으로 일반적인 Sonnet/GPT 동등성을 주장하지 않음.
- compile-fix, semantic-refactor, runtime-debug, C++ capability를 하나의 headline score로 합치지 않음.
- advanced C++ capability case에서 UBT green만으로 충분하다고 보지 않음.
- Linux 지원을 v1.3.0에 약속하지 않음. v1.3.2 목표로 유지.
