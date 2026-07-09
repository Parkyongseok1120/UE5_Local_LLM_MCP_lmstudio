from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lmstudio_unreal_wrapper as wrapper  # noqa: E402


def test_build_retry_state_payload_marks_repeated_error():
    records = [
        {
            "text": "Message: fatal error C1083: Cannot open include file: 'UserWidget.h'",
            "metadata": {"error_code": "C1083", "error_subkind": "C1083_MISSING_INCLUDE"},
        }
    ]
    first = wrapper.build_retry_state_payload(
        previous_record=None,
        attempt=1,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )
    second = wrapper.build_retry_state_payload(
        previous_record=first["current"],
        attempt=2,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )

    assert second["recommendation"]["sameErrorRepeated"] is True
    assert second["current"]["errorRoute"]["broadMode"] == "module_fix"
    assert "Do not repeat the same patch" in wrapper.retry_feedback_block(second["recommendation"])


def test_build_error_record_priority_prefers_real_error_over_toolchain_warning():
    warning = {
        "text": "Message:\nVisual Studio compiler 14.51.36248 is not a preferred version",
        "metadata": {"severity": "warning", "error_code": "", "error_subkind": "COMPILE_GENERIC"},
    }
    c2511 = {
        "text": "Message:\n'void UDemo::DoWork(void)': overloaded member function not found",
        "metadata": {"severity": "error", "error_code": "C2511", "error_subkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
    }

    records = wrapper.prioritize_build_error_records([warning, c2511])

    assert records[0] is c2511


def test_retry_payload_preserves_specific_route_when_new_parse_is_generic():
    records = [
        {
            "text": "Message: Visual Studio compiler 14.51.36248 is not a preferred version",
            "metadata": {"error_code": "", "error_subkind": "COMPILE_GENERIC"},
        }
    ]
    previous_route = {
        "errorSubkind": "LNK_MISSING_CPP_DEFINITION",
        "broadMode": "compile_fix",
        "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
        "softSteering": [],
    }

    payload = wrapper.build_retry_state_payload(
        previous_record=None,
        previous_route=previous_route,
        attempt=2,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )

    assert payload["current"]["errorRoute"]["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert payload["current"]["routePreservedFromInitial"] is True


def test_retry_payload_preserves_editor_runtime_route_for_ueditorengine_c1083():
    records = [
        {
            "text": "Message: fatal error C1083: Cannot open include file: 'UEditorEngine.h'",
            "metadata": {"error_code": "C1083", "error_subkind": "C1083_MISSING_INCLUDE"},
        }
    ]
    previous_route = {
        "errorSubkind": "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
        "broadMode": "editor_runtime_fix",
        "forbiddenActions": ["adding UnrealEd to runtime module as default fix"],
    }

    payload = wrapper.build_retry_state_payload(
        previous_record=None,
        previous_route=previous_route,
        attempt=2,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Private/EditorBoundary.cpp"],
        build_log_path="ubt.log",
        fallback_message="",
        mode="editor_runtime_fix",
        request="Fix runtime module boundary; do not add UnrealEd to Build.cs.",
    )

    route = payload["current"]["errorRoute"]
    assert route["errorSubkind"] == "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE"
    assert route["broadMode"] == "editor_runtime_fix"
    assert "adding UnrealEd to runtime module as default fix" in route["forbiddenActions"]


def test_optional_symbol_graph_context_missing_graph_is_empty(monkeypatch):
    monkeypatch.setattr(wrapper, "load_symbol_graph", lambda: {"version": 1, "symbols": []})

    assert wrapper.optional_symbol_graph_context("ADemoActor C1083") == ""


def test_rag_telemetry_summary_counts_sidecars(tmp_path):
    rows = [
        {"source": "project_guideline", "layer": "compile_fix", "resolved_mode": "module_fix"},
        {
            "source": "rag_sidecar",
            "sidecarType": "module_resolver",
            "items": [{"module": "UMG"}],
            "resolved_mode": "module_fix",
        },
        {
            "source": "rag_sidecar",
            "sidecarType": "error_route",
            "items": [{"broadMode": "module_fix", "errorSubkind": "C1083_MISSING_INCLUDE"}],
            "resolved_mode": "module_fix",
        },
    ]

    telemetry = wrapper.summarize_rag_telemetry(
        query="C1083 UserWidget.h",
        requested_mode="module_fix",
        selected_mode="module_fix",
        rows=rows,
        context="abc",
    )
    wrapper.write_rag_telemetry(tmp_path, telemetry)

    assert telemetry["normalRowCount"] == 1
    assert telemetry["sidecarRowCount"] == 2
    assert telemetry["sidecarCountsByType"] == {"module_resolver": 1, "error_route": 1}
    assert telemetry["suspectedModules"] == ["UMG"]
    assert (tmp_path / "rag_telemetry.jsonl").is_file()


def test_wrapper_rag_project_filters_prefers_explicit_project_file(tmp_path):
    project_dir = tmp_path / "DemoProject"
    args = SimpleNamespace(project_file=str(project_dir / "DemoProject.uproject"))

    assert wrapper.wrapper_rag_project_filters(args) == ["DemoProject"]


def test_collect_rag_context_passes_project_filter_for_live_refactor(tmp_path, monkeypatch):
    index = tmp_path / "rag.sqlite"
    index.write_text("", encoding="utf-8")
    project_dir = tmp_path / "AdventureGame"
    captured = {}

    def fake_search(index_path, query, top_k, options):
        captured["projects"] = options.projects
        captured["mode"] = options.mode
        return [
            {
                "source": "project_source",
                "layer": "project_text",
                "resolved_mode": "refactor_r2",
            }
        ]

    monkeypatch.setattr(wrapper, "search_index", fake_search)
    monkeypatch.setattr(wrapper, "assemble_context", lambda rows, request, mode, **kwargs: "ctx")
    monkeypatch.setattr(wrapper, "write_rag_telemetry", lambda run_dir, record: None)

    args = SimpleNamespace(
        index=str(index),
        mode="refactor_r2",
        top_k=8,
        project_file=str(project_dir / "AdventureGame.uproject"),
    )

    context, used_ids = wrapper.collect_rag_context(args, "refactor cinematic logging")
    assert context.endswith("ctx")
    assert isinstance(used_ids, set)
    assert captured["mode"] == "refactor_r2"
    assert captured["projects"] == ["AdventureGame"]


def test_rag_telemetry_summary_includes_route_hint_fields():
    rows = [
        {
            "source": "rag_sidecar",
            "sidecarType": "error_route",
            "items": [
                {
                    "broadMode": "compile_fix",
                    "errorSubkind": "LNK_MISSING_CPP_DEFINITION",
                    "requiredReads": ["header declaration", "matching cpp definition"],
                    "allowedPatchTargets": ["matching cpp/header"],
                    "forbiddenActions": ["Build.cs-first fix without module evidence"],
                    "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
                    "routePriorityApplied": "lnk_missing_definition_before_signature_mismatch",
                }
            ],
            "resolved_mode": "compile_fix",
        }
    ]

    telemetry = wrapper.summarize_rag_telemetry(
        query="LNK2019 unresolved external",
        requested_mode="compile_fix",
        selected_mode="compile_fix",
        rows=rows,
        context="abc",
    )

    assert telemetry["requiredReadHints"] == ["header declaration", "matching cpp definition"]
    assert telemetry["allowedPatchTargetHints"] == ["matching cpp/header"]
    assert telemetry["forbiddenActionHints"] == ["Build.cs-first fix without module evidence"]
    assert telemetry["buildCsFirstWarningEmitted"] is True
    assert telemetry["buildCsUnsupportedForRouteWarning"] is True
    assert telemetry["routePriorityApplied"] == "lnk_missing_definition_before_signature_mismatch"
    assert telemetry["staticValidationRetryHint"] is False


def test_soft_route_feedback_warns_without_hard_blocking():
    route = {
        "softSteering": ["This looks like a header/cpp signature mismatch."],
        "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
    }

    feedback = wrapper.soft_route_feedback(route, module_evidence=False)
    warning = wrapper.build_cs_first_soft_warning(route, ["Source/Demo/Demo.Build.cs"], module_evidence=False)

    assert "warning only" in feedback
    assert "header/cpp signature mismatch" in feedback
    assert "Build.cs-first fix is not supported" in warning


def test_build_cs_unsupported_warning_for_signature_route():
    route = {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "Build.cs edit is not supported by the current route" in warning
    assert "matching header/cpp file" in warning


def test_build_cs_unsupported_warning_for_lnk_route():
    route = {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "missing implementation related" in warning


def test_build_cs_unsupported_warning_not_emitted_for_module_evidence():
    route = {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=True,
    )

    assert warning == ""


def test_unsupported_build_cs_soft_replan_for_signature_route():
    route = {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"}

    feedback = wrapper.unsupported_build_cs_soft_replan_feedback(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "Soft replan" in feedback
    assert "matching header/cpp pair only" in feedback


def test_static_validation_retry_feedback_for_signature_mismatch():
    finding = wrapper.Finding(
        "warning",
        "Source/Demo/Private/DemoComponent.cpp",
        3,
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "signature mismatch",
    )

    feedback = wrapper.static_validation_retry_feedback(
        [finding],
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
    )

    assert "Static validation still reports CPP_FUNCTION_SIGNATURE_MISMATCH" in feedback
    assert "Do not edit Build.cs unless module evidence exists" in feedback


def test_static_validation_retry_feedback_for_lnk_remaining():
    feedback = wrapper.static_validation_retry_feedback(
        [],
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        build_output="error LNK2019: unresolved external symbol UDemoComponent::StartDash",
    )

    assert "unresolved external / missing cpp definition remains" in feedback


def test_first_attempt_patch_preset_is_limited_to_decl_definition_routes():
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        "compile_fix",
    )
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        "compile_fix",
    )
    assert not wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "C1083_MISSING_INCLUDE"},
        "compile_fix",
    )
    assert not wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        "analyze",
    )
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        "multifile_refactor",
    )


