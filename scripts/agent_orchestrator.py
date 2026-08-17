#!/usr/bin/env python
"""Small planner/executor/verifier for Unreal agent tasks (Phase 14)."""

from __future__ import annotations

import json
import os
import re
import hashlib
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskKind = Literal[
    "answer_only",
    "project_control",
    "inspect_only",
    "cpp_analysis",
    "code_sketch",
    "edit",
    "compile_fix",
    "refactor",
    "runtime_debug",
]


@dataclass(frozen=True)
class ProjectControlIntent:
    """A bounded parse of active-project control language.

    ``matched`` says that a control speech act was present.  ``pure_control``
    is intentionally separate so a switch-and-work request cannot be consumed
    by the control plane and silently lose the user's remaining objective.
    """

    matched: bool = False
    operation: Literal["status", "select", "clear", "noop"] = "noop"
    speech_act: Literal["query", "command"] = "command"
    negated: bool = False
    target_kind: Literal["path", "name", "none"] = "none"
    target: str = ""
    pure_control: bool = False
    remaining_request: str = ""
EditStrategy = Literal[
    "no_edit",
    "new_file",
    "full_rewrite_small",
    "exact_patch",
    "line_range_patch",
]

COMPILE_MARKERS = (
    "c1083", "lnk2019", "uht", "generated.h", "build.cs", "compile error",
    "build error", "build failure", "fails to build", "does not build",
    "undefined", "unresolved", "missing module", "signature mismatch",
    "cpp_function_signature_mismatch", "declaration", "definition",
    "빌드 오류", "빌드오류", "컴파일 오류", "컴파일오류",
)
COMPILE_CONTEXT_MARKERS = (
    "compile", "build", "link", "uht", "c1083", "lnk2019", "generated.h", "build.cs",
    "빌드", "컴파일", "undefined", "unresolved",
)
COMPILE_FIX_GOAL_PATTERNS = (
    r"\b(?:fix|repair|correct|patch).{0,100}\b(?:compil(?:e|es|ed|ing)?|build(?:s|ing)?)\b",
    r"\b(?:compil(?:e|es|ed|ing)?|build(?:s|ing)?).{0,100}\b(?:fix|repair|correct|patch)\b",
    r"\b(?:until|so\s+that).{0,40}\b(?:it\s+)?(?:compil(?:e|es)|builds?)\b",
    r"(?:컴파일|빌드).{0,40}(?:성공|될\s*때까지|되도록|통과)",
    r"(?:고쳐|수정|패치).{0,80}(?:컴파일|빌드)",
)
BROAD_ERROR_MARKERS = ("에러", "오류", "error", "failure", "failed")
READ_ONLY_OVERRIDE_MARKERS = (
    "수정하지 말", "분석만", "설명만", "계획만",
    "찾기만하고", "보고만", "수정 없이", "파일 수정 없이",
    "don't edit", "do not edit", "don't fix", "do not fix", "dont fix",
    "read only", "no edits", "no fixes", "analysis only", "report only",
)
# Negations that still contain write verbs ("수정", "fix") and must not count as write intent.
NEGATED_WRITE_PATTERNS = (
    r"\b(?:do\s+not|don't|dont)\s+(?:fix|edit|patch|change|modify|write)\b",
    r"\b(?:no|without)\s+(?:fixes|edits|patches|modifications)\b",
    r"\bfind\s+bugs?\s+only\b",
    r"수정은\s*하(?:지\s*)?마",
    r"수정을\s*하(?:지\s*)?마",
    r"수정하지\s*마",
    r"고치지\s*마",
    r"고치지\s*말",
    r"패치하지\s*마",
    r"패치하지\s*말",
    r"찾기만\s*하",
    r"찾기만하고",
    r"찾아만\s*하",
    r"보고만\s*하",
)
CREATE_TARGET_MARKERS = (
    ".h", ".cpp", ".cs", "class ", "component", "subsystem", "actor",
    "클래스", "컴포넌트", "서브시스템", "액터", "파일",
)
REFACTOR_MARKERS = ("refactor", "r0", "r1", "r2", "r3", "r4", "move class", "extract")
RUNTIME_MARKERS = (
    "pie", "runtime", "gamemode", "input mapping", "crash", "assert", "log",
    # Sequencer / state-preservation behavior questions are runtime-behavior, not
    # codegen: route them to log/evidence-first debugging instead of a quick patch.
    "sequencer", "levelsequence", "level sequence", "completion mode", "restore state",
    "tick order", "tickgroup", "tick group",
    "시퀀서", "레벨 시퀀스", "상태 보존", "위치 유지", "되돌아", "되돌아감", "틱 순서", "틱 그룹",
)
REVIEW_MARKERS = (
    "review", "inventory", "audit", "findings", "architecture review",
    "리뷰", "코드리뷰", "코드 리뷰", "프로젝트 리뷰", "구조 리뷰",
    "전체 프로젝트", "전체 구조", "개선사항", "부족한", "문제점",
)
ANALYSIS_MARKERS = (
    "analyze", "analysis", "system analysis", "architecture analysis",
    "explain structure", "how it works", "current system",
    "분석", "구조 분석", "시스템 분석", "작동 방식", "동작 방식",
    "전체 동작", "구조 설명", "현재 시스템", "시네마틱",
)
WRITE_INTENT_MARKERS = (
    "implement", "improve", "fix", "patch", "create", "add ", "write ", "generate ",
    "\uac1c\uc120",
    "구현", "수정", "고쳐", "추가", "생성", "만들", "패치",
)
CONTINUATION_REQUEST_RE = re.compile(
    r"^(?:please\s+)?(?:continue|keep\s+going|go\s+on|proceed|resume)(?:\s+please)?[.!?\s]*$|"
    r"^(?:계속(?:\s*해|\s*해주세요|\s*진행해|\s*진행해주세요)?|"
    r"진행해(?:줘|주세요)?|이어서(?:\s*해|\s*해주세요)?|"
    r"이어가(?:줘|주세요)?|ㅇㅇ|응|네)[.!?\s]*$",
    re.IGNORECASE,
)
NEGATED_REFACTOR_PATTERNS = (
    r"\b(?:no|not|without)\s+(?:cross[- ]file\s+)?refactor(?:ing)?\b",
    r"\bdo\s+not\s+refactor\b",
    r"\ub9ac\ud329\ud130\ub9c1(?:\uc740|\uc744)?\s*(?:\ud558\uc9c0\s*\ub9d0|\uc81c\uc678|\uc5c6\uc774)",
    r"\ub9ac\ud329\ud1a0\ub9c1(?:\uc740|\uc744)?\s*(?:\ud558\uc9c0\s*\ub9d0|\uc81c\uc678|\uc5c6\uc774)",
)
PLAN_REQUEST_MARKERS = (
    "implementation plan", "implementation roadmap", "make a plan", "draft a plan",
    "plan this", "work plan", "change plan", "refactor plan",
    "구현 계획", "작업 계획", "수정 계획", "변경 계획", "리팩터링 계획", "리팩토링 계획",
    "계획 세워", "계획 세우", "계획 짜", "계획 작성",
)
PLAN_AND_EXECUTE_PATTERNS = (
    r"(?:plan|roadmap).{0,40}(?:and|then).{0,20}(?:implement|fix|apply|execute|patch|write|create)",
    r"계획.{0,20}(?:세우고|세운 뒤|세운 후|짜고|작성하고|작성한 뒤|작성한 후).{0,20}(?:구현|수정|고쳐|적용|실행|패치|생성)",
    r"계획(?:대로|에 따라).{0,20}(?:구현|수정|고쳐|적용|실행|패치|생성)",
    r"계획.{0,20}(?:뿐만 아니라|말고).{0,20}(?:구현|수정|고쳐|적용|실행|패치|생성)",
)
ASSET_ANALYSIS_MARKERS = (
    "shader", "usf", "ush", "hlsl", "material", "material node",
    "material graph", "material porting", "blueprint graph", "blueprint verification", "function call", "variable", "pin link", "screenshot",
    "셰이더", "쉐이더", "머티리얼", "머티리얼 노드", "머티리얼 그래프",
    "블루프린트", "블루프린트 그래프", "블루프린트 검증", "핀 연결", "노드 연결",
)
CODEGEN_MARKERS = (
    "codegen", "code generation", "generate code",
    "코드 생성", "코드생성", "클래스 생성", "컴포넌트 생성", "서브시스템 생성",
)
# Explicit "draft/sketch/example code" intent. Chat requests that match these are
# routed to the evidence-first code_sketch task (no writes, symbol verification
# required) instead of falling through to a write-enabled edit task.
SKETCH_MARKERS = (
    "sketch", "draft", "example code", "sample code", "show me code",
    "show me the code", "pseudocode", "pseudo code", "mock up", "mockup",
    "시안", "초안", "예시 코드", "예시코드", "샘플 코드", "샘플코드",
    "코드 예시", "코드예시", "코드 샘플", "코드 초안", "코드초안",
    "대략적인 코드", "간단한 코드 예", "코드 스케치",
    "코드로 짜면 어떻게 돼", "코드로 보여줘", "구현 예제", "구현 예시",
    "파일에 적용하지 말", "파일 수정 없이", "채팅창에만", "코드만 작성해줘",
    "대략 어떻게 구현할지", "c++로 표현해줘", "apply하지 말", "draft only",
)
# Protocol/tool identifiers can occur inside an otherwise concrete implementation
# request.  Their internal word "sketch" describes the validator, not the user's
# requested outcome, so mask them before applying the natural-language sketch
# heuristic.
SKETCH_PROTOCOL_IDENTIFIERS = (
    "unreal_code_sketch_claim_validate",
    "code_sketch_claim_validate",
)
from rag_modes import ASSET_METADATA_MODES  # single source of truth
from tool_policy import tool_sequence_for_task

ASSET_METADATA_TOOL_POLICY = tool_sequence_for_task("asset_metadata_inspect")
PROJECT_SOURCE_ANALYSIS_POLICY_KEY = "project_source_analysis"
CPP_REVIEW_TOOL_POLICY = tool_sequence_for_task(PROJECT_SOURCE_ANALYSIS_POLICY_KEY) or [
    "unreal_get_active_project",
    "unreal_symbol_lookup",
    "search_files",
    "read_file",
    "unreal_rag_search",
    "unreal_review_claim_validate",
    "answer_with_evidence",
]
API_MARKERS = ("what is", "how does", "api", "lookup", "documentation", "explain")
CPP_ANALYSIS_MARKERS = (
    "cpp", "c++", ".h", ".cpp", "source", "class", "function", "component", "subsystem",
    "current project", "existing system", "project code",
    "\uD604\uC7AC \uD504\uB85C\uC81D\uD2B8", "\uD604\uC7AC \uC2DC\uC2A4\uD15C", "\uD504\uB85C\uC81D\uD2B8 \uCF54\uB4DC",
    "\uC18C\uC2A4 \uCF54\uB4DC", "\uD074\uB798\uC2A4", "\uD568\uC218",
)
PROJECT_SPECIFIC_MARKERS = (
    "current", "existing", "this code", "project",
    "\uD604\uC7AC", "\uAE30\uC874", "\uC774 \uCF54\uB4DC", "\uD504\uB85C\uC81D\uD2B8",
    "fix this", "improve this", "\uACE0\uCE58", "\uAC1C\uC120", "\uB9AC\uD329\uD130\uB9C1",
)

# Project selection/status is a control-plane concern, not a source-analysis
# task.  Keep this deliberately narrow: an ordinary request that merely
# mentions a project must still reach the normal evidence planner.
PROJECT_CONTROL_PATTERNS = (
    re.compile(
        r"\b(?:set|select|switch|change|choose|activate|use)\s+"
        r"(?:an?\s+|the\s+)?(?:active\s+|current\s+)?project\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what(?:'s|\s+is)|which|get|show|tell\s+me|display|check)\s+"
        r"(?:is\s+)?(?:the\s+)?(?:active|current)\s+project"
        r"(?:\s+(?:path|name|status))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:active|current)\s+project\s+(?:status|path|name)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:현재|활성)\s*프로젝트\s*(?:의\s*)?"
        r"(?:상태|경로|이름)(?:\s*(?:을|를)?\s*(?:알려|보여|확인|조회|말해))?",
        re.IGNORECASE,
    ),
    re.compile(r"(?:현재|활성)\s*프로젝트\s*(?:가|는)?\s*(?:뭐|무엇|어디)", re.IGNORECASE),
    re.compile(
        r"(?:현재|활성)\s*프로젝트\s*(?:를|을)?\s*(?:확인|조회|알려|보여)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:현재|활성)?\s*프로젝트\s*(?:를|을)?\s*"
        r"(?:지정|설정|선택|전환|변경|바꿔|바꾸|교체)(?:\s*해|해줘|해주세요)?",
        re.IGNORECASE,
    ),
)
PROJECT_CONTROL_SELECTION_PATTERNS = (
    re.compile(
        r"\b(?:set|select|switch|change|choose|activate|use|clear|unset|remove)\s+"
        r"(?:an?\s+|the\s+)?(?:active\s+|current\s+)?project\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:현재|활성)?\s*프로젝트\s*(?:를|을)?\s*"
        r"(?:지정|설정|선택|전환|변경|바꿔|바꾸|교체|해제|초기화)",
        re.IGNORECASE,
    ),
)
PROJECT_CONTROL_CLEAR_PATTERNS = (
    re.compile(r"\b(?:clear|unset|remove)\s+(?:the\s+)?active\s+project\b", re.IGNORECASE),
    re.compile(r"(?:현재|활성)?\s*프로젝트\s*(?:를|을)?\s*(?:해제|초기화|지워)", re.IGNORECASE),
)
PROJECT_CONTROL_WORK_MARKERS = (
    "analyze", "analysis", "inspect", "review", "audit", "architecture",
    "source", "code", "class", "function", "component", "subsystem",
    "build", "compile", "test", "implement", "fix", "refactor",
    "분석", "검토", "리뷰", "구조", "소스", "코드", "클래스", "함수",
    "컴포넌트", "서브시스템", "빌드", "컴파일", "테스트", "구현", "수정",
    "고쳐", "리팩터링", "리팩토링",
)
_PROJECT_CONTROL_PATH_RE = re.compile(
    r"(?:[\"'](?P<quoted>[^\"'\r\n]+?\.uproject)[\"']|"
    r"(?P<bare>(?:(?:[A-Za-z]:[\\/])|/|~[\\/])[^\r\n,;]*?\.uproject))",
    re.IGNORECASE,
)

