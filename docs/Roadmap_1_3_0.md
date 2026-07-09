# v1.3.0 Roadmap

[English](#english) | [한국어](#korean)

## English

v1.3.0 is expected roughly three months after v1.2.5. Until then, the v1.2.x line is in maintenance mode: small bug fixes, documentation corrections, and low-risk stability patches only.

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

## Korean

v1.3.0은 v1.2.5 이후 약 3개월 뒤 업데이트를 목표로 합니다. 그 전까지 v1.2.x 라인은 maintenance mode입니다. 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch만 예정합니다.

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