def test_multifile_refactor_mode_directive_names_hidden_file_surfaces():
    directive = wrapper.mode_directive("multifile_refactor")

    assert "declaration" in directive
    assert "definition" in directive
    assert "callsite" in directive
    assert "binding" in directive
    assert "override" in directive
    assert "cpp-only" in directive


def test_refactor_r0_directive_requires_scope_and_approval_gates():
    directive = wrapper.mode_directive("refactor_r0")

    assert "classify scope" in directive
    assert "approval gates" in directive
    assert "No code edits" in directive


def test_refactor_r2_directive_limits_execution_to_approved_cluster():
    directive = wrapper.mode_directive("refactor_r2")

    assert "approved implementation cluster" in directive
    assert "Do not combine API migration" in directive
    assert "Existing files are patch-only" in directive
    assert "UBT must pass" in directive


def test_refactor_mode_rejects_existing_full_file_rewrite(tmp_path: Path):
    target = tmp_path / "Source" / "Demo" / "Private" / "DemoComponent.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void UDemoComponent::OldName() {}\n", encoding="utf-8")

    bundle = {
        "files": [
            {
                "path": "Source/Demo/Private/DemoComponent.cpp",
                "content": "void UDemoComponent::NewName() {}\n",
            }
        ],
        "patches": [],
    }

    blockers = wrapper.existing_full_file_rewrite_blockers(tmp_path, bundle, "refactor_r2")

    assert blockers
    assert "patches[]" in blockers[0]
    assert "files[] is only for new files" in blockers[0]