# Project-control targets are deliberately bounded.  Quoting is required for
# names containing whitespace, while absolute .uproject paths may keep spaces.
# This prevents ordinary phrases such as "use project settings to fix input"
# from being consumed as a project name and blocking the real source task.
_PROJECT_CONTROL_TARGET_ATOM = (
    r'(?:"[^"\r\n]+"|\'[^\'\r\n]+\'|'
    r'(?:(?:[A-Za-z]:[\\/])|/|~[\\/])[^\r\n,;]*?\.uproject|'
    r'[^\s\r\n,;.!?"\']+?)'
)
_PROJECT_CONTROL_ABSOLUTE_UPROJECT_RE = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|/|~[\\/])[^\r\n]*\.uproject",
    re.IGNORECASE,
)
_PROJECT_CONTROL_KOREAN_COMMAND_VERB = (
    r"(?:지정|설정|선택|전환|변경|바꿔|바꾸|교체)"
    r"(?:\s*(?:해주세요|해줘|해|주세요|줘))?"
)
_PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY = (
    r"(?=\s*(?:(?:고|하고)(?=\s|[,;:])|그리고|그다음|그\s*후|[,;:]|[.!?]|$))"
)
_PROJECT_CONTROL_ENGLISH_COMMAND_PREFIX = (
    r"(?:then\s+)?(?:please\s+|(?:(?:can|could|would|will)\s+you\s+))?"
)
_PROJECT_CONTROL_ENGLISH_COMMAND_BOUNDARY = (
    r"(?=\s*(?:(?:[,;:]\s*)?(?:and|then)\b|[,;:]|[.!?]|$))"
)

