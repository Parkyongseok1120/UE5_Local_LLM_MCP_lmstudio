from __future__ import annotations

import pytest

from agent_orchestrator import classify_task  # noqa: E402


# Realistic Korean prompts — must not misclassify as write-enabled edit/compile_fix.
READ_ONLY_CASES = [
    ("현재 프로젝트 시네마틱 시스템 분석해줘", "cpp_analysis"),
    ("시네마틱 시스템 구조와 작동 방식 설명", "inspect_only"),
    ("프로젝트 전체 구조 리뷰해줘", "inspect_only"),
    ("코드 리뷰만 해줘 수정하지 말", "inspect_only"),
    ("분석만 해줘 MyActor.cpp", "inspect_only"),
    ("설명만 해줘 GameMode 흐름", "inspect_only"),
    ("계획만 세워줘 리팩터링", "inspect_only"),
    ("런타임 오류 원인 분석", "inspect_only"),
    ("PIE에서 크래시 나는 문제", "runtime_debug"),
    ("UHT generated.h 관련 컴파일 오류", "compile_fix"),
    ("C1083 include missing", "compile_fix"),
    ("시퀀서 위치 유지 코드 시안 보여줘", "code_sketch"),
    ("예시 코드만 보여줘", "code_sketch"),
    ("에러 로그 분석해줘", "inspect_only"),
    ("오류 메시지 설명해줘", "inspect_only"),
    ("생성", "inspect_only"),
    ("만들어줘", "inspect_only"),
]

WRITE_CASES = [
    ("MyActor.h에 Stop 기능 구현", "edit"),
    ("C1083 수정해줘 include 추가", "compile_fix"),
    ("새 Actor 클래스 생성 MyFoo.h", "edit"),
    ("GameMode.cpp 패치해줘", "edit"),
]


@pytest.mark.parametrize("prompt,expected", READ_ONLY_CASES + WRITE_CASES)
def test_korean_classification_corpus(prompt: str, expected: str) -> None:
    assert classify_task(prompt, "auto") == expected
