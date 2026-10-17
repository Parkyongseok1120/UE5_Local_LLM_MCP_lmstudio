"""UE correctness eval cases: schema, contract links and scorer self-check.

Each case is scored with a correct answer (must pass without penalties) and an
answer repeating the recorded mistake (must be penalised or fail), so a loose
regex cannot silently reward the error the case was written for.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_unreal_review_answers as scorer  # noqa: E402
import unreal_contracts  # noqa: E402

CASE_SET = json.loads((ROOT / "config" / "unreal_correctness_eval_cases.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in CASE_SET["cases"]}

ANSWERS = {
    "cmc_prediction_is_engine_default": (
        "엔진 기본: CharacterMovementComponent(CMC)가 소유 클라이언트 이동 예측을 이미 수행하고, 서버가 ClientAdjustPosition으로 보정한다. "
        "프로젝트 구현: AddMovementInput만 호출하며 별도 예측 계층은 없다.",
        "이 프로젝트의 CMC에는 이동 예측이 없다. 클라이언트 예측을 직접 구현해야 한다."),
    "owner_see_flags_are_render_visibility": (
        "SetOnlyOwnerSee와 SetOwnerNoSee는 렌더링 가시성 플래그다. 복제나 relevancy와는 무관하며 모든 머신에 컴포넌트가 존재한다.",
        "Arms는 OnlyOwnerSee라서 다른 클라이언트로 복제되지 않는다. 대역폭이 절약된다."),
    "netmode_branch_runs_beyond_remote_clients": (
        "GetNetMode() != NM_DedicatedServer 분기는 Standalone, ListenServer(리슨 서버 호스트), Client에서 실행된다. "
        "전용 서버에서만 생략되며 원격 클라이언트에만 한정되지 않는다.",
        "이 이펙트는 원격 클라이언트에서만 실행된다.\n서버에서는 호출되지 않는다."),
    "link_claim_requires_build_evidence": (
        "확인된 것: CombatCore.cpp와 IMPLEMENT_MODULE(모듈 구현)이 보이지 않는다. 확인이 필요한 것: 실제 영향은 빌드로 확인해야 한다. "
        "build_unreal_project로 검증하기 전에는 위험으로만 기록한다.",
        "IMPLEMENT_MODULE이 없으므로 링크 에러가 발생한다. 빌드로 확인할 필요는 없다."),
    "redundant_check_is_cleanup_not_defect": (
        "Weapon && IsValid(Weapon)은 중복 검사다. 동작 결함은 아니므로 정리 제안으로 분류하고 IsValid(Weapon) 하나로 단순화한다.",
        "P2 확정 오류: 중복 검사(Weapon && IsValid(Weapon))가 있다. 불필요한 검사는 제거한다."),
    "worker_thread_spawns_actor": (
        "SpawnActor를 AnyBackgroundThreadNormalTask 워커에서 호출한다. 액터 생성은 게임 스레드에서 해야 하므로 "
        "AsyncTask(ENamedThreads::GameThread, ...)로 돌아와 호출한다. this를 그대로 캡처하면 파괴 후 접근할 수 있으니 TWeakObjectPtr로 캡처한다.",
        "람다에서 ComputeSpawnTransform을 호출하는 구조는 깔끔하다. 특별한 문제는 없다."),
    "subsystem_initialize_dependency": (
        "같은 GameInstance 컬렉션의 USaveSubsystem을 Initialize에서 바로 호출하므로 초기화 순서가 보장되지 않는다. "
        "Collection.InitializeDependency<USaveSubsystem>()를 먼저 호출한다.",
        "GetSubsystem 호출은 문제 없다. Initialize는 적절하다."),
    "inherited_lifecycle_override_is_valid": (
        "BeginDestroy는 UObject의 GC 해체 단계라 override 자체는 유효하다. 다만 delegate 해제는 서브시스템 종료 시점인 "
        "Deinitialize로 옮겨 RemoveAll(this)을 호출하는 것이 맞다.",
        "UTickableWorldSubsystem에서 BeginDestroy는 잘못된 override이다. Deinitialize만 사용해야 한다."),
}


# Engine-source-backed regressions; these exercise the rubric, not a model benchmark.
ANSWERS.update({'actor_damage_returns_processed_amount': ('AActor는 ActualDamage = DamageAmount로 시작하고 InternalTakePointDamage 기본 구현은 전달된 Damage를 반환한다. APawn은 ShouldTakeDamage 검사를 하고 Super는 피해 이벤트 통지도 수행한다. 무조건 0이라는 전제는 틀리므로 Super를 없애지 않는다.', 'Super::TakeDamage는 항상 0을 반환한다. 헬스 컴포넌트가 없어서 전투가 불가능하니 Actual = Damage로 교체한다.'), 'spawned_controller_initializes_player_state': ('서버/Standalone의 PostInitializeComponents에서 InitPlayerState를 호출해 GameMode의 PlayerStateClass로 생성한다. 수동 스폰도 이 경로를 거치며 생성 실패에 대한 널 가드는 별도로 검토한다.', '로그인이 없으므로 PlayerState는 반드시 null이다. 모든 테스트가 크래시한다.'), 'standalone_game_instance_creates_world': ('InitializeStandalone은 WorldContext를 생성하고 CreateWorld로 임시 월드를 만든 뒤 SetCurrentWorld로 연결한다. 이후 추가 초기화 성공 여부는 별도 확인 대상이다.', 'InitializeStandalone은 월드를 생성하지 않는다. GetWorld가 null이라 반드시 크래시한다.'), 'timer_fixture_frame_counter_has_purpose': ('HasBeenTickedThisFrame은 LastTickedFrame과 GFrameCounter를 비교해 한 프레임의 중복 타이머 Tick을 막는다. 이 동기 fixture에서는 증가 이유가 있으므로 무조건 삭제하지 않는다. 전역 상태의 테스트 격리와 대체 구동 방법을 검증해야 한다.', 'GFrameCounter 증가는 불필요하니 즉시 삭제한다. World::Tick만 반복하면 타이머는 동일하게 동작한다.'), 'set_player_state_does_not_set_owner': ('SetPlayerState는 PlayerState 참조 대입을 담당한다. InitPlayerState는 별도로 SpawnInfo.Owner = this를 설정하므로 수동 생성은 이 소유권을 누락한다. 엔진 초기화 경로를 재사용하거나 동등한 Owner 설정을 확인한다.', 'SetPlayerState는 Owner를 자동으로 설정하므로 동일하다.'), 'server_client_rpc_targets_owner': ('GetFunctionCallspace는 원격 소유 연결이면 Client RPC를 해당 소유 클라이언트로 전송하고 로컬 소유자는 로컬 실행 경로를 사용한다. NetMulticast와 다르며 연결/복제 조건도 확인해야 한다.', 'Client RPC는 모든 클라이언트에 전송된다. 소유자 전용으로 만들려면 별도 원격 이벤트 코드를 작성해야 한다.'), 'repeated_die_checks_idempotence': ('마지막 DeathCount == 1 assertion과 Dead 가드를 보면 중복 호출에도 한 번만 집계되는 멱등성을 검증하는 목적을 읽을 수 있다. TakeDamage 경로의 동작은 별도 실행 근거로 확인한다.', '이 테스트는 의도적으로 은폐하는 코드다. Die 수동 호출을 삭제해야 한다.')})


def test_case_set_is_well_formed_and_linked_to_contracts():
    contracts = unreal_contracts.load_contracts()
    assert CASE_SET["contractRevision"] == unreal_contracts.contract_revision(contracts)
    assert len(CASES) == len(CASE_SET["cases"]) == len(ANSWERS)
    assert set(CASE_SET["protocol"]["pilot"]["caseIds"]) <= CASES.keys()
    assert CASE_SET["protocol"]["pilot"]["totalRuns"] == 2 * len(CASE_SET["protocol"]["pilot"]["caseIds"])
    for case in CASES.values():
        assert case["prompt"] and case["files"] and case["expected_findings"], case["id"]
        assert all(unreal_contracts.contract_by_id(contracts, cid) for cid in case["contractIds"]), case["id"]
        patterns = [p for finding in case["expected_findings"] for key in ("diagnosis", "fix") for p in finding.get(key, [])]
        patterns += [p for forbidden in case["forbidden_patterns"] for p in forbidden["patterns"]]
        for pattern in patterns:
            re.compile(pattern)


@pytest.mark.parametrize("case_id", sorted(ANSWERS))
def test_scorer_rewards_correct_answer_and_rejects_recorded_mistake(case_id):
    case, (good, bad) = CASES[case_id], ANSWERS[case_id]
    good_score = scorer.score_case(case, CASE_SET["defaults"], good)
    assert good_score.penalties == [] and good_score.ratio == 1.0, [f for f in good_score.findings if not f.diagnosis_ok or not f.fix_ok]
    bad_score = scorer.score_case(case, CASE_SET["defaults"], bad)
    assert bad_score.penalties or bad_score.ratio < 0.75, case_id
    if case["forbidden_patterns"]:
        assert {p.penalty_id for p in bad_score.penalties} & {f["id"] for f in case["forbidden_patterns"]}, case_id


# Wording recorded in the S5 pilot (artifacts/ue-correctness-20261017).
PILOT_LINK_ASSERTIONS = [
    "Game·Editor 두 타깃 모두 **링크 단계에서 LNK2019(unresolved external symbol)로 실패**할 것입니다.",
    "대부분의 UE 버전에서는 타겟 링크 단계에서 모듈 인터페이스 심볼 미해결로 **빌드 실패**합니다.",
    "The game target will fail at the link stage with LNK2019.",
]
PILOT_LINK_HEDGED = ("실패 모드의 정확한 형태: 위 문제의 결과가 UE 5.8에서 링크 에러인지, 아니면 링크는 통과하고 "
                     "런타임 모듈 로드 실패인지는 실제 빌드로만 확인 가능합니다.")


@pytest.mark.parametrize("sentence", PILOT_LINK_ASSERTIONS)
def test_future_tense_link_failure_assertions_are_penalised(sentence):
    case = CASES["link_claim_requires_build_evidence"]
    answer = ANSWERS["link_claim_requires_build_evidence"][0] + "\n" + sentence
    penalties = scorer.score_case(case, CASE_SET["defaults"], answer).penalties
    assert [p.penalty_id for p in penalties] == ["asserted_link_failure"], sentence


def test_hedged_link_outcome_is_not_penalised():
    case = CASES["link_claim_requires_build_evidence"]
    answer = ANSWERS["link_claim_requires_build_evidence"][0] + "\n" + PILOT_LINK_HEDGED
    assert scorer.score_case(case, CASE_SET["defaults"], answer).penalties == []


def test_cli_accepts_the_case_set(tmp_path):
    answers = tmp_path / "answers.jsonl"
    answers.write_text("\n".join(json.dumps({"case_id": cid, "answer": good}, ensure_ascii=False)
                                 for cid, (good, _) in ANSWERS.items()), encoding="utf-8")
    code = scorer.main(argparse.Namespace(case_set=str(ROOT / "config" / "unreal_correctness_eval_cases.json"),
                                          answers=str(answers), answers_dir="", case_id=[], threshold=0.75,
                                          print_prompts=False, verbose=False))
    assert code == 0


def test_engine_regressions_record_version_and_source_locations():
    regressions = [c for c in CASES.values() if "harness_regression" in c["category"]]
    assert len(regressions) == 7
    for case in regressions:
        assert case["engineVersion"] == "5.7"
        assert re.search(r"\.(?:cpp|h):\d+", case["origin"])
