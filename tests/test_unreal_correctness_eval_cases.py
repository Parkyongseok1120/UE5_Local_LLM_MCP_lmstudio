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


def test_cli_accepts_the_case_set(tmp_path):
    answers = tmp_path / "answers.jsonl"
    answers.write_text("\n".join(json.dumps({"case_id": cid, "answer": good}, ensure_ascii=False)
                                 for cid, (good, _) in ANSWERS.items()), encoding="utf-8")
    code = scorer.main(argparse.Namespace(case_set=str(ROOT / "config" / "unreal_correctness_eval_cases.json"),
                                          answers=str(answers), answers_dir="", case_id=[], threshold=0.75,
                                          print_prompts=False, verbose=False))
    assert code == 0