_PROJECT_CONTROL_STATUS_RE = re.compile(
    r"(?:"
    r"(?:지금|현재)?\s*(?:활성|작업)?\s*프로젝트(?:\s*(?:가|는|의|를|을))?\s*"
    r"(?:뭐(?:야|지)?|무엇(?:이야|인가)?|어디(?:야|지)?|상태|경로|이름|확인|조회|알려|보여)"
    r"|현재\s*작업\s*프로젝트\s*(?:뭐(?:야|지)?|무엇(?:이야|인가)?|어디(?:야|지)?)"
    r"|(?:what(?:'s|\s+is)|which|show|tell\s+me|check|get)\s+"
    r"(?:the\s+)?(?:active|current)\s+project(?:\s+(?:status|path|name))?"
    r"|(?:active|current)\s+project\s+(?:status|path|name)"
    r")",
    re.IGNORECASE,
)
_PROJECT_CONTROL_CLEAR_RE = re.compile(
    r"(?:"
    r"(?:현재|활성)?\s*프로젝트\s*(?:를|을)?\s*(?:해제|초기화|지워)"
    r"|(?:clear|unset|remove)\s+(?:the\s+)?(?:active|current)\s+project"
    r")",
    re.IGNORECASE,
)
_PROJECT_CONTROL_NEGATED_RES = (
    re.compile(
        rf"(?:그럼\s*)?(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s*로\s*"
        r"(?:프로젝트(?:를|을)?\s*)?"
        r"(?:(?:지정|설정|선택|전환|변경|교체)\s*하?지|바꾸\s*지)\s*(?:마|말)"
        + _PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:do\s+not|don't|dont|never)\s+"
        r"(?:(?:set|select|switch|change)\s+(?:(?:the\s+)?(?:active|current)\s+)?project|"
        r"use\s+(?:the\s+)?(?:active|current)\s+project)\s+(?:to\s+)?"
        rf"(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})"
        + _PROJECT_CONTROL_ENGLISH_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:do\s+not|don't|dont|never)\s+(?:set|select|choose|activate|use)\s+"
        rf"(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s+(?:as\s+)?(?:the\s+)?"
        r"(?:(?:active|current)\s+)?project\b"
        + _PROJECT_CONTROL_ENGLISH_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
)
_PROJECT_CONTROL_TARGET_QUERY_RE = re.compile(
    r"(?P<target>[\"']?[^\r\n,;?]+?[\"']?)\s*로\s*"
    r"(?:프로젝트(?:가|는)?\s*)?(?:지정|설정|선택)(?:돼|되어)\s*"
    r"(?:있어|있는지|있나요|있습니까)?\s*\??",
    re.IGNORECASE,
)
_PROJECT_CONTROL_ENGLISH_TARGET_QUERY_RE = re.compile(
    r"(?:is|was)\s+(?P<target>[\"']?[^\r\n,;?]+?[\"']?)\s+"
    r"(?:the\s+)?(?:active|current)\s+project\s*\??",
    re.IGNORECASE,
)
_PROJECT_CONTROL_SELECT_RES = (
    # 프로젝트 <name/path>로 바꾸고 <remaining request>
    re.compile(
        r"(?:그럼\s*)?(?:현재\s*|활성\s*)?프로젝트\s+"
        rf"(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s*(?:로|을|를)?\s*"
        + _PROJECT_CONTROL_KOREAN_COMMAND_VERB
        + _PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    # <name/path>를 프로젝트로 지정해
    re.compile(
        rf"(?:그럼\s*)?(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s*(?:을|를)\s*"
        r"프로젝트(?:로)?\s*(?:지정|설정|선택|전환|변경|바꿔|바꾸|교체)"
        r"(?:\s*(?:해주세요|해줘|해|주세요|줘))?"
        + _PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    # <name/path>로 프로젝트 바꿔
    re.compile(
        rf"(?:그럼\s*)?(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s*로\s*"
        r"(?:현재\s*|활성\s*)?프로젝트\s*"
        + _PROJECT_CONTROL_KOREAN_COMMAND_VERB
        + _PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    re.compile(
        _PROJECT_CONTROL_ENGLISH_COMMAND_PREFIX
        + r"(?:(?:set|select|switch|change|choose|activate)\s+"
        r"(?:(?:the|an?)\s+)?(?:(?:active|current)\s+)?project|"
        r"use\s+(?:the\s+)?(?:active|current)\s+project)\s+"
        rf"(?:to\s+)?(?!and\b|then\b)(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})"
        + _PROJECT_CONTROL_ENGLISH_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
    re.compile(
        _PROJECT_CONTROL_ENGLISH_COMMAND_PREFIX
        + r"(?:set|select|choose|activate|use)\s+"
        rf"(?P<target>{_PROJECT_CONTROL_TARGET_ATOM})\s+(?:as\s+)?(?:the\s+)?"
        r"(?:(?:active|current)\s+)?project\b"
        + _PROJECT_CONTROL_ENGLISH_COMMAND_BOUNDARY,
        re.IGNORECASE,
    ),
)
_PROJECT_CONTROL_TARGETLESS_SELECT_RE = re.compile(
    r"(?:현재\s*|활성\s*)?프로젝트\s*(?:를|을)?\s*"
    + _PROJECT_CONTROL_KOREAN_COMMAND_VERB
    + _PROJECT_CONTROL_KOREAN_COMMAND_BOUNDARY,
    re.IGNORECASE,
)
_PROJECT_CONTROL_LEADING_CONNECTIVE_RE = re.compile(
    r"^(?:\s|[,;:.!?])+|^(?:(?:고|하고)(?=\s|[,;:])|그리고|그다음|그\s*후|and\b|then\b)\s*",
    re.IGNORECASE,
)
_PROJECT_CONTROL_POLITE_TAIL_RE = re.compile(
    r"^(?:(?:을|를)\s*)?(?:"
    r"알려(?:줘|주세요)?|보여(?:줘|주세요)?|말해(?:줘|주세요)?|"
    r"확인(?:해줘|해주세요|해)?|조회(?:해줘|해주세요|해)?|"
    r"해줘|해주세요|줘|주세요"
    r")?[.!?\s]*$",
    re.IGNORECASE,
)


def normalize_project_name(value: str) -> str:
    """Return a comparison-only project identity without altering real paths."""

    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    normalized = re.sub(r"\.uproject$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[\s_-]+", "", normalized)
    return normalized.casefold()


_ECMASCRIPT_TRIM_CHARS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def normalize_objective_for_hash(value: str) -> str:
    """Match ECMAScript String.trim for the cross-runtime intent protocol."""

    return str(value or "").strip(_ECMASCRIPT_TRIM_CHARS)


def objective_hash(value: str) -> str:
    return hashlib.sha256(normalize_objective_for_hash(value).encode("utf-8")).hexdigest()


def _clean_project_control_target(value: str) -> str:
    target = str(value or "").strip().strip("\"'").strip()
    target = re.sub(r"^(?:그럼|then)\s+", "", target, flags=re.IGNORECASE)
    return target.rstrip(".,;:)]}")


def _remaining_after_control_match(request: str, end: int) -> str:
    remaining = str(request or "")[end:]
    previous = None
    while previous != remaining:
        previous = remaining
        remaining = _PROJECT_CONTROL_LEADING_CONNECTIVE_RE.sub("", remaining, count=1)
    remaining = remaining.strip()
    if _PROJECT_CONTROL_POLITE_TAIL_RE.fullmatch(remaining):
        return ""
    return remaining


def _project_target_kind(target: str) -> Literal["path", "name", "none"]:
    if not target:
        return "none"
    path_match = _PROJECT_CONTROL_PATH_RE.fullmatch(target.strip())
    if path_match:
        return "path"
    if _PROJECT_CONTROL_ABSOLUTE_UPROJECT_RE.fullmatch(target.strip()):
        return "path"
    candidate = Path(target).expanduser()
    if candidate.suffix.casefold() == ".uproject" and candidate.is_absolute():
        return "path"
    return "name"


def parse_project_control_intent(request: str) -> ProjectControlIntent:
    """Parse project status/selection without consuming an attached work goal."""

    source = str(request or "").strip()
    if not source:
        return ProjectControlIntent()

    status_match = _PROJECT_CONTROL_STATUS_RE.match(source)
    if status_match:
        remaining = _remaining_after_control_match(source, status_match.end())
        return ProjectControlIntent(
            matched=True,
            operation="status",
            speech_act="query",
            target_kind="none",
            pure_control=not remaining,
            remaining_request=remaining,
        )

    target_query = _PROJECT_CONTROL_TARGET_QUERY_RE.match(source)
    if not target_query:
        target_query = _PROJECT_CONTROL_ENGLISH_TARGET_QUERY_RE.match(source)
    if target_query:
        target = _clean_project_control_target(target_query.group("target"))
        remaining = _remaining_after_control_match(source, target_query.end())
        return ProjectControlIntent(
            matched=True,
            operation="status",
            speech_act="query",
            target_kind=_project_target_kind(target),
            target=target,
            pure_control=not remaining,
            remaining_request=remaining,
        )

    clear_match = _PROJECT_CONTROL_CLEAR_RE.match(source)
    if clear_match:
        remaining = _remaining_after_control_match(source, clear_match.end())
        return ProjectControlIntent(
            matched=True,
            operation="clear",
            speech_act="command",
            target_kind="none",
            pure_control=not remaining,
            remaining_request=remaining,
        )

    for pattern in _PROJECT_CONTROL_NEGATED_RES:
        match = pattern.match(source)
        if not match:
            continue
        target = _clean_project_control_target(match.group("target"))
        remaining = _remaining_after_control_match(source, match.end())
        return ProjectControlIntent(
            matched=True,
            operation="noop",
            speech_act="command",
            negated=True,
            target_kind=_project_target_kind(target),
            target=target,
            pure_control=not remaining,
            remaining_request=remaining,
        )

    for pattern in _PROJECT_CONTROL_SELECT_RES:
        match = pattern.match(source)
        if not match:
            continue
        target = _clean_project_control_target(match.group("target"))
        remaining = _remaining_after_control_match(source, match.end())
        return ProjectControlIntent(
            matched=True,
            operation="select",
            speech_act="command",
            target_kind=_project_target_kind(target),
            target=target,
            pure_control=not remaining,
            remaining_request=remaining,
        )

    targetless = _PROJECT_CONTROL_TARGETLESS_SELECT_RE.match(source)
    if targetless:
        remaining = _remaining_after_control_match(source, targetless.end())
        return ProjectControlIntent(
            matched=True,
            operation="select",
            speech_act="command",
            target_kind="none",
            pure_control=not remaining,
            remaining_request=remaining,
        )

    return ProjectControlIntent()


@dataclass
class EvidencePlan:
    task_kind: TaskKind
    rag_modes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    files_to_read: list[str] = field(default_factory=list)
    symbols_to_scan: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    writes_allowed: bool = False
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.writes_allowed and self.task_kind in {
            "cpp_analysis", "inspect_only", "project_review", "answer_only"
        }:
            payload["recommendedValidators"] = payload.pop("gates", [])
        return payload


@dataclass
class AgentPlan:
    request: str
    task_kind: TaskKind
    evidence: EvidencePlan
    edit_strategy: EditStrategy
    tool_policy: list[str] = field(default_factory=list)
    suggested_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    project_context: dict[str, Any] = field(default_factory=dict)
    write_gate: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    retry_policy: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error_route: dict[str, Any] = field(default_factory=dict)
    module_hints: list[dict[str, Any]] = field(default_factory=list)
    symbol_graph_hints: list[dict[str, Any]] = field(default_factory=list)
    refactor_manager: dict[str, Any] = field(default_factory=dict)
    domain_kind: str = "generic"
    domain_profile: dict[str, Any] = field(default_factory=dict)
    plan_slices: list[dict[str, Any]] = field(default_factory=list)
    informational_plan_slices: list[dict[str, Any]] = field(default_factory=list)
    executable_plan_slices: list[dict[str, Any]] = field(default_factory=list)
    fix_evidence: dict[str, Any] = field(default_factory=dict)
    ambiguity_gate: dict[str, Any] = field(default_factory=dict)
    feature_intent: dict[str, Any] = field(default_factory=dict)
    feature_completion_audit: dict[str, Any] = field(default_factory=dict)
    source_evidence: dict[str, Any] = field(default_factory=dict)
    tool_discovery_candidates: list[dict[str, Any]] = field(default_factory=list)
    plan_graph_delta: dict[str, Any] = field(default_factory=dict)
    orchestration: dict[str, Any] = field(default_factory=dict)
    request_intent: dict[str, Any] = field(default_factory=dict)
    project_control: dict[str, Any] = field(default_factory=dict)
    resolved_targets: list[dict[str, Any]] = field(default_factory=list)
    semantic_ambiguity: dict[str, Any] = field(default_factory=dict)
    inspection_contract: dict[str, Any] = field(default_factory=dict)
    original_objective: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "request": self.request,
            "objective": self.original_objective or self.request,
            "taskKind": self.task_kind,
            "evidencePlan": self.evidence.to_dict(),
            "editStrategy": self.edit_strategy,
            "toolPolicy": self.tool_policy,
            "suggestedToolCalls": self.suggested_tool_calls,
            "projectContext": self.project_context,
            "writeGate": self.write_gate,
            "checkpoints": self.checkpoints,
            "stopConditions": self.stop_conditions,
            "retryPolicy": self.retry_policy,
            "notes": self.notes,
            "domainKind": self.domain_kind,
            "requestIntent": self.request_intent,
        }
        if self.domain_profile:
            payload["domainProfile"] = self.domain_profile
        if self.inspection_contract:
            payload["inspectionContract"] = self.inspection_contract
        if self.informational_plan_slices:
            payload["informationalPlanSlices"] = self.informational_plan_slices
        if self.executable_plan_slices:
            payload["executablePlanSlices"] = self.executable_plan_slices
        if self.plan_slices:
            payload["planSlices"] = self.plan_slices
        if self.fix_evidence:
            payload["fixEvidence"] = self.fix_evidence
        if self.ambiguity_gate:
            payload["ambiguityGate"] = self.ambiguity_gate
        if self.feature_intent:
            payload["featureIntent"] = self.feature_intent
        if self.feature_completion_audit:
            payload["featureCompletionAudit"] = self.feature_completion_audit
        if self.source_evidence:
            payload["sourceEvidence"] = self.source_evidence
        if self.error_route:
            payload["errorRoute"] = self.error_route
        if self.module_hints:
            payload["moduleHints"] = self.module_hints
        if self.symbol_graph_hints:
            payload["symbolGraphHints"] = self.symbol_graph_hints
        if self.refactor_manager:
            payload["refactorManager"] = self.refactor_manager
        if self.tool_discovery_candidates:
            payload["toolDiscoveryCandidates"] = self.tool_discovery_candidates
        if self.plan_graph_delta:
            payload["planGraphDelta"] = self.plan_graph_delta
        if self.orchestration:
            payload["orchestration"] = self.orchestration
        if self.project_control:
            payload["projectControl"] = self.project_control
        if self.resolved_targets:
            payload["resolvedTargets"] = self.resolved_targets
        if self.semantic_ambiguity:
            payload["semanticAmbiguity"] = self.semantic_ambiguity
        return payload


def build_orchestration_decision(
    *,
    task_kind: TaskKind,
    file_count_hint: int,
    domain_kind: str,
    architecture_required: bool,
    policy: dict[str, Any],
    profile_name: str,
    completion_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a bounded reasoning/validation route for the active model profile."""
    write_task = task_kind in {"edit", "compile_fix", "refactor"}
    architecture_gate_required = (
        task_kind == "refactor"
        or architecture_required
        or domain_kind in {"architecture", "subsystem", "replication"}
    )
    high_risk = (
        task_kind == "refactor"
        or file_count_hint > 2
        or architecture_gate_required
    )
    if architecture_gate_required:
        risk_tier = "high"
        strategy = "architecture_first"
    elif high_risk:
        risk_tier = "high"
        strategy = "staged_guarded"
    elif write_task:
        risk_tier = "medium"
        strategy = "guarded"
    else:
        risk_tier = "low"
        strategy = "evidence_first"

    required_before_write: list[str] = []
    if write_task and architecture_gate_required:
        required_before_write.append("unreal_architecture_reasoning")
    if write_task:
        required_before_write.append("unreal_code_sketch_claim_validate")
    if task_kind == "refactor":
        required_before_write.append("unreal_semantic_refactor_guard")

    validation_stages = ["direct_source_evidence"]
    if write_task:
        validation_stages.extend(
            [
                "static_validate_project",
                "build_unreal_project",
                "automation_if_declared_or_required_by_server_control",
            ]
        )
    if high_risk:
        validation_stages.append("targeted_regression")

    decision = {
        "riskTier": risk_tier,
        "strategy": strategy,
        "profile": profile_name,
        "targetTier": str(policy.get("targetTier") or ""),
        "promptContract": str(policy.get("promptContract") or ""),
        "requiredBeforeWrite": required_before_write,
        "validationStages": validation_stages,
        "roleContract": {
            "planner": {
                "mayWrite": False,
                "output": "bounded targets, invariants, evidence gaps, and validation plan",
            },
            "implementer": {
                "mayWrite": write_task,
                "startsAfter": required_before_write,
                "output": "minimal authorized patch or atomic bundle",
            },
            "verifier": {
                "mayWrite": False,
                "mustUseFreshPostWriteEvidence": True,
                "mustNotAcceptImplementerSelfReport": True,
                "requiredEvidence": validation_stages,
            },
        },
        "escalationTriggers": [
            "more_than_two_files",
            "ownership_or_lifetime_ambiguity",
            "new_cross_module_dependency",
            "same_error_repeated",
            "runtime_claim_without_runtime_evidence",
        ],
        "toolBudget": {
            "defaultTopK": int(policy.get("defaultTopK") or 0),
            "maxFilesPerEdit": int(policy.get("maxFilesPerEdit") or 0),
            "oneToolPerTurn": str(policy.get("mcpToolDiscipline") or "") == "one_tool_per_turn",
        },
        "routingBoundary": (
            "This selects reasoning and tool phases for the currently loaded model. "
            "It does not prove that LM Studio has multiple models loaded or perform model switching."
        ),
    }
    if write_task and completion_contract:
        decision["completionContract"] = dict(completion_contract)
    return decision


def _insert_tool_before(policy: list[str], tool: str, anchors: tuple[str, ...]) -> None:
    if tool in policy:
        return
    positions = [policy.index(anchor) for anchor in anchors if anchor in policy]
    policy.insert(min(positions) if positions else len(policy), tool)


def _has_negated_write_intent(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in NEGATED_WRITE_PATTERNS)


def _has_read_only_override(text: str) -> bool:
    """Recognize an actual read-only instruction, not its explicit rejection."""

    normalized = str(text or "")
    # "Do not stop at analysis/plan/report only" is an execution instruction.
    # A raw substring check used to see ``계획만`` inside that contrast and
    # incorrectly turn explicit implementation requests into inspect-only work.
    normalized = re.sub(
        r"(?:문서나\s*)?(?:계획|분석|설명|보고)만.{0,40}?그치지\s*말(?:고|라|아)?",
        " ",
        normalized,
    )
    normalized = re.sub(
        r"\b(?:do\s+not|don't|dont)\s+(?:only|just)\s+(?:plan|analy[sz]e|report|explain)\b",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    return any(marker in normalized for marker in READ_ONLY_OVERRIDE_MARKERS)


def _has_write_intent(text: str) -> bool:
    if _has_read_only_override(text):
        return False
    if _has_negated_write_intent(text):
        return False
    english_write = bool(
        re.search(
            r"\b(?:implement(?:s|ed|ing)?|improve(?:s|d|ing)?|fix(?:es|ed|ing)?|"
            r"patch(?:es|ed|ing)?|create(?:s|d|ing)?|add(?:s|ed|ing)?|"
            r"write(?:s|written|writing)?|generate(?:s|d|ing)?)\b",
            text,
        )
    )
    non_english_write = any(
        marker in text
        for marker in WRITE_INTENT_MARKERS
        if marker not in {
            "implement", "improve", "fix", "patch", "create", "add ",
            "write ", "generate ",
        }
    )
    if not english_write and not non_english_write:
        return False
    if any(m in text for m in ("생성", "만들")):
        return any(m in text for m in CREATE_TARGET_MARKERS)
    return True

def _has_refactor_intent(text: str) -> bool:
    if any(re.search(pattern, text) for pattern in NEGATED_REFACTOR_PATTERNS):
        return False
    return any(marker in text for marker in REFACTOR_MARKERS)



def _is_plan_only_request(text: str) -> bool:
    if not any(marker in text for marker in PLAN_REQUEST_MARKERS):
        return False
    return not any(re.search(pattern, text) for pattern in PLAN_AND_EXECUTE_PATTERNS)


def is_plan_only_request(request: str, mode: str = "auto") -> bool:
    """Expose the classifier's single plan-only predicate to lifecycle owners."""

    return _is_plan_only_request(f"{mode} {request}".lower())

def resolve_task_lifecycle_mode(
    plan_payload: dict[str, Any],
    request: str,
    mode: str = "auto",
) -> Literal["agent_edit", "read_only", "plan_only"]:
    """Choose task durability from execution intent, not a task-kind allow-list.

    ``plan_only`` is terminal by design, so it is reserved for an explicit
    request for a plan. Every other non-writing Agent plan may span evidence
    tools, compaction, retries, and a final synthesis handshake and therefore
    owns a durable ``read_only`` lifecycle. This keeps newly added task kinds
    from silently regressing to an immediately-completed task.
    """

    write_gate = plan_payload.get("writeGate")
    if isinstance(write_gate, dict) and write_gate.get("writesAllowed") is True:
        return "agent_edit"
    if mode == "plan_only" or is_plan_only_request(request, mode):
        return "plan_only"
    if str(plan_payload.get("taskKind") or "").strip().casefold() == "project_control":
        # Pure project control is normally returned before task_start. Keep the
        # fallback terminal if a caller bypasses that no-session handoff.
        return "plan_only"
    return "read_only"



def _is_compile_fix_request(text: str) -> bool:
    if any(m in text for m in COMPILE_MARKERS):
        return True
    if any(re.search(pattern, text) for pattern in COMPILE_FIX_GOAL_PATTERNS):
        return True
    if any(m in text for m in BROAD_ERROR_MARKERS):
        return any(m in text for m in COMPILE_CONTEXT_MARKERS)
    return False


def _is_feature_implementation_request(text: str) -> bool:
    """Treat build-and-fix language as acceptance when feature work is primary."""
    feature_markers = (
        "feature", "prototype", "finish the remaining", "complete the remaining",
        "implement the remaining", "support a complete", "add gameplay",
        "real implementation", "implementation and verification", "implement the roadmap",
        "gameplay roadmap", "original gameplay roadmap", "stages 0 through",
        "stage 0:", "stage 1:", "match loop", "lobby", "minigame", "bots",
    )
    concrete_compile_diagnostics = (
        "c1083", "lnk2019", "lnk2001", "uht error", "unresolved external symbol",
        "undefined reference to", "fatal error c", "error c2", "error lnk",
    )
    diagnostic_line = re.search(
        r"(?:\.h|\.cpp|\.cs)\s*\(?\d+(?::\d+)?\)?\s*:\s*(?:fatal\s+)?error\b",
        text,
    )
    return (
        any(marker in text for marker in feature_markers)
        and _has_write_intent(text)
        and "no new feature" not in text
        and not any(marker in text for marker in concrete_compile_diagnostics)
        and diagnostic_line is None
    )


def _is_runtime_symptom_analysis(text: str) -> bool:
    """Runtime bug/symptom analysis (not structure inventory)."""
    runtime_symptoms = (
        "bug", "버그", "crash", "assert", "wrong", "broken", "fail", "issue",
        "되돌아", "복원", "안됨", "안 됨", "문제",
    )
    value = str(text or "").casefold()
    if not value:
        return False
    # A crash is inherently runtime evidence. Other words such as GameMode,
    # log, assert, broken, and fail are common in long implementation/test
    # specifications, so a document-wide cross product creates false runtime
    # debug gates without an observed symptom or reproduction.
    if re.search(r"\bcrash(?:es|ed|ing)?\b", value):
        return True
    proximity = 120
    causal_runtime_markers = tuple(
        marker
        for marker in RUNTIME_MARKERS
        if marker not in {"gamemode", "input mapping", "assert", "log"}
    )
    runtime_positions = [
        (match.start(), marker)
        for marker in causal_runtime_markers
        for match in re.finditer(re.escape(marker.casefold()), value)
    ]
    symptom_positions = [
        (match.start(), marker)
        for marker in runtime_symptoms
        for match in re.finditer(re.escape(marker.casefold()), value)
    ]
    return any(
        abs(runtime_at - symptom_at) <= proximity
        for runtime_at, _runtime_marker in runtime_positions
        for symptom_at, _symptom_marker in symptom_positions
    )


def _is_project_specific(text: str) -> bool:
    return any(marker in text for marker in PROJECT_SPECIFIC_MARKERS)


def _has_sketch_intent(text: str) -> bool:
    natural_language = str(text or "")
    for identifier in SKETCH_PROTOCOL_IDENTIFIERS:
        natural_language = natural_language.replace(identifier, " ")
    return any(marker in natural_language for marker in SKETCH_MARKERS)


def is_project_control_request(request: str, mode: str = "auto") -> bool:
    """Return true only for a *pure* project-control speech act."""

    if str(mode or "").strip().casefold() == "project_control":
        return True
    intent = parse_project_control_intent(request)
    return intent.matched and intent.pure_control


def project_control_requests_selection(request: str) -> bool:
    """Return whether an explicit project-control request changes selection."""

    intent = parse_project_control_intent(request)
    return intent.matched and intent.operation == "select" and not intent.negated


def project_control_requests_clear(request: str) -> bool:
    """Return whether project control explicitly requests clearing the selection."""

    intent = parse_project_control_intent(request)
    return intent.matched and intent.operation == "clear" and not intent.negated


def project_control_project_path_hint(request: str) -> str:
    """Return the exact path target from the structured parse, if present."""

    intent = parse_project_control_intent(request)
    return intent.target if intent.target_kind == "path" else ""


def project_control_project_name_hint(request: str) -> str:
    """Return the exact name target from the structured parse, if present."""

    intent = parse_project_control_intent(request)
    return intent.target if intent.target_kind == "name" else ""


def classify_task(request: str, mode: str = "auto") -> TaskKind:
    text = f"{mode} {request}".lower()
    if is_project_control_request(request, mode):
        return "project_control"
    if mode in {"refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4"}:
        return "refactor"
    if mode in {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}:
        return "compile_fix"
    if mode in {"shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"}:
        return "inspect_only"
    if mode == "runtime_debug":
        return "edit" if _has_write_intent(request.lower()) else "runtime_debug"
    if mode in {"cpp_analysis", "code_analysis"}:
        return "cpp_analysis"
    if mode in {"review", "planning"}:
        return "inspect_only"
    if mode == "code_sketch":
        return "code_sketch"
    if mode == "api_lookup":
        return "answer_only"
    if _has_read_only_override(text) or _has_negated_write_intent(text):
        return "inspect_only"
    if _is_feature_implementation_request(text):
        return "edit"
    if _is_compile_fix_request(text):
        return "compile_fix"
    if _has_sketch_intent(text):
        return "code_sketch"
    if _has_refactor_intent(text):
        return "refactor"
    if _is_plan_only_request(text):
        return "inspect_only"
    if _is_runtime_symptom_analysis(text) and not _has_write_intent(text):
        return "runtime_debug"
    if any(m in text for m in ASSET_ANALYSIS_MARKERS) and not _has_write_intent(text):
        return "inspect_only"
    if (
        any(m in text for m in (*REVIEW_MARKERS, *ANALYSIS_MARKERS))
        and any(m in text for m in CPP_ANALYSIS_MARKERS)
        and not _has_write_intent(text)
    ):
        return "cpp_analysis"
    if any(m in text for m in REVIEW_MARKERS):
        return "inspect_only"
    if any(m in text for m in ANALYSIS_MARKERS) and not _has_write_intent(text):
        return "inspect_only"
    if any(m in text for m in API_MARKERS) and not _has_write_intent(text):
        return "answer_only"
    if any(m in text for m in CODEGEN_MARKERS):
        return "edit"
    if _has_write_intent(text):
        return "edit"
    return "inspect_only"


def build_request_intent(
    request: str,
    task_kind: TaskKind | None = None,
    *,
    objective: str | None = None,
    domain_kind: str = "",
    ambiguity_material: bool = False,
) -> dict[str, Any]:
    """Issue the minimal server intent contract consumed by the compactor."""

    raw_request = str(request or "")
    source = raw_request.strip()
    authoritative_objective = normalize_objective_for_hash(
        objective if objective is not None else raw_request
    )
    resolved_kind = task_kind or classify_task(source, "auto")
    project_intent = parse_project_control_intent(source)

    if project_intent.matched:
        domain = "project_control"
        operation = (
            "status"
            if project_intent.operation in {"status", "noop"}
            else "select"
        )
        mutability = (
            "control_state"
            if project_intent.operation in {"select", "clear"}
            and not project_intent.negated
            else "none"
        )
        speech_act = project_intent.speech_act
        targets: dict[str, Any] = {
            "project": {
                "kind": project_intent.target_kind,
                "value": project_intent.target,
            }
        }
        if project_intent.operation == "clear":
            targets["project"]["clear"] = True
    else:
        lower = source.casefold()
        if resolved_kind == "compile_fix":
            domain, operation, mutability = "build", "repair", "source_files"
        elif resolved_kind == "runtime_debug":
            domain, operation, mutability = "runtime", "analyze", "none"
        elif resolved_kind in {"edit", "refactor"}:
            domain, operation, mutability = "source", "modify", "source_files"
        elif resolved_kind in {"cpp_analysis", "code_sketch"}:
            domain, operation, mutability = "source", "analyze", "none"
        elif any(marker in lower for marker in ASSET_ANALYSIS_MARKERS):
            domain, operation, mutability = "asset", "analyze", "none"
        elif domain_kind and domain_kind != "generic":
            domain, operation, mutability = "source", "analyze", "none"
        else:
            domain, operation, mutability = "generic", "analyze", "none"
        speech_act = (
            "query"
            if "?" in source
            or re.search(r"(?:뭐|무엇|어디|알려|설명해|what|which|how\s+does)\b", lower)
            else "command"
        )
        symbols = _symbol_candidates_from_text(source)
        targets = {"symbols": symbols[:8]} if symbols else {}

    return {
        "version": 1,
        "objectiveHash": objective_hash(authoritative_objective),
        "domain": domain,
        "operation": operation,
        "mutability": mutability,
        "speechAct": speech_act,
        "negated": bool(project_intent.negated or _has_negated_write_intent(source.casefold())),
        "targets": targets,
        "ambiguity": {
            "status": "unresolved" if ambiguity_material else "resolved",
            "material": bool(ambiguity_material),
        },
    }


def is_continuation_request(request: str) -> bool:
    """Return true only for a context-dependent, goal-free continuation command."""

    return bool(CONTINUATION_REQUEST_RE.fullmatch(str(request or "").strip()))


def resolve_plan_request(request: str, latest_user_message: str | None = None) -> dict[str, Any]:
    """Keep the user's latest verbatim goal authoritative over model restatements."""
    plan_request = normalize_objective_for_hash(request or "")
    latest = normalize_objective_for_hash(latest_user_message or "")
    result: dict[str, Any] = {
        "request": plan_request,
        "usedLatestUserMessage": False,
        "modelRequestSuppressed": False,
        "inventedImplementationPlan": False,
        "modelRequestKind": classify_task(plan_request, "auto") if plan_request else None,
        "latestUserKind": classify_task(latest, "auto") if latest else None,
    }
    if latest and latest != plan_request:
        # A pure continuation is context, not a replacement objective.  Keep a
        # meaningful planner request when non-compactor clients pass the raw
        # latest utterance ("continue" / "계속해") as latestUserMessage.
        if is_continuation_request(latest) and not is_continuation_request(plan_request):
            return result
        result["request"] = latest
        result["usedLatestUserMessage"] = True
        result["modelRequestSuppressed"] = True
        result["inventedImplementationPlan"] = _looks_like_invented_implementation_plan(
            plan_request
        )
        return result
    if _looks_like_invented_implementation_plan(plan_request):
        result["inventedImplementationPlan"] = True
        if latest:
            result["request"] = latest
            result["usedLatestUserMessage"] = True
            result["modelRequestSuppressed"] = True
            return result
        # No latest user text available: fail closed to inspect-only wording.
        result["request"] = (
            "Find bugs only from current project source evidence. Do not edit files, "
            "do not invent a refactor/implementation plan, and do not restate a prior "
            "project-structure overview."
        )
        result["modelRequestSuppressed"] = True
        return result
    if not latest or not plan_request or latest == plan_request:
        return result
    return result


def _looks_like_invented_implementation_plan(text: str) -> bool:
    source = str(text or "")
    if len(source) < 180:
        return False
    markers = (
        "introduce u",
        "implementationfiles",
        "implementationslices",
        "thin down",
        "selectedalternative",
        "component-per-concern",
        "keep changes focused and backward-compatible",
    )
    lower = source.lower()
    hits = sum(1 for marker in markers if marker in lower)
    return hits >= 2 or ("refactor" in lower and "introduce u" in lower and len(source) > 250)


def choose_edit_strategy(task_kind: TaskKind, request: str, *, file_count_hint: int = 0) -> EditStrategy:
    if task_kind in {"answer_only", "project_control", "inspect_only", "cpp_analysis", "code_sketch"}:
        return "no_edit"
    if task_kind == "compile_fix":
        return "exact_patch"
    if task_kind == "refactor" and "r0" in request.lower():
        return "no_edit"
    if file_count_hint == 0 and "new file" in request.lower():
        return "new_file"
    if file_count_hint == 1:
        return "exact_patch"
    return "exact_patch"


def build_evidence_plan(request: str, task_kind: TaskKind, mode: str = "auto") -> EvidencePlan:
    plan = EvidencePlan(task_kind=task_kind, queries=[request.strip()])
    try:
        from rag_search import resolve_mode

        resolved_mode = resolve_mode(request, mode)
    except Exception:
        resolved_mode = mode
    if task_kind == "project_control":
        # Selecting or reporting a project uses its dedicated control tool. It
        # must never create source/RAG evidence obligations or write authority.
        plan.queries = []
        plan.rag_modes = []
        plan.gates = []
        plan.writes_allowed = False
        plan.confidence = 1.0
    elif task_kind == "answer_only":
        plan.rag_modes = ["api_lookup", "auto"]
        plan.gates = []
        plan.writes_allowed = False
        plan.confidence = 0.8
    elif task_kind == "code_sketch":
        # Draft/example code: gather codegen + API evidence, verify every named
        # symbol, and never write files. The sketch stays at proof level Proposed.
        plan.rag_modes = ["codegen", "api_lookup", "implementation"]
        plan.gates = ["unreal_symbol_lookup", "unreal_code_sketch_claim_validate"]
        plan.writes_allowed = False
        plan.confidence = 0.6
        plan.files_to_read.append("Source/**/*.h")
    elif task_kind == "cpp_analysis":
        # Validate negative symbol claims and logic-missing / by-design false positives.
        plan.rag_modes = ["review", "planning"]
        plan.gates = [
            "direct_source_evidence",
            "unreal_review_claim_validate",  # negative + logic-missing claims
        ]
        plan.files_to_read.extend(["project://Source/**/*.h", "project://Source/**/*.cpp"])
        plan.writes_allowed = False
        plan.confidence = 0.7
    elif task_kind == "inspect_only":
        if mode in ASSET_METADATA_MODES:
            plan.rag_modes = [mode, "review"]
            plan.gates = [
                "unreal_editor_metadata_status",
                "unreal_sync_editor_metadata",
                "unreal_asset_graph_lookup",
                "unreal_material_claim_validate",
                "unreal_blueprint_claim_validate",
            ]
        else:
            plan.rag_modes = ["review", "planning"]
            plan.gates = [
                "unreal_project_architecture",
                "unreal_review_claim_validate",  # negative + logic-missing claims
            ]
        plan.writes_allowed = False
        plan.confidence = 0.75
    elif task_kind == "compile_fix":
        if resolved_mode == "reflection_fix":
            plan.rag_modes = ["reflection_fix", "compile_fix", "module_fix"]
        elif resolved_mode == "module_fix":
            plan.rag_modes = ["module_fix", "compile_fix", "reflection_fix", "multifile_refactor"]
        elif resolved_mode == "multifile_refactor":
            plan.rag_modes = ["multifile_refactor", "compile_fix", "module_fix", "reflection_fix"]
        else:
            plan.rag_modes = ["compile_fix", "module_fix", "reflection_fix", "multifile_refactor"]
        plan.gates = ["static_validate", "ubt_build"]
        plan.writes_allowed = True
        plan.confidence = 0.7
        if resolved_mode == "module_fix" or "build.cs" in request.lower() or "gameplaytag" in request.lower():
            plan.files_to_read.append("Source/**/*.Build.cs")
    elif task_kind == "refactor":
        try:
            from refactor_plan import classify_refactor_scope

            refactor_scope = classify_refactor_scope(request)
        except Exception:
            refactor_scope = {"scope": "unknown", "writesAllowedByDefault": False, "requiredGates": []}
        plan.rag_modes = [mode if mode.startswith("refactor_") else "refactor_r0", "planning"]
        plan.gates = [
            "unreal_refactor_manager_plan",
            "unreal_refactor_plan_validate",
            "unreal_refactor_impact_scan",
            "unreal_project_architecture",
            *[gate for gate in refactor_scope.get("requiredGates", []) if gate not in {"impact_analysis"}],
        ]
        plan.writes_allowed = bool(refactor_scope.get("writesAllowedByDefault")) and (
            mode not in {"refactor_r0", "refactor_r1", "auto"} or "r0" not in mode
        )
        if "r0" in mode.lower() or task_kind == "refactor" and "discover" in request.lower():
            plan.writes_allowed = False
        plan.confidence = 0.65
    elif task_kind == "runtime_debug":
        plan.rag_modes = ["runtime_debug", "review"]
        plan.gates = ["unreal_runtime_config_check", "read_unreal_logs"]
        plan.writes_allowed = False
        plan.confidence = 0.7
    else:
        plan.rag_modes = [resolved_mode if resolved_mode != "auto" else "agent_edit", "codegen", "compile_fix"]
        plan.gates = ["static_validate", "ubt_build"]
        plan.writes_allowed = True
        plan.confidence = 0.72 if resolved_mode in {"codegen", "prototype_component", "prototype_subsystem"} else 0.6

    _extract_symbols(request, plan)
    return plan


def _extract_symbols(request: str, plan: EvidencePlan) -> None:
    for match in re.finditer(r"\bU[A-Z][A-Za-z0-9_]+\b", request):
        plan.symbols_to_scan.append(match.group(0))
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode)\b", request):
        sym = match.group(0)
        if sym not in plan.symbols_to_scan:
            plan.symbols_to_scan.append(sym)


def build_error_route(request: str, task_kind: TaskKind, mode: str) -> dict[str, Any]:
    lower = f"{mode} {request}".lower()
    if task_kind == "runtime_debug" or any(marker in lower for marker in RUNTIME_MARKERS):
        try:
            from error_taxonomy import route_error_action

            routed = route_error_action(request)
            if routed:
                return routed
        except Exception:
            pass
    if task_kind != "compile_fix" and mode not in {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}:
        return {}
    if mode != "multifile_refactor" and not any(marker in lower for marker in COMPILE_MARKERS):
        return {}
    try:
        from error_taxonomy import route_error_action

        return route_error_action(request)
    except Exception:
        return {}


def apply_error_route_to_plan(evidence: EvidencePlan, checkpoints: list[str], route: dict[str, Any]) -> None:
    preferred = [str(mode) for mode in route.get("preferredRagModes") or [] if str(mode).strip()]
    if preferred:
        merged = preferred + [mode for mode in evidence.rag_modes if mode not in preferred]
        evidence.rag_modes = merged[:4]
    for read in route.get("requiredReads") or []:
        item = f"Route required read: {read}"
        if item not in checkpoints:
            checkpoints.append(item)
    for action in route.get("forbiddenActions") or []:
        item = f"Route forbidden action: {action}"
        if item not in checkpoints:
            checkpoints.append(item)
    for steering in route.get("softSteering") or []:
        item = f"Route soft steering: {steering}"
        if item not in checkpoints:
            checkpoints.append(item)
    build_cs_warning = str(route.get("buildCsFirstWarning") or "").strip()
    if build_cs_warning:
        item = f"Route soft warning: {build_cs_warning}"
        if item not in checkpoints:
            checkpoints.append(item)


def build_module_hints(request: str, project_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        from module_resolver import build_cs_has_module, resolve_modules_from_error, resolve_modules_from_text
    except Exception:
        return []
    modules = []
    for module in [*resolve_modules_from_error(request), *resolve_modules_from_text(request)]:
        if module not in modules:
            modules.append(module)
    if not modules:
        return []

    build_cs_text = ""
    project_dir = Path(str((project_context or {}).get("projectDir") or ""))
    if project_dir.is_dir():
        for path in sorted((project_dir / "Source").rglob("*.Build.cs")) if (project_dir / "Source").is_dir() else []:
            try:
                build_cs_text += "\n" + path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
    hints: list[dict[str, Any]] = []
    for module in modules:
        already = build_cs_has_module(build_cs_text, module) if build_cs_text else None
        target = "source include/signature first" if already else "owner Build.cs if missing module evidence is confirmed"
        hints.append(
            {
                "module": module,
                "buildCsAlreadyContains": already,
                "suggestedPatchTarget": target,
                "note": "Hint only; do not force Build.cs edits without evidence.",
            }
        )
    return hints


_FEATURE_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "what",
        "when",
        "where",
        "which",
        "review",
        "inventory",
        "missing",
        "system",
        "project",
        "source",
        "component",
        "components",
        "add",
        "need",
        "needs",
        "should",
        "about",
        "after",
        "before",
        "only",
        "please",
        "analyze",
        "analysis",
    }
)


def _symbol_candidates_from_text(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (
        r"\b[AUFSI][A-Z][A-Za-z0-9_]{2,}\b",
        r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode|Widget)\b",
        r"\b[A-Z][a-zA-Z]{2,}\b",
    ):
        for match in re.finditer(pattern, text or ""):
            symbol = match.group(0)
            if symbol.lower() in _FEATURE_STOPWORDS:
                continue
            if symbol not in found:
                found.append(symbol)
    return found[:12]


def _feature_search_tokens_from_text(text: str) -> list[str]:
    """Prefer compact search_files queries over the full user sentence."""
    tokens = _symbol_candidates_from_text(text)
    if tokens:
        return tokens[:3]
    lowered = (text or "").strip()
    if "시네마틱" in lowered or "cinematic" in lowered.lower():
        return ["Cinematic"]
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text or "")
    picked: list[str] = []
    for word in words:
        if word.lower() in _FEATURE_STOPWORDS:
            continue
        if word not in picked:
            picked.append(word)
        if len(picked) >= 3:
            break
    return picked


def build_symbol_graph_hints(request: str) -> list[dict[str, Any]]:
    try:
        from symbol_graph import load_symbol_graph, lookup_symbol
    except Exception:
        return []
    graph = load_symbol_graph()
    hints: list[dict[str, Any]] = []
    for symbol in _symbol_candidates_from_text(request):
        for row in lookup_symbol(symbol, graph, limit=2):
            hints.append(
                {
                    "symbol": row.get("symbol_name", ""),
                    "kind": row.get("symbol_kind", ""),
                    "file": row.get("file_path", ""),
                    "lineStart": row.get("line_start", 0),
                    "lineEnd": row.get("line_end", row.get("line_start", 0)),
                    "module": row.get("module_name", ""),
                    "ownerBuildCs": row.get("owner_build_cs", ""),
                    "evidenceKind": "project_source",
                    "proofBoundary": row.get(
                        "proofBoundary",
                        "Source-located symbol hint only; it does not prove behavior, wiring, or data flow.",
                    ),
                }
            )
            if len(hints) >= 6:
                return hints
    return hints


def build_write_gate(
    task_kind: TaskKind,
    evidence: EvidencePlan,
    policy: dict[str, Any],
    *,
    edit_strategy: str = "",
    gate_extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    max_files = int(policy.get("maxFilesPerEdit") or 0)
    requires_human_approval = "human_approval_gate" in set(evidence.gates or [])
    writes_allowed = bool(evidence.writes_allowed) and not requires_human_approval
    if edit_strategy == "no_edit":
        writes_allowed = False
    gate: dict[str, Any] = {
        "writesAllowed": writes_allowed,
        "requiresHumanApproval": requires_human_approval,
        "maxFilesPerEdit": max_files,
        "preferPatch": bool(policy.get("preferPatch", True)),
        "mustReadBeforeWrite": bool(evidence.writes_allowed),
        "mustBuildAfterWrite": task_kind in {"edit", "compile_fix", "refactor"},
        "forbiddenWhen": [
            "taskKind is answer_only, project_control, inspect_only, code_sketch, or runtime_debug",
            "editStrategy is no_edit",
            "target file was not read in this session",
            "human_approval_gate is required and has not been satisfied",
        ],
    }
    if gate_extras:
        gate.update(gate_extras)
        if gate_extras.get("requiresHumanApproval"):
            gate["writesAllowed"] = False
        if gate_extras.get("requiresUserClarification"):
            gate["writesAllowed"] = False
        if gate_extras.get("architectureApprovalValid") is False:
            gate["writesAllowed"] = False
            gate["requiresHumanApproval"] = True
    gate["allowSmallRefactor"] = bool(policy.get("allowSmallRefactor"))
    gate["smallRefactorMaxFiles"] = int(policy.get("smallRefactorMaxFiles") or 0)
    gate["mediumRefactorPlanOnly"] = bool(policy.get("mediumRefactorPlanOnly"))
    return gate


def build_checkpoints(task_kind: TaskKind, evidence: EvidencePlan, mode: str = "auto") -> list[str]:
    common = [
        "Confirm activeProject before using project-relative paths.",
        "Call unreal_agent_plan before edits and follow toolPolicy in order.",
        "Use RAG evidence before making Unreal API or Build.cs claims.",
    ]
    if task_kind == "project_control":
        return [
            "Treat project selection/status as a control operation, not an implementation task.",
            "Use unreal_set_active_project only with an exact user-supplied .uproject path (or clear=true).",
            "Do not infer a project from its display name and do not start a task session.",
        ]
    if task_kind == "answer_only":
        return common + ["Answer only after symbol/RAG evidence; do not write files."]
    if task_kind == "code_sketch":
        return common + [
            "Decompose the problem before code: state preservation, target APIs, "
            "lifecycle point, and restore order. List unknowns first.",
            "Call unreal_symbol_lookup for every Unreal type/function you name; "
            "mark anything not found as UNKNOWN instead of inventing an API.",
            "Do not confuse similar concepts (e.g. Actor Tag vs Sequencer Binding "
            "Tag, Spawnable vs Possessable); cite evidence for the distinction.",
            "Validate the drafted symbols with unreal_code_sketch_claim_validate "
            "before presenting compile-ready code.",
            "Keep proof level at Proposed; do not claim it compiles or runs. Do not "
            "write files.",
        ]
    if task_kind == "cpp_analysis":
        return common + [
            "Read current project .h/.cpp files before diagnosis; RAG is background/API evidence only.",
            "Claims about what exists or is missing in the active project require search_files/read_file on that project's Source.",
            "Guideline/engine RAG hits are not project implementation evidence.",
            "On project_miss / doNotRepeatSearch, stop RAG and use search_files then read_file (or conclude absence from zero Source hits).",
            "Record project-relative files and line ranges in sourceEvidence.filesRead.",
            "If direct source reads fail or filesRead is empty, stop without code or project claims.",
            "Read header, cpp, and relevant callsites for cross-file lifecycle/API claims.",
        ]
    if task_kind == "inspect_only":
        inventory_steps = [
            "Claims about what exists or is missing in the active project require search_files/read_file on that project's Source.",
            "Guideline/engine RAG hits are not project implementation evidence.",
            "On project_miss / doNotRepeatSearch, stop RAG and use search_files then read_file (or conclude absence from zero Source hits).",
            "Read target files before findings; do not write files.",
        ]
        asset_steps = [
            "Call unreal_editor_metadata_status before material/blueprint wire claims.",
            "If export dir has JSONL newer than index, call unreal_sync_editor_metadata.",
            "Use unreal_asset_graph_lookup for the target /Game/... asset before summarizing graph facts.",
            "Validate concrete claims with unreal_material_claim_validate or unreal_blueprint_claim_validate.",
        ]
        if mode in ASSET_METADATA_MODES:
            return common + asset_steps + inventory_steps
        return common + inventory_steps
    if task_kind == "runtime_debug":
        return common + ["Read logs/config before diagnosis; do not write files by default."]
    edit_steps = [
        "Read each target file before editing.",
        "Before creating a new .h/.cpp, search_files for basename collisions under Source/.",
        "Prefer replace_in_file with expectedOccurrences=1 for existing files.",
        "Use write_file only for brand-new files; never full-rewrite an existing .h/.cpp/.cs.",
        "If write/replace returns static validation findings, fix them before build_unreal_project.",
        "If cleanup requires deleting files, finish edits first; deletion tools are Extended-only "
        "(propose_file_deletions / delete_file). In Essential mode report the duplicate path and stop for user approval.",
        "For broad multi-feature work whose initial plan has no concrete files, finish bounded discovery, then call "
        "unreal_task_define_slices once with every known executable 1-4 file slice before the first write.",
        "For more than 2 files in Essential mode, patch sequentially and run build_unreal_project after each slice; "
        "prefer unreal_start_compile_loop only when that tool appears in tools/list (Extended).",
        "Do not use run_javascript/js-code-sandbox/Deno file APIs for project file I/O; use unreal-agent file tools.",
    ]
    if "ubt_build" in evidence.gates or task_kind in {"edit", "compile_fix", "refactor"}:
        edit_steps.append("Run build_unreal_project after C++ or Build.cs changes.")
    if task_kind == "refactor":
        edit_steps.extend(
            [
                "Run unreal_refactor_manager_plan before stage-specific refactor tools.",
                "Classify refactor scope before writes: small, medium, or large.",
                "Run unreal_refactor_impact_scan for each public symbol touched.",
                "Use staged patches; do not mix API boundary, callsite rewiring, and cleanup in one turn.",
                "If replace_in_file fails, re-read a smaller range and retry; do not fall back to write_file on existing files.",
                "For medium/large scope, stop at impact plan until human approval is explicit.",
            ]
        )
    return common + edit_steps


def build_stop_conditions(
    task_kind: TaskKind,
    *,
    runtime_write: bool = False,
    completion_contract: dict[str, Any] | None = None,
) -> list[str]:
    if task_kind == "project_control":
        return [
            "Stop after reporting the active project or handing off one exact project-control action.",
            "Do not start source analysis, RAG retrieval, or a task session for project control alone.",
        ]
    if task_kind == "code_sketch":
        return [
            "Stop after presenting a labeled Proposed sketch backed by symbol evidence.",
            "If a required API cannot be verified, present it as UNKNOWN and state "
            "what log/header/export would confirm it; do not guess an API name.",
            "Do not write files or claim the sketch compiles or runs.",
        ]
    if task_kind in {"answer_only", "inspect_only", "cpp_analysis", "runtime_debug"}:
        return [
            "Stop after evidence-backed answer or findings.",
            "If target Source files were already read, answer from direct file evidence; label stale RAG as background-only.",
            "Do not repeat unreal_rag_search while only saying refresh is needed.",
            "If evidence is missing, report the exact missing file/log/index instead of guessing.",
            "For cpp_analysis, zero direct source reads is a hard stop; never substitute RAG snippets.",
        ]
    completion_contract = completion_contract or {}
    automation_route = " → ".join(completion_contract.get("whenAutomationRequired") or [])
    build_only_route = " → ".join(
        completion_contract.get("whenAutomationNotRequiredOrDisabled") or []
    )
    conditions = [
        "Build success is a transition point, not an unconditional terminal result.",
        (
            "Follow the latest authoritative server task control after Build: "
            f"{automation_route} when Automation is declared or required; "
            f"{build_only_route} when Automation is not required or is disabled."
        ),
        "Stop only when authoritative task control reports Complete for the current changed-file set.",
        "BuiltStale and BuiltUnverified do not complete a compile-oriented plan slice.",
        "Runtime-oriented work remains runtimePending until PIE/runtime evidence is recorded.",
        "If build fails, report the first actionable error line and retry with compile_fix RAG.",
        "If required file or activeProject is missing, stop and report the blocker.",
    ]
    if runtime_write:
        conditions.extend(
            [
                "Before the patch, record a supporting same-reproduction experiment and compare two to four isolated patch candidates.",
                "Apply only the selected candidate, then record it in unreal_runtime_debug_session with build evidence.",
                "Do not claim the runtime bug fixed until the same reproductionFingerprint and observer produce RuntimeVerified evidence.",
            ]
        )
    return conditions


def build_retry_policy(task_kind: TaskKind, policy: dict[str, Any]) -> list[str]:
    attempts = int(policy.get("compileFixMaxAttempts") or 3)
    delta_top_k = int(policy.get("deltaTopK") or 3)
    if task_kind not in {"edit", "compile_fix", "refactor"}:
        return ["Do not retry with writes for non-edit tasks."]
    return [
        f"Use at most {attempts} compile-fix attempts for this profile.",
        f"On failure, search only the current error context with delta top_k={delta_top_k}.",
        "Do not repeat a no-op edit; inspect the current file state before the next patch.",
    ]


def build_suggested_tool_calls(
    request: str,
    task_kind: TaskKind,
    mode: str,
    project_context: dict[str, Any],
) -> list[dict[str, Any]]:
    text = str(request or "")

    if task_kind == "project_control":
        if project_control_requests_clear(text):
            return [{"tool": "unreal_set_active_project", "args": {"clear": True}}]
        project_path = project_control_project_path_hint(text)
        if project_path:
            return [
                {
                    "tool": "unreal_set_active_project",
                    "args": {"projectPath": project_path},
                }
            ]
        # A name-only request is intentionally not turned into a guessed path.
        # The caller reports the required user input instead of suggesting an
        # unusable placeholder argument.
        return []

    if task_kind == "refactor":
        symbols = _symbol_candidates_from_text(text)
        calls = [{"tool": "unreal_get_active_project", "args": {}}]
        calls.append(
            {
                "tool": "unreal_refactor_manager_plan",
                "args": {
                    "request": text,
                    "symbols": symbols,
                    "maxFiles": 40,
                },
            }
        )
        calls.append({"tool": "unreal_project_architecture", "args": {}})
        return calls

    if task_kind == "code_sketch":
        symbols = _symbol_candidates_from_text(text)
        calls = [
            {"tool": "unreal_get_active_project", "args": {}},
            {"tool": "unreal_rag_search", "args": {"query": text, "mode": "codegen", "hybrid": False, "top_k": 6}},
        ]
        for symbol in symbols[:4]:
            calls.append({"tool": "unreal_symbol_lookup", "args": {"query": symbol, "top_k": 3}})
        calls.append(
            {
                "tool": "unreal_code_sketch_claim_validate",
                "args": {"sketch": "<paste your drafted code/API list here before presenting it>"},
            }
        )
        return calls

    if not project_context.get("ok"):
        blocking_calls = list(
            project_context.get("suggestedToolCalls") or [{"tool": "unreal_set_active_project", "args": {}}]
        )
        if task_kind not in {"inspect_only", "cpp_analysis"}:
            return blocking_calls
        # Source-first tasks still expose the recovery chain when no active project is set.
        lower = text.lower()
        browse_path = "project://Source"
        search_term = "Cinematic" if "시네마틱" in lower or "cinematic" in lower else text
        calls = list(blocking_calls)
        calls.append({"tool": "search_files", "args": {"query": search_term, "path": browse_path}})
        calls.append({"tool": "read_file", "args": {"path": "<from search_files matches>"}})
        calls.append({"tool": "unreal_rag_search", "args": {"query": text, "mode": "review", "hybrid": False, "top_k": 4}})
        calls.append(
            {
                "tool": "unreal_review_claim_validate",
                "args": {"claims": ["<paste findings with Bug|ByDesign|Ambiguous|NeedsRuntimeProof labels>"]},
            }
        )
        return calls

    from asset_hint_resolver import resolve_asset_folder_hint
    from code_hint_resolver import looks_like_cpp_domain_request, resolve_code_domain_hint

    lower = text.lower()
    asset_markers = (
        "material", "머티리얼", "shader", "folder", "폴더", "/game/", "m_", "mf_",
        "blueprint", "블루프린트", "asset", "에셋",
    )
    asset_like = any(marker in lower for marker in asset_markers)

    if asset_like or mode in ASSET_METADATA_MODES:
        hint_payload = resolve_asset_folder_hint(text, project_context)
        segment = str(hint_payload.get("folderSegment") or hint_payload.get("searchToken") or text).strip()
        calls: list[dict[str, Any]] = [
            {"tool": "unreal_get_active_project", "args": {}},
            {"tool": "unreal_editor_metadata_status", "args": {}},
        ]
        if "folder" in lower or "폴더" in lower:
            calls.append(
                {
                    "tool": "unreal_asset_graph_lookup",
                    "args": {
                        "folderHint": segment,
                        "projectName": project_context["projectName"],
                        "graphDetail": "compact",
                    },
                },
            )
        else:
            calls.append(
                {
                    "tool": "unreal_asset_graph_lookup",
                    "args": {
                        "search": segment,
                        "projectName": project_context["projectName"],
                        "graphDetail": "compact",
                    },
                },
            )
        return calls

    if task_kind in {"inspect_only", "cpp_analysis"}:
        browse_path = str(project_context.get("sourceBrowsePath") or "project://Source")
        search_tokens = _feature_search_tokens_from_text(text)
        # build_agent_plan already resolved projectContext. Repeating the
        # control-plane lookup here is redundant and can collide with another
        # chat's task ownership without adding any source evidence.
        calls: list[dict[str, Any]] = [
            {"tool": "unreal_symbol_lookup", "args": {"query": text, "top_k": 8}},
        ]
        if search_tokens:
            for token in search_tokens:
                calls.append({"tool": "search_files", "args": {"query": token, "path": browse_path}})
        else:
            calls.append({"tool": "search_files", "args": {"query": text[:64], "path": browse_path}})
        calls.append({"tool": "read_file", "args": {"path": "<from search_files matches>"}})
        calls.append({"tool": "unreal_rag_search", "args": {"query": text, "mode": "review", "hybrid": False, "top_k": 4}})
        calls.append(
            {
                "tool": "unreal_review_claim_validate",
                "args": {"claims": ["<paste findings with Bug|ByDesign|Ambiguous|NeedsRuntimeProof labels>"]},
            }
        )
        return calls

    cpp_like = looks_like_cpp_domain_request(text)

    if cpp_like and not asset_like:
        payload = resolve_code_domain_hint(text, project_context)
        return list(payload.get("suggestedToolCalls") or [])

    if task_kind in {"edit", "compile_fix", "refactor"} and project_context.get("ok"):
        browse_path = str(project_context.get("sourceBrowsePath") or "")
        symbols = _symbol_candidates_from_text(text)
        # The planner response already includes a resolved projectContext. A
        # second active-project lookup wastes a route call and, when another
        # conversation owns a task, can produce an avoidable ownership error.
        calls: list[dict[str, Any]] = []
        if browse_path:
            for symbol in symbols[:3]:
                calls.append({"tool": "search_files", "args": {"query": symbol, "path": browse_path}})
            if "component" in lower:
                calls.append({"tool": "search_files", "args": {"query": "Component", "path": browse_path}})
        calls.append(
            {
                "tool": "unreal_rag_search",
                "args": {"query": text, "mode": "codegen" if task_kind == "edit" else "compile_fix", "hybrid": False, "top_k": 6},
            }
        )
        return calls

    return [{"tool": "unreal_get_active_project", "args": {}}]


def _build_project_control_plan(
    request: str,
    *,
    project_context: dict[str, Any],
    policy: dict[str, Any],
) -> AgentPlan:
    """Build a deliberately taskless plan for explicit project control only."""

    parsed = parse_project_control_intent(request)
    evidence = build_evidence_plan(request, "project_control")
    suggested = build_suggested_tool_calls(
        request,
        "project_control",
        "project_control",
        project_context,
    )
    selection_requested = project_control_requests_selection(request)
    has_exact_target = bool(
        project_control_requests_clear(request)
        or project_control_project_path_hint(request)
        or project_control_project_name_hint(request)
    )
    notes = [
        "Project control is handled directly; no task session, source scan, RAG query, or write authority is created.",
    ]
    if selection_requested and not has_exact_target:
        notes.append(
            "Project selection needs one exact project name or .uproject path; fuzzy selection is forbidden."
        )
    return AgentPlan(
        request=request,
        task_kind="project_control",
        evidence=evidence,
        edit_strategy="no_edit",
        tool_policy=[str(call.get("tool") or "") for call in suggested if str(call.get("tool") or "")],
        suggested_tool_calls=suggested,
        project_context=project_context,
        write_gate=build_write_gate(
            "project_control",
            evidence,
            policy,
            edit_strategy="no_edit",
        ),
        checkpoints=build_checkpoints("project_control", evidence),
        stop_conditions=build_stop_conditions("project_control"),
        retry_policy=["Do not retry through source or write tools for project control."],
        notes=notes,
        source_evidence={
            "required": False,
            "claimPolicy": "project_context_only",
        },
        orchestration={
            "strategy": "project_control",
            "riskTier": "control",
            "requiredBeforeWrite": [],
            "taskSessionRequired": False,
        },
        request_intent=build_request_intent(
            request,
            "project_control",
            objective=request,
        ),
        project_control={
            "operation": parsed.operation,
            "speechAct": parsed.speech_act,
            "negated": parsed.negated,
            "targetKind": parsed.target_kind,
            "target": parsed.target,
            "pureControl": parsed.pure_control,
            "remainingRequest": parsed.remaining_request,
        },
        original_objective=request,
    )


def build_agent_plan(
    request: str,
    mode: str = "auto",
    *,
    file_count_hint: int = 0,
    latest_user_message: str | None = None,
    original_objective: str | None = None,
) -> AgentPlan:
    from load_sampling_preset import profile_agent_policy, resolve_profile_name
    from project_context import resolve_active_project_context
    from rag_search import resolve_mode
    from tool_policy import completion_contract_for_task, gates_for_task, tool_sequence_for_task

    resolved = resolve_plan_request(request, latest_user_message)
    request = str(resolved.get("request") or request)
    authoritative_objective = normalize_objective_for_hash(original_objective or request)
    policy = profile_agent_policy()
    project_context = resolve_active_project_context()
    task_kind = classify_task(request, mode)
    if task_kind == "project_control":
        return _build_project_control_plan(
            request,
            project_context=project_context,
            policy=policy,
        )
    resolved_mode = resolve_mode(request, mode)
    evidence = build_evidence_plan(request, task_kind, mode)
    runtime_write = bool(
        task_kind == "edit"
        and (
            mode == "runtime_debug"
            or (_is_runtime_symptom_analysis(request.lower()) and _has_write_intent(request.lower()))
        )
    )
    if runtime_write:
        evidence.rag_modes = ["runtime_debug", "review", "compile_fix"]
        evidence.gates = [
            "unreal_runtime_config_check",
            "read_unreal_logs",
            "unreal_runtime_debug_session",
            "unreal_code_sketch_claim_validate",
            "static_validate",
            "ubt_build",
        ]
        evidence.writes_allowed = True
        evidence.confidence = min(evidence.confidence, 0.65)
    error_route = build_error_route(request, task_kind, mode)
    strategy = choose_edit_strategy(task_kind, request, file_count_hint=file_count_hint)
    if not evidence.writes_allowed:
        strategy = "no_edit"
    if runtime_write:
        tool_policy_key = "runtime_edit"
    elif task_kind == "code_sketch":
        tool_policy_key = "code_sketch"
    elif task_kind in {"inspect_only", "cpp_analysis"}:
        from code_hint_resolver import looks_like_cpp_domain_request

        if task_kind == "cpp_analysis":
            tool_policy_key = PROJECT_SOURCE_ANALYSIS_POLICY_KEY
        elif mode in ASSET_METADATA_MODES:
            tool_policy_key = "asset_metadata_inspect"
        elif looks_like_cpp_domain_request(request) or any(m in request.lower() for m in ANALYSIS_MARKERS):
            tool_policy_key = PROJECT_SOURCE_ANALYSIS_POLICY_KEY
        else:
            tool_policy_key = task_kind
    else:
        tool_policy_key = (
            "codegen"
            if resolved_mode in {"codegen", "prototype_component", "prototype_subsystem"}
            else resolved_mode
            if resolved_mode in {"module_fix", "reflection_fix"}
            else task_kind
        )
    orch_gates = gates_for_task(tool_policy_key) or gates_for_task(task_kind)
    if orch_gates:
        merged_gates = list(evidence.gates or [])
        for gate in orch_gates:
            if gate not in merged_gates:
                merged_gates.append(gate)
        evidence.gates = merged_gates
    tool_policy = tool_sequence_for_task(tool_policy_key) or tool_sequence_for_task(task_kind)
    completion_contract = completion_contract_for_task(
        tool_policy_key
    ) or completion_contract_for_task(task_kind)
    if task_kind == "inspect_only" and mode in ASSET_METADATA_MODES:
        tool_policy = list(ASSET_METADATA_TOOL_POLICY)
    elif task_kind in {"inspect_only", "cpp_analysis"}:
        from code_hint_resolver import looks_like_cpp_domain_request

        if task_kind == "cpp_analysis" or looks_like_cpp_domain_request(request) or any(m in request.lower() for m in ANALYSIS_MARKERS):
            tool_policy = list(CPP_REVIEW_TOOL_POLICY)
    notes: list[str] = []
    if resolved.get("modelRequestSuppressed"):
        notes.append(
            "Plan request overridden: latestUserMessage is the authoritative user goal; "
            "a model restatement cannot narrow, broaden, or replace it."
        )
        if classify_task(request, "auto") in {
            "answer_only",
            "inspect_only",
            "cpp_analysis",
            "runtime_debug",
            "code_sketch",
        }:
            notes.append(
                "Do not call unreal_architecture_reasoning or invent implementation slices. "
                "Deliver findings (Bug|ByDesign|Ambiguous|NeedsRuntimeProof) only."
            )
    if resolved.get("inventedImplementationPlan"):
        notes.append(
            "Detected invented implementation-plan text in unreal_agent_plan.request; "
            "re-call with the user's latest verbatim message."
        )
    module_hints = build_module_hints(request, project_context) if task_kind == "compile_fix" else []
    symbol_graph_hints = build_symbol_graph_hints(request) if task_kind == "compile_fix" else []
    refactor_manager: dict[str, Any] = {}
    if task_kind == "refactor":
        try:
            from refactor_plan import build_refactor_manager_plan, extract_refactor_symbols

            refactor_symbols = list(dict.fromkeys([*evidence.symbols_to_scan, *extract_refactor_symbols(request)]))
            try:
                from symbol_graph import load_symbol_graph

                refactor_graph = load_symbol_graph()
            except (OSError, ValueError):
                refactor_graph = None
            refactor_manager = build_refactor_manager_plan(
                request,
                project_root=str(project_context.get("projectDir") or "") or None,
                symbols=refactor_symbols,
                max_files=40,
                graph=refactor_graph,
                build_graph_if_needed=False,
            )
            refactor_scope = refactor_manager["scope"]
            notes.append(
                "Refactor scope: "
                f"{refactor_scope['scope']} "
                f"(requiresHumanApproval={refactor_scope['requiresHumanApproval']})"
            )
            notes.append(f"Refactor manager nextAction: {refactor_manager.get('nextAction')}")
            graph_impact = refactor_manager.get("changeImpact") or {}
            if graph_impact:
                notes.append(
                    "Graph impact: "
                    f"direct={len(graph_impact.get('directImpacts') or [])}, "
                    f"candidate={len(graph_impact.get('candidateImpacts') or [])}, "
                    f"truncated={bool(graph_impact.get('truncated'))}."
                )
                if any(
                    isinstance(item, dict) and item.get("status") == "coverage_gap"
                    for item in (graph_impact.get("regressionPlan") or [])
                ):
                    notes.append("Targeted regression coverage was not found; define a focused regression check before claiming behavior is preserved.")
            if refactor_scope.get("requiresHumanApproval"):
                notes.append("Medium/large refactors require impact plan and explicit approval before code edits.")
            if not refactor_manager.get("writePolicy", {}).get("writesAllowedNow"):
                strategy = "no_edit"
                evidence.writes_allowed = False
        except Exception:
            notes.append("Refactor scope unavailable; prefer R0 impact planning before edits.")
    if evidence.confidence < 0.65:
        notes.append("Low confidence: prefer inspect-only before edits.")
    if strategy == "exact_patch":
        notes.append("Prefer minimal patch over full-file rewrite.")
    if policy.get("maxFilesPerEdit"):
        notes.append(f"Max files per edit: {policy['maxFilesPerEdit']}")
    if policy.get("defaultTopK"):
        notes.append(f"Default retrieval top_k: {policy['defaultTopK']}")
    if policy.get("targetTier"):
        notes.append(f"Target track: {policy['targetTier']}")
    if policy.get("promptContract"):
        notes.append(f"Prompt contract: {policy['promptContract']}")
    if not policy.get("allowRefactorModes", True) and task_kind == "refactor":
        scope_name = str((refactor_manager.get("scope") or {}).get("scope") or "")
        small_ok = bool(policy.get("allowSmallRefactor")) and scope_name in {
            "small_single_surface_refactor",
            "small_multifile_refactor",
        }
        if not small_ok:
            strategy = "no_edit"
            evidence.writes_allowed = False
            notes.append("Refactor modes disabled for active model profile.")
        else:
            notes.append(
                "Small refactor exception active: bounded refactor allowed despite allowRefactorModes=false."
            )
            small_max = int(policy.get("smallRefactorMaxFiles") or 2)
            if small_max > 0:
                policy = dict(policy)
                policy["maxFilesPerEdit"] = min(int(policy.get("maxFilesPerEdit") or small_max), small_max)

    from domain_planner import (
        architecture_ambiguity_gate,
        build_domain_slice_dag,
        build_fix_evidence,
        build_domain_profile,
        detect_domain_kind,
        partition_plan_slices,
        select_subsystem_lifetime,
    )

    domain_kind = detect_domain_kind(request, resolved_mode if resolved_mode != "auto" else mode)
    domain_profile = build_domain_profile(request, resolved_mode if resolved_mode != "auto" else mode)
    dag_slices = build_domain_slice_dag(domain_profile, request)
    informational_slices, executable_slices = partition_plan_slices(
        dag_slices,
        task_kind=task_kind,
        mode=resolved_mode if resolved_mode != "auto" else mode,
    )
    informational_plan_slices = [slice_.to_dict() for slice_ in informational_slices]
    executable_plan_slices = [slice_.to_dict() for slice_ in executable_slices]
    plan_slices = executable_plan_slices
    fix_evidence = build_fix_evidence(
        request,
        error_route,
        project_root=Path(str(project_context.get("projectDir") or "")) if project_context.get("projectDir") else None,
    ) or {}
    ambiguity_gate: dict[str, Any] = {}
    feature_intent: dict[str, Any] = {}
    feature_completion_audit: dict[str, Any] = {}
    from semantic_ambiguity import resolve_lexical_semantic_ambiguity

    semantic_ambiguity = resolve_lexical_semantic_ambiguity(
        request,
        write_intent=task_kind in {"edit", "compile_fix", "refactor"},
    )
    architecture_required = domain_profile.architecture_required or domain_kind == "architecture"
    if architecture_required:
        ambiguity_gate = architecture_ambiguity_gate(request)
        action = str(ambiguity_gate.get("recommendedAction") or "")
        if action == "plan_only":
            notes.append("Architecture ambiguity gate: plan-only until ownership checklist is satisfied.")
        elif action == "ask_user_once":
            notes.append("Architecture ambiguity gate: user clarification required before writes.")
        elif action == "human_approval":
            notes.append("Architecture ambiguity gate: human approval required before writes.")

    from plan_consistency import (
        apply_ambiguity_write_policy,
        apply_consistency_fallback,
        essential_tools_enabled,
        sanitize_tools_for_exposure,
        validate_plan_consistency,
    )

    gate_extras: dict[str, Any] = {}
    if semantic_ambiguity.get("material"):
        strategy = "no_edit"
        evidence.writes_allowed = False
        gate_extras.update(
            {
                "requiresUserClarification": True,
                "semanticAmbiguityStatus": "unresolved",
            }
        )
        notes.append(
            "Lexical semantic ambiguity is material: compare the server-issued "
            "interpretations and obtain one explicit target before writes."
        )
    if ambiguity_gate:
        strategy, evidence_writes, gate_extras = apply_ambiguity_write_policy(
            ambiguity_gate=ambiguity_gate,
            strategy=strategy,
            evidence_writes_allowed=evidence.writes_allowed,
        )
        evidence.writes_allowed = evidence_writes
        if float(ambiguity_gate.get("ambiguityScore") or 0) >= 0.6:
            from architecture_decision import approval_is_valid, build_architecture_decision

            decision = build_architecture_decision(
                ambiguity_gate=ambiguity_gate,
                project_path=str(project_context.get("uprojectPath") or ""),
                plan_revision="1",
            )
            store_path = Path(__file__).resolve().parent.parent / "data" / "architecture_approvals.json"
            if not approval_is_valid(store_path, decision):
                gate_extras.setdefault("requiresHumanApproval", True)
                gate_extras["architectureApprovalValid"] = False
                gate_extras["architectureDecisionId"] = decision.decision_id
            else:
                gate_extras["architectureApprovalValid"] = True
                gate_extras["architectureDecisionId"] = decision.decision_id

    if task_kind in {"edit", "refactor"}:
        from feature_intent_contract import (
            requires_feature_completion_audit,
            resolve_feature_intent,
        )

        completion_audit_required = requires_feature_completion_audit(request)
        feature_completion_audit = {
            "version": 1,
            "required": completion_audit_required,
            "status": "pending" if completion_audit_required else "not_required",
        }

        feature_resolution = resolve_feature_intent(
            request,
            write_intent=True,
            candidate_count=3,
        )
        feature_intent = {
            "version": 1,
            "ambiguity": dict(feature_resolution.get("ambiguity") or {}),
            "candidateCount": int(feature_resolution.get("candidateCount") or 0),
            "eligibleCandidateCount": int(
                feature_resolution.get("eligibleCandidateCount") or 0
            ),
            "candidates": list(feature_resolution.get("candidates") or []),
            "blockingQuestions": list(
                feature_resolution.get("blockingQuestions") or []
            )[:3],
            "requiresResolution": bool(
                (feature_resolution.get("ambiguity") or {}).get(
                    "requiresResolution"
                )
                or completion_audit_required
            ),
            "recommendedAction": str(
                (feature_resolution.get("ambiguity") or {}).get(
                    "recommendedAction"
                )
                or ""
            ),
            "requiresFeatureCompletionAudit": completion_audit_required,
        }
        if completion_audit_required:
            notes.append(
                "Feature completion audit is required: bind only a direct-source-backed "
                "functional gap; test-only work cannot be the selected feature."
            )
        if feature_intent["requiresResolution"]:
            notes.append(
                "Feature intent is ambiguous: choose one compact candidate and "
                "bind its acceptance oracles to exact targets before writes."
            )
            if feature_intent["recommendedAction"] == "user_approval":
                gate_extras["requiresFeatureIntentApproval"] = True
                gate_extras["intentResolutionMode"] = "plan_only_until_approved"

    if domain_kind == "subsystem" and plan_slices:
        lifetime = select_subsystem_lifetime(request)
        notes.append(
            f"Subsystem lifetime: requested={lifetime.get('requestedLifetime')} "
            f"recommended={lifetime.get('recommendedBase')}"
        )
    if fix_evidence:
        notes.append("fixEvidence populated from error route/resolver.")
    if informational_plan_slices:
        notes.append(f"Informational plan slices: {len(informational_plan_slices)} (not executable).")
    if plan_slices:
        notes.append(f"Executable plan slices ({domain_kind}): {len(plan_slices)} slice(s), max 2 files per slice.")

    orchestration = build_orchestration_decision(
        task_kind=task_kind,
        file_count_hint=file_count_hint,
        domain_kind=domain_kind,
        architecture_required=architecture_required,
        policy=policy,
        profile_name=resolve_profile_name(),
        completion_contract=completion_contract,
    )
    if feature_intent.get("requiresResolution") or semantic_ambiguity.get("material"):
        required = list(orchestration.get("requiredBeforeWrite") or [])
        if "unreal_feature_intent_resolve" not in required:
            required.insert(0, "unreal_feature_intent_resolve")
        orchestration["requiredBeforeWrite"] = required
        (orchestration.get("roleContract") or {}).get("implementer", {})[
            "startsAfter"
        ] = list(required)
    if runtime_write:
        required = list(orchestration.get("requiredBeforeWrite") or [])
        for gate_name in ("unreal_runtime_debug_session", "unreal_code_sketch_claim_validate"):
            if gate_name not in required:
                required.append(gate_name)
        orchestration["requiredBeforeWrite"] = required
        orchestration["strategy"] = "runtime_causal_loop"
        orchestration["runtimeVerificationRequired"] = True
        (orchestration.get("roleContract") or {}).get("implementer", {})[
            "startsAfter"
        ] = list(required)
        verifier_evidence = (
            (orchestration.get("roleContract") or {}).get("verifier", {}).get(
                "requiredEvidence"
            )
            or []
        )
        if "same_observer_runtime_verification" not in verifier_evidence:
            verifier_evidence.append("same_observer_runtime_verification")
    for required_tool in orchestration["requiredBeforeWrite"]:
        _insert_tool_before(
            tool_policy,
            required_tool,
            ("replace_in_file", "write_file", "build_unreal_project"),
        )
    if task_kind in {"edit", "compile_fix", "refactor"}:
        _insert_tool_before(
            tool_policy,
            "static_validate_project",
            ("build_unreal_project",),
        )
    notes.append(
        "Orchestration route: "
        f"{orchestration['strategy']} ({orchestration['riskTier']} risk, "
        f"profile={orchestration['profile']})."
    )

    suggested = build_suggested_tool_calls(request, task_kind, mode, project_context)
    checkpoints = build_checkpoints(task_kind, evidence, mode)
    if runtime_write:
        checkpoints.extend(
            [
                "Prepare unreal_runtime_debug_session with a fixed symptom, reproduction fingerprint, observer, ranked falsifiable hypotheses, and runtime policy.",
                "Record a supporting experiment, compare two to four isolated patch candidates, and apply only the selected candidate.",
                "Record the selected patch/build proof, then verify with the same reproduction fingerprint, observer, and metric/trace/soak oracle.",
            ]
        )
    if error_route:
        apply_error_route_to_plan(evidence, checkpoints, error_route)
        allowed = ", ".join(str(item) for item in error_route.get("allowedPatchTargets") or [])
        if allowed:
            notes.append(f"Route allowed patch targets hint: {allowed}")
    for hint in module_hints:
        notes.append(
            f"Module hint: {hint['module']} -> {hint['suggestedPatchTarget']}"
        )
    if symbol_graph_hints:
        notes.append(f"Symbol graph hints available: {len(symbol_graph_hints)} compact match(es).")
    if not project_context.get("ok"):
        notes.append(str(project_context.get("error") or "Set activeProject before browse or asset lookup."))
    notes.append("Copy suggestedToolCalls args exactly; never hardcode project paths.")

    refactor_embedded = bool(refactor_manager)
    tool_policy, suggested, exposure_notes, sanitized_gates = sanitize_tools_for_exposure(
        tool_policy,
        suggested,
        refactor_manager_embedded=refactor_embedded,
        gates=list(evidence.gates or []),
    )
    evidence.gates = sanitized_gates
    notes.extend(exposure_notes)
    if refactor_embedded and essential_tools_enabled():
        notes.append("Refactor manager results are embedded in refactorManager; do not call hidden refactor tools.")
    from phase_tool_router import compact_plan_tool_policy

    tool_policy = compact_plan_tool_policy(
        task_kind,
        required_gates=list(orchestration.get("requiredBeforeWrite") or []),
        writes_allowed=bool(evidence.writes_allowed),
        base_policy=tool_policy,
    )
    notes.append(
        "Dynamic tool route: the server exposes 5-10 budgeted work tools plus "
        "separate recovery controls and a bounded replan surface; phase, scores, "
        "gate decisions, and replan limits are server-owned."
    )

    source_required = task_kind in {"cpp_analysis", "refactor"} or (
        task_kind in {"edit", "code_sketch"} and _is_project_specific(request.lower())
    )
    source_evidence = {
        "required": source_required,
        "sourceReadSucceeded": False,
        "filesRead": [],
        "claimPolicy": "fail_closed" if source_required else "generic_example_allowed",
        "onMissing": (
            "Stop without project diagnosis or code. Report the failed path, reason, and next read tool call."
            if source_required else "Do not label generic examples as project-specific."
        ),
    }
    inspection_contract: dict[str, Any] = {}
    if task_kind in {"inspect_only", "cpp_analysis"}:
        inspection_text = authoritative_objective or request
        repository_scope = bool(
            re.search(
                r"(?:repository[- ]wide|entire\s+(?:repository|project|codebase)|"
                r"all\s+(?:repository|project|source|code)|whole\s+(?:repository|project)|"
                r"저장소\s*전체|프로젝트\s*전체|전체\s*(?:코드|소스)|모든\s*(?:코드|소스)|소스\s*코드\s*구조)",
                inspection_text,
                flags=re.IGNORECASE,
            )
        )
        exhaustive = bool(
            re.search(
                r"\b(?:every|all|exhaustive|complete)\b|(?:모든|전부|전체\s*파일|빠짐없이|완전\s*감사)",
                inspection_text,
                flags=re.IGNORECASE,
            )
        )
        if repository_scope:
            coverage_mode = "repository_exhaustive" if exhaustive else "repository_overview"
        else:
            coverage_mode = "exhaustive_targeted_audit" if exhaustive else "targeted_overview"
        topic_match = re.search(
            r"([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,2}|[가-힣]{2,24})\s*(?:시스템|서브시스템|모듈|폴더)",
            inspection_text,
            flags=re.IGNORECASE,
        )
        topic = (topic_match.group(1).strip() if topic_match else "repository")
        direct_read_limit = 8 if coverage_mode == "targeted_overview" else 12
        inspection_contract = {
            "version": 1,
            "intent": "targeted_structural_analysis" if not repository_scope else "repository_structural_analysis",
            "topicTarget": topic,
            "coverageMode": coverage_mode,
            "candidateSourceRoots": ["project://Source", "project://Plugins/*/Source"],
            "evidenceBudget": {
                "maxDirectoryLists": 2,
                "maxDirectSourceReadsPerPhase": direct_read_limit,
                "maxFullReadChars": 12000,
                "maxFullReadLines": 300,
                "maxEvidenceCharsPerPhase": 64000,
                "representativePairs": 4,
            },
            "selectionPolicy": (
                "Select representative interface/types/coordinator/entrypoint Public/Private pairs before reading; "
                "large files use outlines or exact ranges."
            ),
            "synthesisCompletionCondition": (
                "Synthesize after representative coverage is satisfied or the direct-read limit is reached; "
                "record every uninspected candidate in remainingFrontier and never imply exhaustive coverage."
            ),
            "readOnly": True,
        }

    write_gate = build_write_gate(
        task_kind,
        evidence,
        policy,
        edit_strategy=strategy,
        gate_extras=gate_extras,
    )

    from tool_discovery import discover_tool_candidates

    discovery_family = "architecture" if domain_kind in {"subsystem", "component", "replication"} else "source_search"
    tool_discovery_candidates = discover_tool_candidates(family=discovery_family)
    plan_graph_delta: dict[str, Any] = {}
    if informational_plan_slices and not executable_plan_slices and task_kind == "compile_fix":
        plan_graph_delta = {
            "reason": "compile_fix informational-only plan",
            "invalidate": [
                str(item.get("slice_id") or item.get("sliceId") or "")
                for item in informational_plan_slices
            ],
        }

    plan = AgentPlan(
        request=request,
        task_kind=task_kind,
        evidence=evidence,
        edit_strategy=strategy,
        tool_policy=tool_policy,
        suggested_tool_calls=suggested,
        project_context=project_context,
        write_gate=write_gate,
        checkpoints=checkpoints,
        stop_conditions=build_stop_conditions(
            task_kind,
            runtime_write=runtime_write,
            completion_contract=completion_contract,
        ),
        retry_policy=build_retry_policy(task_kind, policy),
        notes=notes,
        error_route=error_route,
        module_hints=module_hints,
        symbol_graph_hints=symbol_graph_hints,
        refactor_manager=refactor_manager,
        domain_kind=domain_kind,
        domain_profile=domain_profile.to_dict(),
        plan_slices=plan_slices,
        informational_plan_slices=informational_plan_slices,
        executable_plan_slices=executable_plan_slices,
        fix_evidence=fix_evidence,
        ambiguity_gate=ambiguity_gate,
        feature_intent=feature_intent,
        feature_completion_audit=feature_completion_audit,
        source_evidence=source_evidence,
        tool_discovery_candidates=tool_discovery_candidates,
        plan_graph_delta=plan_graph_delta,
        orchestration=orchestration,
        request_intent=build_request_intent(
            request,
            task_kind,
            objective=authoritative_objective,
            domain_kind=domain_kind,
            ambiguity_material=bool(
                (ambiguity_gate or {}).get("requiresResolution")
                or feature_intent.get("requiresResolution")
                or semantic_ambiguity.get("material")
            ),
        ),
        semantic_ambiguity=semantic_ambiguity,
        inspection_contract=inspection_contract,
        original_objective=authoritative_objective,
    )
    consistency_issues = validate_plan_consistency(plan)
    if consistency_issues:
        apply_consistency_fallback(plan, consistency_issues)
    return plan


def orchestrator_enabled() -> bool:
    return os.environ.get("UNREAL_AGENT_ORCHESTRATE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def format_plan_for_prompt(plan: AgentPlan) -> str:
    payload = plan.to_dict()
    return (
        "## Agent orchestrator plan (follow this order)\n"
        f"Task: {payload['taskKind']}\n"
        f"Edit strategy: {payload['editStrategy']}\n"
        f"RAG modes: {', '.join(plan.evidence.rag_modes)}\n"
        f"Gates: {', '.join(plan.evidence.gates) or 'none'}\n"
        f"Tool policy: {' -> '.join(plan.tool_policy)}\n"
        f"Orchestration: {json.dumps(plan.orchestration, ensure_ascii=False)}\n"
        f"Suggested tool calls: {json.dumps(plan.suggested_tool_calls, ensure_ascii=False)}\n"
        f"Project: {plan.project_context.get('projectName') or 'unset'}\n"
        f"Write gate: writesAllowed={plan.write_gate.get('writesAllowed')}, "
        f"maxFilesPerEdit={plan.write_gate.get('maxFilesPerEdit')}\n"
        + (
            "Files to read first: " + ", ".join(plan.evidence.files_to_read) + "\n"
            if plan.evidence.files_to_read
            else ""
        )
        + ("Checkpoints: " + "; ".join(plan.checkpoints) + "\n" if plan.checkpoints else "")
        + ("Stop conditions: " + "; ".join(plan.stop_conditions) + "\n" if plan.stop_conditions else "")
        + ("Retry policy: " + "; ".join(plan.retry_policy) + "\n" if plan.retry_policy else "")
        + (
            "Error route: " + json.dumps(plan.error_route, ensure_ascii=False) + "\n"
            if plan.error_route
            else ""
        )
        + (
            "Module hints: " + json.dumps(plan.module_hints, ensure_ascii=False) + "\n"
            if plan.module_hints
            else ""
        )
        + (
            "Symbol graph hints: " + json.dumps(plan.symbol_graph_hints, ensure_ascii=False) + "\n"
            if plan.symbol_graph_hints
            else ""
        )
        + (
            "Refactor manager: "
            + json.dumps(
                {
                    "scope": plan.refactor_manager.get("scope", {}).get("scope"),
                    "nextAction": plan.refactor_manager.get("nextAction"),
                    "writePolicy": plan.refactor_manager.get("writePolicy"),
                    "missingRequiredRoles": plan.refactor_manager.get("impact", {}).get("missingRequiredRoles", []),
                    "directImpactPaths": [
                        item.get("path")
                        for item in (plan.refactor_manager.get("changeImpact", {}).get("directImpacts") or [])[:6]
                        if isinstance(item, dict)
                    ],
                    "candidateImpactPaths": [
                        item.get("path")
                        for item in (plan.refactor_manager.get("changeImpact", {}).get("candidateImpacts") or [])[:6]
                        if isinstance(item, dict)
                    ],
                    "regressionPlan": [
                        {
                            "kind": item.get("kind"),
                            "required": item.get("required"),
                            "status": item.get("status"),
                        }
                        for item in (plan.refactor_manager.get("changeImpact", {}).get("regressionPlan") or [])
                        if isinstance(item, dict)
                    ],
                },
                ensure_ascii=False,
            )
            + "\n"
            if plan.refactor_manager
            else ""
        )
        + (
            "Fix evidence: " + json.dumps(plan.fix_evidence, ensure_ascii=False) + "\n"
            if plan.fix_evidence
            else ""
        )
        + (
            "Plan slices: " + json.dumps(plan.plan_slices, ensure_ascii=False) + "\n"
            if plan.plan_slices
            else ""
        )
        + (
            "Informational plan slices: "
            + json.dumps(plan.informational_plan_slices, ensure_ascii=False)
            + "\n"
            if plan.informational_plan_slices
            else ""
        )
        + (
            "Tool discovery candidates: "
            + json.dumps(plan.tool_discovery_candidates, ensure_ascii=False)
            + "\n"
            if plan.tool_discovery_candidates
            else ""
        )
        + (
            "Domain kind: " + str(plan.domain_kind) + "\n"
            if plan.domain_kind and plan.domain_kind != "generic"
            else ""
        )
        + ("Notes: " + "; ".join(plan.notes) + "\n" if plan.notes else "")
    )


def verify_edit_allowed(plan: AgentPlan, *, files_count: int, patches_count: int) -> dict[str, Any]:
    issues: list[str] = []
    if plan.edit_strategy == "no_edit" and (files_count or patches_count):
        issues.append("Plan forbids edits but bundle contains file changes.")
    if plan.task_kind in {"inspect_only", "cpp_analysis", "code_sketch", "runtime_debug"} and (files_count or patches_count):
        issues.append(f"{plan.task_kind} task must not write files.")
    if not plan.write_gate.get("writesAllowed", plan.evidence.writes_allowed) and (files_count or patches_count):
        issues.append("Write gate forbids edits for this task.")
    max_files = int(plan.write_gate.get("maxFilesPerEdit") or 0)
    total = files_count + patches_count
    if max_files > 0 and total > max_files:
        issues.append(f"Edit bundle exceeds maxFilesPerEdit={max_files}.")
    return {"ok": len(issues) == 0, "issues": issues}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build agent orchestrator plan JSON.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = build_agent_plan(args.request, args.mode)
    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_plan_for_prompt(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
