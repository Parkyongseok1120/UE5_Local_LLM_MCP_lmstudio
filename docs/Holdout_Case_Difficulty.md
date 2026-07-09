# 36-Case Live Holdout Difficulty

[English](#english) | [한국어](#korean)

## English

This difficulty scale is local to this repository's UE 5.8 RAG/MCP/UBT holdout. It is not a general SWE-bench or model-intelligence difficulty scale.

| Level | Meaning |
|---|---|
| D1 | Deterministic or nearly deterministic one-surface fix. |
| D2 | Simple Unreal-specific fix with clear target, but easy to break with wrong include/module choice. |
| D3 | Requires reading the right owner file and avoiding common fake fixes or wrong-surface edits. |
| D4 | Multi-surface or semantic compile fix; declaration/definition/callsite or lifecycle reasoning matters. |
| D5 | Highest-risk holdout case; small mistakes create corruption, no-op loops, wrong Build.cs edits, or broad refactors. |

## Case Table

| Case ID | Tier | Difficulty | Why |
|---|---|---:|---|
| `local_gameplaytags_missing_module` | module_fix | D2 | Clear missing module dependency; must patch owner `Build.cs`. |
| `local_enhanced_input_missing_module` | module_fix | D2 | Clear dependency fix, but wrong gameplay rewrite is forbidden. |
| `local_generated_h_not_last` | uht_reflection | D1 | Deterministic `.generated.h` include ordering fix. |
| `local_header_cpp_signature_mismatch` | single_file_compile_fix | D3 | Must align declaration/definition without Build.cs guessing. |
| `local_lnk2019_missing_cpp_definition` | single_file_compile_fix | D4 | Must add missing implementation while preserving existing callsites; compact models can fail JSON/patch discipline here. |
| `local_umg_missing_module` | module_fix | D2 | Clear owner module dependency for UI type. |
| `local_niagara_missing_module` | module_fix | D2 | Clear plugin/runtime module dependency. |
| `local_aimodule_missing_module` | module_fix | D3 | Module owner evidence matters; common wrong fix is unrelated AI behavior rewrite. |
| `local_navigation_system_missing_module` | module_fix | D3 | Correct `Build.cs` patch can be blocked by static false positives if validator is weak. |
| `local_levelsequence_missing_module` | module_fix | D3 | Module dependency can require public/private surface reasoning. |
| `local_blueprint_native_event_missing_implementation` | uht_reflection | D2 | Needs UE `BlueprintNativeEvent` `_Implementation` convention. |
| `local_editor_only_runtime_boundary` | module_fix | D3 | Must remove runtime editor dependency, not add `UnrealEd`. |
| `local_include_path_wrong_owner` | single_file_compile_fix | D2 | Include owner path correction; avoid Build.cs edits. |
| `local_blueprint_implementable_event_native_impl` | uht_reflection | D2 | Must not implement `BlueprintImplementableEvent` as native `_Implementation`. |
| `local_delegate_broadcast_signature_mismatch` | single_file_compile_fix | D3 | Must patch exact delegate broadcast callsite. |
| `local_component_registration_missing_include` | single_file_compile_fix | D3 | Needs concrete component include, not broad actor rewrite. |
| `local_game_instance_subsystem_missing_include` | single_file_compile_fix | D2 | Deterministic subsystem include repair. |
| `local_plugin_projects_missing_module` | module_fix | D2 | Clear `Projects` module dependency. |
| `local_uobject_lifecycle_missing_include` | single_file_compile_fix | D3 | Must remove bad `NewObject` macro and include `UObjectGlobals`; avoid exact-oldText no-op loops. |
| `local_multifile_method_rename_header_cpp_callsite` | multifile_refactor | D4 | Declaration, definition, and consumer callsite must move together. |
| `local_multifile_delegate_signature_update` | multifile_refactor | D4 | Delegate declaration and receiver surfaces must stay synchronized. |
| `local_multifile_interface_signature_update` | multifile_refactor | D4 | Interface and implementer signatures must align across files. |
| `local_multifile_component_api_move` | multifile_refactor | D4 | API owner changes require consumer update without broad rewrite. |
| `local_common_const_signature_mismatch` | single_file_compile_fix | D3 | Small const/signature mismatch; exact patch discipline matters. |
| `local_multifile_delegate_param_type_change` | multifile_refactor | D4 | Parameter type drift across delegate declaration and receiver. |
| `local_multifile_interface_return_type_change` | multifile_refactor | D4 | Return type drift across interface and implementation. |
| `local_multifile_subsystem_api_move` | multifile_refactor | D4 | Moved subsystem API requires coordinated producer/consumer update. |
| `local_multifile_uproperty_type_migration` | multifile_refactor | D4 | UPROPERTY type migration must preserve reflected API and cpp behavior. |
| `local_multifile_event_binding_signature_change` | multifile_refactor | D4 | Binding signature and handler declaration/definition must match. |
| `local_multifile_base_class_method_override_change` | multifile_refactor | D4 | Override signature must match base class contract. |
| `local_multifile_callback_param_expand` | multifile_refactor | D5 | High-risk callback typedef/handler/registration drift; previous greedy regex corruption happened here. |
| `local_multifile_method_split_callsite_update` | multifile_refactor | D4 | Method split requires updating consumers without losing behavior. |
| `local_reflection_blueprint_event_rename` | uht_reflection | D3 | Blueprint event rename must preserve UE reflection conventions. |
| `local_editor_runtime_guard_boundary` | editor_runtime_boundary | D4 | Must remove editor-only include and `GEditor` linkage without `UnrealEd` Build.cs. |
| `local_module_private_vs_public_dependency` | module_fix | D4 | Requires public/private module dependency reasoning. |
| `local_include_owner_forward_decl_mixup` | single_file_compile_fix | D3 | Must choose include vs forward declaration correctly for UE type usage. |

## Korean

이 난이도는 이 저장소의 UE 5.8 RAG/MCP/UBT holdout 기준입니다. 일반 SWE-bench 또는 모델 지능 난이도 척도가 아닙니다.

| 난이도 | 의미 |
|---|---|
| D1 | deterministic 또는 거의 deterministic한 단일 표면 수정. |
| D2 | 목표가 명확한 간단한 Unreal-specific 수정. 단, include/module 선택을 틀리면 실패할 수 있음. |
| D3 | 올바른 owner file을 읽고 fake fix 또는 wrong-surface edit를 피해야 함. |
| D4 | multi-surface 또는 semantic compile fix. declaration/definition/callsite 또는 lifecycle reasoning이 중요함. |
| D5 | 가장 위험한 holdout case. 작은 실수가 corruption, no-op loop, wrong Build.cs edit, broad refactor로 이어질 수 있음. |

## 케이스 표

| Case ID | Tier | 난이도 | 이유 |
|---|---|---:|---|
| `local_gameplaytags_missing_module` | module_fix | D2 | 명확한 missing module dependency; owner `Build.cs` 수정 필요. |
| `local_enhanced_input_missing_module` | module_fix | D2 | 명확한 dependency fix지만 unrelated gameplay rewrite 금지. |
| `local_generated_h_not_last` | uht_reflection | D1 | deterministic `.generated.h` include ordering fix. |
| `local_header_cpp_signature_mismatch` | single_file_compile_fix | D3 | Build.cs 추측 없이 declaration/definition 정렬 필요. |
| `local_lnk2019_missing_cpp_definition` | single_file_compile_fix | D4 | 기존 callsite를 보존하면서 missing implementation 추가 필요; compact model이 JSON/patch discipline에서 흔들릴 수 있음. |
| `local_umg_missing_module` | module_fix | D2 | UI type에 대한 명확한 owner module dependency. |
| `local_niagara_missing_module` | module_fix | D2 | 명확한 plugin/runtime module dependency. |
| `local_aimodule_missing_module` | module_fix | D3 | module owner evidence가 중요; unrelated AI behavior rewrite가 흔한 오답. |
| `local_navigation_system_missing_module` | module_fix | D3 | 정답 `Build.cs` patch가 약한 validator의 static false positive에 막힐 수 있음. |
| `local_levelsequence_missing_module` | module_fix | D3 | public/private surface reasoning이 필요할 수 있는 module dependency. |
| `local_blueprint_native_event_missing_implementation` | uht_reflection | D2 | UE `BlueprintNativeEvent` `_Implementation` convention 필요. |
| `local_editor_only_runtime_boundary` | module_fix | D3 | runtime editor dependency를 제거해야 하며 `UnrealEd` 추가는 금지. |
| `local_include_path_wrong_owner` | single_file_compile_fix | D2 | include owner path 수정; Build.cs edit 회피 필요. |
| `local_blueprint_implementable_event_native_impl` | uht_reflection | D2 | `BlueprintImplementableEvent`를 native `_Implementation`으로 구현하면 안 됨. |
| `local_delegate_broadcast_signature_mismatch` | single_file_compile_fix | D3 | 정확한 delegate broadcast callsite patch 필요. |
| `local_component_registration_missing_include` | single_file_compile_fix | D3 | concrete component include 필요; broad actor rewrite 금지. |
| `local_game_instance_subsystem_missing_include` | single_file_compile_fix | D2 | deterministic subsystem include repair. |
| `local_plugin_projects_missing_module` | module_fix | D2 | 명확한 `Projects` module dependency. |
| `local_uobject_lifecycle_missing_include` | single_file_compile_fix | D3 | 잘못된 `NewObject` macro 제거와 `UObjectGlobals` include 필요; exact-oldText no-op loop 회피 필요. |
| `local_multifile_method_rename_header_cpp_callsite` | multifile_refactor | D4 | declaration, definition, consumer callsite를 함께 이동해야 함. |
| `local_multifile_delegate_signature_update` | multifile_refactor | D4 | delegate declaration과 receiver surface 동기화 필요. |
| `local_multifile_interface_signature_update` | multifile_refactor | D4 | interface와 implementer signature를 여러 파일에서 맞춰야 함. |
| `local_multifile_component_api_move` | multifile_refactor | D4 | API owner 변경과 consumer update가 필요하며 broad rewrite 금지. |
| `local_common_const_signature_mismatch` | single_file_compile_fix | D3 | 작은 const/signature mismatch지만 exact patch discipline이 중요. |
| `local_multifile_delegate_param_type_change` | multifile_refactor | D4 | delegate declaration과 receiver 간 parameter type drift. |
| `local_multifile_interface_return_type_change` | multifile_refactor | D4 | interface와 implementation 간 return type drift. |
| `local_multifile_subsystem_api_move` | multifile_refactor | D4 | subsystem API 이동에 따른 producer/consumer 동시 수정 필요. |
| `local_multifile_uproperty_type_migration` | multifile_refactor | D4 | reflected API와 cpp behavior를 보존하는 UPROPERTY type migration. |
| `local_multifile_event_binding_signature_change` | multifile_refactor | D4 | binding signature와 handler declaration/definition 일치 필요. |
| `local_multifile_base_class_method_override_change` | multifile_refactor | D4 | base class contract와 override signature 일치 필요. |
| `local_multifile_callback_param_expand` | multifile_refactor | D5 | callback typedef/handler/registration drift의 고위험 케이스; 과거 greedy regex corruption 발생 지점. |
| `local_multifile_method_split_callsite_update` | multifile_refactor | D4 | method split 후 consumer update와 behavior 보존 필요. |
| `local_reflection_blueprint_event_rename` | uht_reflection | D3 | Blueprint event rename 시 UE reflection convention 보존 필요. |
| `local_editor_runtime_guard_boundary` | editor_runtime_boundary | D4 | editor-only include와 `GEditor` linkage 제거 필요; `UnrealEd` Build.cs 금지. |
| `local_module_private_vs_public_dependency` | module_fix | D4 | public/private module dependency reasoning 필요. |
| `local_include_owner_forward_decl_mixup` | single_file_compile_fix | D3 | UE type usage에 맞는 include vs forward declaration 판단 필요. |
