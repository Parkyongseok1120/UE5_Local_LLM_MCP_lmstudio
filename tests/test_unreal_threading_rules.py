"""Fixtures for the conservative thread/async/subsystem rules (S3).

Each rule has 4 problem cases (must warn), 4 valid cases (must stay silent)
and 2 unknown cases where the context cannot be established lexically (must
stay silent rather than guess). All findings are advisory warnings with
contract metadata.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from unreal_static_threading import (  # noqa: E402
    merge_overlapping_cleanup_findings,
    validate_subsystem_contracts,
    validate_thread_contracts,
)
from unreal_static_model import Finding  # noqa: E402

BASES = {
    "UMySub": "UWorldSubsystem", "UOtherSub": "UWorldSubsystem", "UTick": "UTickableWorldSubsystem",
    "UGISub": "UGameInstanceSubsystem", "UMyGI": "UGameInstanceSubsystem", "UMyComp": "UActorComponent",
}


def run(tmp_path: Path, source: str, validator) -> list[Finding]:
    path = tmp_path / "Source/Game/Private/Subject.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return validator(path, source, tmp_path, BASES)


def codes(findings: list[Finding], code: str) -> list[Finding]:
    return [f for f in findings if f.code == code]


GAME_THREAD = "GAME_THREAD_API_OFF_THREAD"
LIFETIME = "ASYNC_UOBJECT_LIFETIME_RISK"
RENDER = "RENDER_THREAD_UOBJECT_ACCESS_REVIEW"
DEPENDENCY = "SUBSYSTEM_INITIALIZATION_DEPENDENCY_REVIEW"
CLEANUP = "SUBSYSTEM_CLEANUP_NOT_OBSERVED"

CASES = {
    GAME_THREAD: (validate_thread_contracts, {
        "problem": [
            "void FWork::Go(){ AsyncTask(ENamedThreads::AnyBackgroundThreadNormalTask, [](){ GetWorldTimerManager().SetTimer(H, 1.f, false); }); }",
            "void FWork::Go(){ Async(EAsyncExecution::ThreadPool, [](){ World->SpawnActor<AActor>(); }); }",
            "void FWork::Go(){ UE::Tasks::Launch(UE_SOURCE_LOCATION, [](){ NewObject<UObject>(); }); }",
            "void FWork::Go(){ ParallelFor(Num, [&](int32 I){ Items[I]->Destroy(); }); }",
        ],
        "valid": [
            "void FWork::Go(){ AsyncTask(ENamedThreads::GameThread, [](){ World->SpawnActor<AActor>(); }); }",
            "void FWork::Go(){ AsyncTask(ENamedThreads::AnyThread, [Data](){ Sum = Compute(Data); }); }",
            "void FWork::Go(){ AsyncTask(ENamedThreads::AnyThread, [](){ int32 V = 1; AsyncTask(ENamedThreads::GameThread, [V](){ GetWorldTimerManager().SetTimer(H, 1.f, false); }); }); }",
            "void FWork::Go(){ Async(EAsyncExecution::TaskGraphMainThread, [](){ NewObject<UObject>(); }); }",
        ],
        "unknown": [
            "void FWork::Go(){ AsyncTask(ENamedThreads::AnyThread, [](){ DoWorkThatMaySpawn(); }); }",
            "uint32 FMyRunnable::Run(){ World->SpawnActor<AActor>(); return 0; }",
        ],
    }),
    LIFETIME: (validate_thread_contracts, {
        "problem": [
            "void AFoo::Start(){ AsyncTask(ENamedThreads::AnyThread, [this](){ Value = 1; }); }",
            "void UFoo::Start(){ AsyncTask(ENamedThreads::GameThread, [this](){ Done(); }); }",
            "void UFoo::Start(){ Async(EAsyncExecution::ThreadPool, [=](){ this->Value = 2; }); }",
            "void UMyComp::Go(){ UE::Tasks::Launch(UE_SOURCE_LOCATION, [this](){ Count++; }); }",
        ],
        "valid": [
            "void FWorker::Go(){ AsyncTask(ENamedThreads::AnyThread, [this](){ Count++; }); }",
            "void AFoo::Start(){ AsyncTask(ENamedThreads::GameThread, [WeakThis = TWeakObjectPtr<AFoo>(this)](){ if (WeakThis.IsValid()) { WeakThis->Done(); } }); }",
            "void AFoo::Start(){ ParallelFor(Num, [this](int32 I){ Values[I] = I; }); }",
            "void AFoo::Start(){ AsyncTask(ENamedThreads::AnyThread, [Copy = Value](){ Use(Copy); }); }",
        ],
        "unknown": [
            "void Go(UObject* Obj){ AsyncTask(ENamedThreads::AnyThread, [Obj](){ Obj->MarkAsGarbage(); }); }",
            "void Helper::Go(){ AsyncTask(ENamedThreads::AnyThread, [this](){ Count++; }); }",
        ],
    }),
    RENDER: (validate_thread_contracts, {
        "problem": [
            "void AFoo::Tick(float D){ ENQUEUE_RENDER_COMMAND(Upd)([this](FRHICommandListImmediate& RHICmdList){ Use(Value); }); }",
            "void FWork::Go(){ ENQUEUE_RENDER_COMMAND(Upd)([Comp](FRHICommandListImmediate& RHICmdList){ AActor* Owner = Comp->GetOwner(); }); }",
            "void UMyComp::Send(){ ENQUEUE_RENDER_COMMAND(Upd)([this](FRHICommandListImmediate& RHICmdList){ Count++; }); }",
            "void FWork::Go(){ ENQUEUE_RENDER_COMMAND(Upd)([Obj](FRHICommandListImmediate& RHICmdList){ auto* P = Cast<APawn>(Obj); }); }",
        ],
        "valid": [
            "void FMySceneProxy::Update(){ ENQUEUE_RENDER_COMMAND(Upd)([this](FRHICommandListImmediate& RHICmdList){ Data = 1; }); }",
            "void AFoo::Tick(float D){ ENQUEUE_RENDER_COMMAND(Upd)([Value = Value](FRHICommandListImmediate& RHICmdList){ Use(Value); }); }",
            "void AFoo::Tick(float D){ ENQUEUE_RENDER_COMMAND(Upd)([Proxy = SceneProxy](FRHICommandListImmediate& RHICmdList){ Proxy->Update(); }); }",
            "void AFoo::Tick(float D){ ENQUEUE_RENDER_COMMAND(Upd)([](FRHICommandListImmediate& RHICmdList){ Use(1); }); }",
        ],
        "unknown": [
            "void Helper::Send(){ ENQUEUE_RENDER_COMMAND(Upd)([this](FRHICommandListImmediate& RHICmdList){ Count++; }); }",
            "void Send(FData Data){ ENQUEUE_RENDER_COMMAND(Upd)([Data](FRHICommandListImmediate& RHICmdList){ Use(Data); }); }",
        ],
    }),
    DEPENDENCY: (validate_subsystem_contracts, {
        "problem": [
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ Super::Initialize(Collection); GetWorld()->GetSubsystem<UOtherSub>()->Register(this); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ UOtherSub* Other = GetWorld()->GetSubsystem<UOtherSub>(); Other->Register(this); }",
            "void UMyGI::Initialize(FSubsystemCollectionBase& Collection){ GetGameInstance()->GetSubsystem<UGISub>()->Prepare(); }",
            "void UTick::Initialize(FSubsystemCollectionBase& Collection){ GetWorld()->GetSubsystem<UOtherSub>()->Register(this); }",
        ],
        "valid": [
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ Collection.InitializeDependency<UOtherSub>(); GetWorld()->GetSubsystem<UOtherSub>()->Register(this); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ GetWorld()->GetGameInstance()->GetSubsystem<UGISub>()->Prepare(); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ CachedOther = GetWorld()->GetSubsystem<UOtherSub>(); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ Collection.InitializeDependency(UOtherSub::StaticClass()); GetWorld()->GetSubsystem<UOtherSub>()->Register(this); }",
        ],
        "unknown": [
            "void UMySub::Initialize(FSubsystemCollectionBase& Collection){ GetWorld()->GetSubsystem<UUnknownSub>()->Register(this); }",
            "void UNotSub::Initialize(FSubsystemCollectionBase& Collection){ GetWorld()->GetSubsystem<UOtherSub>()->Register(this); }",
        ],
    }),
    CLEANUP: (validate_subsystem_contracts, {
        "problem": [
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ FWorldDelegates::OnPostWorldCleanup.AddUObject(this, &UMySub::H); }\nvoid UMySub::Deinitialize(){ Super::Deinitialize(); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ Handle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateUObject(this, &UMySub::T)); }",
            "void UMySub::OnWorldBeginPlay(UWorld& W){ FCoreUObjectDelegates::PostLoadMapWithWorld.AddUObject(this, &UMySub::L); }\nvoid UMySub::Deinitialize(){ Super::Deinitialize(); }",
            "void UMyGI::Initialize(FSubsystemCollectionBase& C){ FCoreDelegates::OnPreExit.AddUObject(this, &UMyGI::Exit); }",
        ],
        "valid": [
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ FWorldDelegates::OnPostWorldCleanup.AddUObject(this, &UMySub::H); }\nvoid UMySub::Deinitialize(){ FWorldDelegates::OnPostWorldCleanup.RemoveAll(this); Super::Deinitialize(); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ Handle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateUObject(this, &UMySub::T)); }\nvoid UMySub::Deinitialize(){ FTSTicker::GetCoreTicker().RemoveTicker(Handle); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ Super::Initialize(C); }",
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ FWorldDelegates::OnPostWorldCleanup.AddUObject(this, &UMySub::H); }\nvoid UMySub::PreDeinitialize(){ FWorldDelegates::OnPostWorldCleanup.Remove(Handle); }",
        ],
        "unknown": [
            "void UMySub::Initialize(FSubsystemCollectionBase& C){ FWorldDelegates::OnPostWorldCleanup.AddUObject(this, &UMySub::H); }\nvoid UMySub::Deinitialize(){ UnbindAll(); Super::Deinitialize(); }",
            "void UNotSub::Initialize(FSubsystemCollectionBase& C){ FWorldDelegates::OnPostWorldCleanup.AddUObject(this, &UNotSub::H); }",
        ],
    }),
}

PARAMS = [(rule, group, index) for rule, (_, groups) in CASES.items()
          for group, sources in groups.items() for index in range(len(sources))]


@pytest.mark.parametrize("rule, group, index", PARAMS)
def test_rule_fixture(tmp_path, rule, group, index):
    validator, groups = CASES[rule]
    found = codes(run(tmp_path, groups[group][index], validator), rule)
    if group == "problem":
        assert len(found) == 1, groups[group][index]
        finding = found[0]
        assert finding.severity == "warning"
        meta = finding.metadata
        assert meta["ruleId"] == rule and meta["analysisKind"] == "lexical"
        assert meta["confidence"] in {"high", "medium"} and meta["limitations"]
        assert meta["contractIds"] and meta["contractRevision"]
    else:
        assert found == [], groups[group][index]


def test_fixture_counts_meet_the_minimum_per_rule():
    for rule, (_, groups) in CASES.items():
        assert (len(groups["problem"]), len(groups["valid"]), len(groups["unknown"])) == (4, 4, 2), rule


def test_timer_rule_is_high_confidence_engine_fact():
    finding = codes(run(Path(pytest.importorskip("tempfile").mkdtemp()), CASES[GAME_THREAD][1]["problem"][0],
                        validate_thread_contracts), GAME_THREAD)[0]
    assert finding.metadata["confidence"] == "high"
    assert finding.metadata["contractIds"] == ["ue.timer.game-thread"]


def test_one_cause_one_warning_when_timer_rule_already_fired():
    timer = Finding("warning", "a.cpp", 3, "TIMER_SET_WITHOUT_CLEAR", "timer")
    cleanup = Finding("warning", "a.cpp", 3, CLEANUP, "cleanup")
    other = Finding("warning", "a.cpp", 9, CLEANUP, "cleanup elsewhere")
    assert merge_overlapping_cleanup_findings([cleanup, other], [timer]) == [other]
