# v1.3.0 Roadmap

[English](#english) | [한국어](#korean)

## English

v1.3.0 development is expected to start roughly four months after v1.2.5. Until then, the v1.2.x line is in maintenance mode: small bug fixes, documentation corrections, and low-risk stability patches only.

## v1.2.5 Completed Baseline

v1.3.0 starts from the following delivered baseline; these are not remaining v1.3.0 feature promises:

- The narrow UE 5.8 compile-fix suite reached 36/36 Pass@K and 36/36 Pass@1 with the measured community fine-tuned 27B profile, with zero wrong-file edits, Build.cs false positives, and no-op edits in that run.
- Scoped validate-on-write, static guards, deterministic autofix routes, retry/loop breakers, and UBT revalidation closed the v1.2.x compile-fix stabilization line.
- `unreal-context-compactor` is installable and performs automatic, model-facing history compaction before its hard margin; `write_session_handoff` remains the fallback when compaction cannot recover enough context.
- `build_unreal_project` and `read_unreal_logs` return compact actionable slices with full-log artifacts instead of defaulting to full build output.
- Rider + Cline has an installer path, setup guide, and smoke checklist. It is supported as a tool-backed workflow, not yet as a separately benchmarked frontend.
- Hint-only failure memory can be collected, rejected, and incrementally indexed; it never outranks engine evidence.

## Release Targets

| Version | Target Window | Scope |
|---|---|---|
| v1.2.x maintenance | Until v1.3.0 | Simple bug fixes, documentation corrections, and low-risk stability patches only. |
| v1.3.0 | Development starts about 4 months after v1.2.5 | Evaluation tier separation, advanced Unreal C++ capability suites, Rider + Cline evaluation parity, and Ollama app support. |
| v1.3.1 | After v1.3.0 stabilization | Polish for additional frontends, installer cleanup, docs, and failure-memory improvements. |
| v1.3.2 | Later 1.3.x | Certify the implemented Linux/macOS native paths with platform-specific live Unreal install, indexing, build, and evaluation runs. |

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

### Remaining Gaps After v1.2.5

The completed v1.2.5 transport and safety baseline is recorded above. The rows below name only work that remains after that baseline; they are not regressions in the completed compile-fix suite.

| Gap | v1.2.5 state | Planned work | Target |
|---|---|---|---|
| Long-session context collapse (~60K tokens) | **Delivered for the LM Studio model-facing history:** automatic compaction uses a hard margin, while oversized schemas and saturated KV cache still require a fresh chat/handoff. | Add activation/compaction telemetry and define equivalent compaction behavior for additional frontends. | v1.3.0 |
| UBT / build log token bloat | **Delivered for MCP tool output:** compact responses and full-log artifacts are the default; some wrapper retry paths can still repeat more context than needed. | Apply one compact error-slice contract across wrapper, MCP, and retry loops. | v1.3.0 |
| World-context architecture immaturity (`GEngine`, static command maps) | Detected as warnings/anti-patterns only; models still emit fragile dispatcher designs. | Subsystem-first scaffold templates, negative-control cases for static global registries, optional stricter validation on newly created files, RAG recipes for world-scoped dispatch. | v1.3.0–v1.3.1 |
| `INCLUDE_PATH_NOT_FOUND` on plugin / engine public headers | Validator only indexes project `Source/`; plugin includes can false-positive. | Extend include resolution using module graph + engine/plugin public header index; document known limitations until indexed. | v1.3.1 |
| Autonomous long-running design + implement flows | Strong on single compile-fix turns; weak on multi-hour architecture + multi-file feature work. | N-turn planning contract enforcement, phase gates with progress artifacts, scoped task handoff between sessions, separate eval tier for long-horizon tasks (not merged into compile-fix KPI). | v1.3.1 |
| UE version-pinned API hallucination (real-project compile failures) | **Partially delivered:** denylist, sketch/static warnings, RAG blocklist, component-registration include validation/routing, and hint-only failure-memory collection are present. | Generalize include-before-use checks for project `U*` types and expand the version-pinned engine-symbol allowlist. | v1.3.0 |

These are UX and transport maturity items, not compile-fix regressions. v1.2.x maintenance should not attempt them without a v1.3.0 design pass.

#### Real-project failure patterns to seed v1.3.0 (from v1.2.5 live use)

| Symptom | Wrong pattern | Preferred direction |
|---|---|---|
| Disable character gravity | `MoveComp->DisableGravity()` | `GravityScale = 0.f` or `SetMovementMode(MOVE_Flying)` on `UCharacterMovementComponent` |
| Restart current level | `World->GetURL()` or editor-only `GetEditorWorldContext().MapName` in runtime | `World->GetMapName()` + `UGameplayStatics::OpenLevel` or `World->ServerTravel` (authority-aware) |
| Spawn actor at transform | `SpawnActor<T>(..., &SpawnTransform, ...)` | `SpawnActor<T>(..., SpawnTransform, Params)` — `const FTransform&`, not pointer |
| Use project subsystem type | `GetSubsystem<UCinematicInputLockSubsystem>()` without include or before type exists | `#include` matching header, or remove stub usage until the type is implemented |
| World access from dev console / dispatcher | `GEngine->GetWorld()` | Pass `UWorld*` from owning `UWorldSubsystem` or caller context |

### Rider + Cline Evaluation Parity

The basic Rider + Cline installation, setup documentation, safe/agent-mode boundary, and smoke checklist are complete in v1.2.5. v1.3.0 work is limited to proving frontend parity rather than adding the initial integration.

Planned scope:

- Run a controlled, documented subset of the compile-fix suite through the Rider + Cline path.
- Record any frontend-specific differences in tool calling, context limits, and build/validation evidence.
- Keep Rider/Cline results separate from the LM Studio headline until the same suite and oracle are used consistently.

### Ollama App Support

v1.3.0 should add an Ollama app path as a supported local model frontend, separate from LM Studio.

Planned scope:

- Add configuration docs for Ollama base URL, model name, context length, and JSON/patch discipline.
- Add a preflight check equivalent to LM Studio model preflight.
- Keep model profile names separate from transport frontend names.
- Document known differences: streaming behavior, context limits, tool/MCP availability, and JSON strictness.
- Initial support goal is compile-fix wrapper compatibility, not full MCP chat parity.

### Linux and macOS Certification Target for v1.3.2

The native installer/runtime path now exists: the POSIX launcher uses `python3`, indexing passes that exact interpreter through `pwsh`, engine discovery is host-specific, macOS maps to Unreal's `Mac` platform, and agent builds use host `Build.sh` or `dotnet UnrealBuildTool.dll`. Static, parser, and fixture tests cover those contracts. This is not yet equivalent to live release certification on physical macOS/Linux Unreal installations.

Remaining certification work:

- Run the installer and each indexing tier on physical Linux and macOS hosts.
- Prove native Unreal Editor metadata export and a real UBT build on each host.
- Verify packaged executable permissions, paths containing spaces/non-ASCII text, and localized output on each filesystem.
- Document Linux distribution assumptions and required packages.
- Document macOS prerequisites, Apple Silicon/Intel differences where relevant, and Unreal Engine source/binary installation requirements.
- Keep native claims qualified until the eval harness can run at least a dry-run and small live subset reliably on each platform.

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
- Do not present fixture-tested Linux/macOS paths as live platform certification; keep that proof target in v1.3.2.

## Korean

v1.3.0 개발은 v1.2.5 이후 약 4개월 뒤부터 시작하는 것을 목표로 합니다. 그 전까지 v1.2.x 라인은 maintenance mode입니다. 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch만 예정합니다.

## v1.2.5 완료 기준선

v1.3.0은 아래의 완료된 기준선에서 시작합니다. 이 항목들은 v1.3.0에 남은 기능 약속이 아닙니다.

- 좁은 UE 5.8 compile-fix suite는 측정된 community fine-tuned 27B profile에서 36/36 Pass@K, 36/36 Pass@1을 기록했고, 해당 run의 wrong-file edit, Build.cs false positive, no-op edit은 모두 0이었습니다.
- scoped validate-on-write, static guard, deterministic autofix route, retry/loop breaker, UBT 재검증으로 v1.2.x compile-fix 안정화 라인을 마쳤습니다.
- `unreal-context-compactor`는 설치 가능하며 hard margin 전에 모델에 전달되는 과거 대화를 자동 압축합니다. 충분한 여유를 회복하지 못할 때는 `write_session_handoff`가 대체 경로입니다.
- `build_unreal_project`와 `read_unreal_logs`는 전체 build 출력을 기본 반환하지 않고, 실행 가능한 compact slice와 full-log artifact를 반환합니다.
- Rider + Cline은 설치 경로, setup guide, smoke checklist를 갖춘 tool-backed workflow로 지원합니다. 다만 별도 frontend benchmark로 측정되지는 않았습니다.
- hint-only failure memory는 수집·거절·incremental index가 가능하며 engine evidence보다 우선하지 않습니다.

## Release Targets

| 버전 | 목표 시점 | 범위 |
|---|---|---|
| v1.2.x maintenance | v1.3.0 전까지 | 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch만 진행. |
| v1.3.0 | v1.2.5 이후 약 4개월 뒤 개발 시작 | 평가 tier 분리, advanced Unreal C++ capability suite, Rider + Cline 평가 동등성, Ollama app 지원. |
| v1.3.1 | v1.3.0 안정화 이후 | 추가 frontend polish, installer 정리, docs, failure-memory 개선. |
| v1.3.2 | 이후 1.3.x | 구현된 Linux/macOS native path를 실제 플랫폼의 Unreal 설치·인덱싱·빌드·평가 run으로 인증. |

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

### v1.2.5 완료 후 남은 과제

완료된 v1.2.5 transport·safety 기준선은 위에 기록했습니다. 아래 표는 그 기준선 이후에도 남은 작업만 다루며, 완료된 compile-fix suite의 회귀를 뜻하지 않습니다.

| Gap | v1.2.5 상태 | 계획 작업 | 목표 버전 |
|---|---|---|---|
| 긴 세션 컨텍스트 붕괴 (~60K tokens) | **LM Studio 모델 전달 히스토리에는 완료:** 자동 압축이 hard margin을 사용합니다. 다만 과도하게 큰 schema나 포화된 KV cache는 새 채팅/handoff가 필요합니다. | activation/compaction telemetry를 추가하고 다른 frontend의 동등한 compaction 동작을 정의. | v1.3.0 |
| UBT / build log 토큰 비대 | **MCP tool 응답에는 완료:** compact 응답과 full-log artifact가 기본이며, 일부 wrapper retry 경로는 필요한 것보다 많은 context를 반복할 수 있습니다. | wrapper, MCP, retry loop 전체에 하나의 compact error-slice 계약 적용. | v1.3.0 |
| world-context 아키텍처 미숙 (`GEngine`, static command map) | warning/anti-pattern 탐지만 있고, fragile dispatcher 설계를 모델이 여전히 생성. | subsystem-first scaffold template, static global registry negative-control case, 신규 파일에 대한 선택적 강화 validation, world-scoped dispatch RAG recipe. | v1.3.0–v1.3.1 |
| plugin / engine public header의 `INCLUDE_PATH_NOT_FOUND` | validator가 project `Source/`만 인덱싱해서 plugin include가 false positive 가능. | module graph + engine/plugin public header index로 include resolution 확장, 인덱싱 전까지 known limitation 문서화. | v1.3.1 |
| 자율 장시간 설계 + 구현 흐름 | 단일 compile-fix turn은 강하지만, 수 시간짜리 architecture + multi-file feature 작업은 약함. | N-turn planning contract 강제, phase gate + progress artifact, session 간 scoped task handoff, long-horizon task용 별도 eval tier(compile-fix KPI와 합산 금지). | v1.3.1 |
| UE 버전 고정 API 환각 (실프로젝트 compile 실패) | **부분 완료:** denylist, sketch/static warning, RAG blocklist, component-registration include validation/routing, hint-only failure-memory 수집이 있습니다. | 프로젝트 `U*` 타입 전반으로 include-before-use 검사 일반화, 버전 고정 engine-symbol allowlist 확장. | v1.3.0 |

이 항목들은 compile-fix 회귀가 아니라 UX·transport 성숙도 과제입니다. v1.2.x maintenance에서 v1.3.0 설계 없이 시도하지 않습니다.

#### v1.3.0에 넣을 실프로젝트 실패 패턴 (v1.2.5 live use)

| 증상 | 잘못된 패턴 | 권장 방향 |
|---|---|---|
| 캐릭터 중력 끄기 | `MoveComp->DisableGravity()` | `UCharacterMovementComponent`에서 `GravityScale = 0.f` 또는 `SetMovementMode(MOVE_Flying)` |
| 현재 레벨 재시작 | `World->GetURL()` 또는 runtime에서 `GetEditorWorldContext().MapName` | `World->GetMapName()` + `UGameplayStatics::OpenLevel` 또는 `World->ServerTravel` (authority 고려) |
| Transform 위치 스폰 | `SpawnActor<T>(..., &SpawnTransform, ...)` | `SpawnActor<T>(..., SpawnTransform, Params)` — `const FTransform&`, 포인터 아님 |
| 프로젝트 subsystem 타입 사용 | include 없이 `GetSubsystem<UCinematicInputLockSubsystem>()` | 매칭 `#include` 또는 타입 구현 전까지 stub 제거 |
| dev console / dispatcher에서 world 접근 | `GEngine->GetWorld()` | 소유 `UWorldSubsystem` 또는 caller context에서 `UWorld*` 전달 |

### Rider + Cline 평가 동등성

기본 Rider + Cline 설치, setup 문서, safe/agent-mode 경계, smoke checklist는 v1.2.5에서 완료되었습니다. v1.3.0의 범위는 초기 통합 추가가 아니라 frontend 동등성 검증으로 제한합니다.

계획 범위:

- Rider + Cline 경로로 compile-fix suite의 통제된 문서화 subset 실행.
- tool calling, context limit, build/validation evidence에서 frontend별 차이를 기록.
- 동일 suite와 oracle이 일관되게 쓰일 때까지 Rider/Cline 결과를 LM Studio headline과 분리.

### Ollama App 지원

v1.3.0에서는 LM Studio와 분리된 local model frontend로 Ollama app 경로를 추가할 예정입니다.

계획 범위:

- Ollama base URL, model name, context length, JSON/patch discipline 설정 문서화.
- LM Studio preflight와 동등한 Ollama preflight 추가.
- model profile 이름과 transport frontend 이름을 분리.
- streaming behavior, context limit, tool/MCP availability, JSON strictness 차이 문서화.
- 초기 목표는 compile-fix wrapper 호환성이고, full MCP chat parity는 아님.

### Linux와 macOS 인증은 v1.3.2 목표

native installer/runtime 경로는 구현되어 있습니다. POSIX launcher는 `python3`를 사용하고, indexing은 동일 interpreter를 `pwsh`에 전달하며, engine discovery는 host별로 분리됩니다. macOS는 Unreal `Mac` platform으로 매핑되고 agent build는 host `Build.sh` 또는 `dotnet UnrealBuildTool.dll`을 사용합니다. static/parser/fixture test로 이 계약을 검증했지만, 실제 macOS/Linux Unreal 설치에서의 live release 인증과 동일한 근거는 아닙니다.

남은 인증 작업:

- 실제 Linux/macOS host에서 installer와 각 indexing tier 실행.
- 각 host에서 Unreal Editor metadata export와 실제 UBT build 증명.
- package executable permission, 공백·비ASCII path, localized output을 각 filesystem에서 검증.
- Linux distro 가정과 required package 문서화.
- macOS prerequisite, 해당되는 경우 Apple Silicon/Intel 차이, Unreal Engine source/binary install 요구사항 문서화.
- 각 플랫폼에서 eval harness가 dry-run과 작은 live subset을 안정적으로 실행할 때까지 native 지원 claim을 제한적으로 유지.

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
- fixture test를 통과한 Linux/macOS 경로를 실제 플랫폼 live 인증처럼 표현하지 않음. 해당 증명은 v1.3.2 목표로 유지.