def test_refactor_mode_allows_new_file_content(tmp_path: Path):
    bundle = {
        "files": [
            {
                "path": "Source/Demo/Private/NewComponent.cpp",
                "content": "void NewHelper() {}\n",
            }
        ],
        "patches": [],
    }

    assert wrapper.existing_full_file_rewrite_blockers(tmp_path, bundle, "refactor_r2") == []


def test_non_refactor_mode_allows_legacy_full_file_response(tmp_path: Path):
    target = tmp_path / "Source" / "Demo" / "Private" / "DemoComponent.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void UDemoComponent::OldName() {}\n", encoding="utf-8")
    bundle = {
        "files": [
            {
                "path": "Source/Demo/Private/DemoComponent.cpp",
                "content": "void UDemoComponent::NewName() {}\n",
            }
        ],
        "patches": [],
    }

    assert wrapper.existing_full_file_rewrite_blockers(tmp_path, bundle, "compile_fix") == []


def test_refactor_system_prompt_makes_existing_files_patch_only():
    prompt = wrapper.system_prompt("", {"maxFilesPerEdit": 2, "preferPatchOverFullFile": True}, mode="refactor_r2")

    assert "Refactor patch-only rule" in prompt
    assert "existing files in files[] will be rejected" in prompt


def test_missing_definition_full_file_merge_preserves_existing_call_site(tmp_path: Path):
    source = tmp_path / "Source" / "HoldoutFixture" / "Private"
    source.mkdir(parents=True)
    target = source / "HoldoutMissingDefinitionComponent.cpp"
    target.write_text(
        "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
        "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
        "{\n"
        "\tSuper::BeginPlay();\n"
        "\tStartDash();\n"
        "}\n",
        encoding="utf-8",
    )
    bundle = {
        "answer": "Added missing definition.",
        "files": [
            {
                "path": "Source/HoldoutFixture/Private/HoldoutMissingDefinitionComponent.cpp",
                "content": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}\n\n"
                    "void UHoldoutMissingDefinitionComponent::StartDash()\n"
                    "{\n"
                    "\t// TODO: Implement StartDash logic here.\n"
                    "}\n"
                ),
            }
        ],
        "patches": [],
        "notes": [],
    }

    merged = wrapper.merge_missing_definition_full_file_edits(
        tmp_path,
        bundle,
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    content = merged["files"][0]["content"]
    assert "\tStartDash();\n" in content
    assert content.count("UHoldoutMissingDefinitionComponent::StartDash()") == 1
    assert "append-only cpp preservation" in merged["notes"][0]


def test_missing_definition_patch_merge_handles_stale_old_text(tmp_path: Path):
    source = tmp_path / "Source" / "HoldoutFixture" / "Private"
    source.mkdir(parents=True)
    target = source / "HoldoutMissingDefinitionComponent.cpp"
    target.write_text(
        "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
        "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
        "{\n"
        "\tSuper::BeginPlay();\n"
        "\tStartDash();\n"
        "}\n",
        encoding="utf-8",
    )
    bundle = {
        "answer": "Added missing definition.",
        "files": [],
        "patches": [
            {
                "path": "Source/HoldoutFixture/Private/HoldoutMissingDefinitionComponent.cpp",
                "oldText": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}"
                ),
                "newText": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::StartDash()\n"
                    "{\n"
                    "\t// TODO: Implement dash logic.\n"
                    "}\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}"
                ),
                "expectedOccurrences": 1,
            }
        ],
        "notes": [],
    }

    merged = wrapper.merge_missing_definition_full_file_edits(
        tmp_path,
        bundle,
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    assert merged["patches"] == []
    assert len(merged["files"]) == 1
    content = merged["files"][0]["content"]
    assert "\tStartDash();\n" in content
    assert content.count("UHoldoutMissingDefinitionComponent::StartDash()") == 1


def test_build_retry_state_payload_carries_static_validation_hint():
    records = [
        {
            "text": "Message: CPP_FUNCTION_SIGNATURE_MISMATCH Source/Demo/Private/DemoComponent.cpp",
            "metadata": {"error_code": "", "error_subkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        }
    ]
    finding = wrapper.Finding(
        "warning",
        "Source/Demo/Private/DemoComponent.cpp",
        3,
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "signature mismatch",
    )

    payload = wrapper.build_retry_state_payload(
        previous_record=None,
        attempt=1,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
        static_findings=[finding],
    )

    assert "CPP_FUNCTION_SIGNATURE_MISMATCH" in payload["current"]["staticValidationRetryHint"]


def test_write_rag_telemetry_without_run_dir_is_noop():
    wrapper.write_rag_telemetry(None, {"query": "noop"})


def test_cached_rag_context_reuses_value_for_same_key():
    cache: dict[str, str] = {}
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "assembled context"

    assert wrapper.cached_rag_context(cache, "same-query", factory) == "assembled context"
    assert wrapper.cached_rag_context(cache, "same-query", factory) == "assembled context"
    assert calls == 1


def test_rag_context_cache_key_sorts_changed_files(tmp_path):
    project_file = tmp_path / "Demo" / "Demo.uproject"
    args = SimpleNamespace(
        index=str(tmp_path / "rag.sqlite"),
        mode="compile_fix",
        top_k=4,
        project_file=str(project_file),
    )

    left = wrapper.rag_context_cache_key(args, "delta", "C2511", changed_files=["B.cpp", "A.h"])
    right = wrapper.rag_context_cache_key(args, "delta", "C2511", changed_files=["A.h", "B.cpp"])
    different = wrapper.rag_context_cache_key(args, "delta", "C2511", changed_files=["A.h"])

    assert left == right
    assert left != different


def test_project_summary_prioritizes_focus_paths(tmp_path: Path):
    source = tmp_path / "Source" / "Demo" / "Public"
    source.mkdir(parents=True)
    (source / "AOther.h").write_text(
        "UCLASS()\nclass DEMO_API UAOther : public UObject\n{\n\tGENERATED_BODY()\n};\n",
        encoding="utf-8",
    )
    target = source / "TargetComponent.h"
    target.write_text(
        "UCLASS()\nclass DEMO_API UTargetComponent : public UActorComponent\n{\n\tGENERATED_BODY()\n\tvoid RefreshScore();\n};\n",
        encoding="utf-8",
    )

    summary = wrapper.summarize_project_state(
        tmp_path,
        max_files=1,
        max_chars=1200,
        focus_text="C2511 in Source/Demo/Public/TargetComponent.h",
    )

    assert "Focused files are summarized first" in summary
    assert "Source/Demo/Public/TargetComponent.h" in summary
    assert "Source/Demo/Public/AOther.h" not in summary


def test_project_summary_prioritizes_absolute_windows_style_focus_path(tmp_path: Path):
    source = tmp_path / "Source" / "Demo" / "Public"
    source.mkdir(parents=True)
    target = source / "TargetComponent.h"
    target.write_text(
        "UCLASS()\nclass DEMO_API UTargetComponent : public UActorComponent\n{\n\tGENERATED_BODY()\n};\n",
        encoding="utf-8",
    )
    other = source / "AOther.h"
    other.write_text(
        "UCLASS()\nclass DEMO_API UAOther : public UObject\n{\n\tGENERATED_BODY()\n};\n",
        encoding="utf-8",
    )

    absoluteish = str(target).replace("/", "\\")
    summary = wrapper.summarize_project_state(
        tmp_path,
        max_files=1,
        max_chars=1200,
        focus_text=f"{absoluteish}(17): error C2511",
    )

    assert "Source/Demo/Public/TargetComponent.h" in summary
    assert "Source/Demo/Public/AOther.h" not in summary


def test_project_summary_limits_shrink_after_first_attempt(monkeypatch):
    monkeypatch.setattr(wrapper.token_budget, "project_summary_limits", lambda mode: (22, 6000))

    assert wrapper.project_summary_limits_for_attempt("compile_fix", 1) == (22, 6000)
    assert wrapper.project_summary_limits_for_attempt("compile_fix", 2) == (6, 3300)


def test_refactor_surface_evidence_names_callsite_binding_and_override(tmp_path: Path):
    public = tmp_path / "Source" / "Demo" / "Public"
    private = tmp_path / "Source" / "Demo" / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "DemoComponent.h").write_text(
        "class UDemoComponent : public UActorComponent\n"
        "{\n"
        "\tvirtual void ActivateFeature(float Strength) override;\n"
        "};\n",
        encoding="utf-8",
    )
    (private / "DemoComponent.cpp").write_text(
        "void UDemoComponent::ActivateFeature(float Strength)\n"
        "{\n"
        "\tOnFeatureChanged.AddDynamic(this, &UDemoComponent::ActivateFeature);\n"
        "}\n",
        encoding="utf-8",
    )
    (private / "FeatureUser.cpp").write_text(
        "void UseFeature(UDemoComponent* Component)\n"
        "{\n"
        "\tComponent->ActivateFeature(1.0f);\n"
        "}\n",
        encoding="utf-8",
    )

    evidence = wrapper.refactor_surface_evidence(
        tmp_path,
        "Update UDemoComponent::ActivateFeature and all callsites",
    )

    assert "Multifile/refactor surface evidence" in evidence
    assert "DemoComponent.h" in evidence
    assert "DemoComponent.cpp" in evidence
    assert "FeatureUser.cpp" in evidence
    assert "override" in evidence
    assert "definition" in evidence
    assert "callsite" in evidence
    assert "binding/delegate" in evidence


def test_focused_current_source_evidence_prefers_structured_evidence_over_full_snippet(tmp_path: Path):
    header = tmp_path / "Source" / "Demo" / "Public" / "DashComponent.h"
    cpp = tmp_path / "Source" / "Demo" / "Private" / "DashComponent.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    header.write_text(
        "class DEMO_API UDashComponent : public UActorComponent\n{\npublic:\n    void StartDash();\n};\n",
        encoding="utf-8",
    )
    cpp.write_text(
        "#include \"DashComponent.h\"\n"
        "void UDashComponent::BeginPlay()\n"
        "{\n"
        "\tStartDash();\n"
        "}\n",
        encoding="utf-8",
    )

    evidence = wrapper.focused_current_source_evidence(
        tmp_path,
        "LNK2019 unresolved external UDashComponent::StartDash",
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    assert "Current declaration/definition evidence" in evidence
    assert "Focused current source evidence" not in evidence
    assert len(evidence) < 2500
