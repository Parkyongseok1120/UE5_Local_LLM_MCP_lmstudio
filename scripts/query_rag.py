#!/usr/bin/env python
"""Query the SQLite RAG index and optionally ask LM Studio."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from load_sampling_preset import profile_agent_policy, set_sampling_profile_for_model
from rag_context import assemble_context
from rag_search import SearchOptions, search as search_index
from workspace_paths import active_project_names


SOURCE_TYPE_LABELS = {
    "project_guideline": "User RAG guideline",
    "game_design_doc": "Game design document",
    "unreal_symbol": "Unreal symbol metadata",
    "build_log": "Unreal build/editor log",
    "epic_docs": "Epic official documentation",
    "unreal_source": "Unreal Engine source",
    "unreal_project_text": "Local project source",
    "unreal_project_asset_path": "Local project asset path",
    "unreal_blueprint_metadata": "Blueprint metadata export",
    "unreal_material_metadata": "Material metadata export",
    "unreal_animation_metadata": "Animation metadata export",
    "unreal_skeletal_mesh_metadata": "SkeletalMesh metadata export",
    "unreal_anim_blueprint_metadata": "AnimBlueprint metadata export",
    "unreal_anim_montage_metadata": "AnimMontage metadata export",
    "unreal_sequencer_metadata": "Sequencer metadata export",
}

SECTION_LABELS = [
    "Critical Rule: Do Not Mix Interface and Event",
    "Critical Rule: Prefer Intent-Revealing Mutation APIs",
    "Critical Rule: Label Code Accuracy",
    "Critical Response Rules",
    "Critical Review Gates",
    "Critical AI Anti-Patterns",
    "9.2 Quality Gate",
    "RAG Layering and Example Use",
    "Request Intent Router",
    "Prototype Planning Template",
    "Reference Abstraction Rule",
    "Prototype Scope Guard",
    "Code Suppression For Planning",
    "Prototype Planning Failure Patterns",
    "Genre Adapter Registry",
    "Action Combat Prototype Adapter",
    "Shooter Prototype Adapter",
    "Battle Royale / Extraction Prototype Adapter",
    "Platformer Prototype Adapter",
    "Puzzle Prototype Adapter",
    "Survival Crafting Prototype Adapter",
    "Roguelike Prototype Adapter",
    "Deckbuilder Prototype Adapter",
    "Management Simulation Prototype Adapter",
    "Strategy Tactics Prototype Adapter",
    "Stealth Prototype Adapter",
    "Horror Prototype Adapter",
    "Narrative Adventure Prototype Adapter",
    "Rhythm Game Prototype Adapter",
    "Racing Prototype Adapter",
    "Tower Defense Prototype Adapter",
    "Core Responsibility Decomposition",
    "Interface Abstractness Audit",
    "Process State Result Pattern",
    "Result Resolver Decision Rule",
    "Risk Explanation Required Format",
    "Scoring Rubric With Caps",
    "Design Review Pipeline Template",
    "Review Mode / Implementation Mode",
    "Required Review Pipeline",
    "Gate 1: Responsibility Decomposition",
    "Gate 2: Interface / Event Separation",
    "Gate 3: Mutation API",
    "Gate 4: Code Consistency",
    "Gate 5: Self-Contradiction Check",
    "Gate 6: RAG Evidence Citation",
    "Gate 7: Final Self Audit Score",
    "Hard Failure Conditions",
    "점수 제한",
    "Failure Pattern 1: 설계 리뷰에서 C++ 구현처럼 보이는 코드 블록을 출력함",
    "Failure Pattern 2: 인터페이스가 대상 내부 상태를 과도하게 노출함",
    "Failure Pattern 3: 프로세스 이벤트와 상태 이벤트를 한 소유자로 뭉침",
    "Failure Pattern 4: PlayerController 책임을 과도하게 빼앗음",
    "Failure Pattern 5: 나쁜 예시 코드가 다른 종류의 오류를 가르침",
    "Failure Pattern 6: RAG를 검색했다고 말하지만 근거가 약함",
    "Failure Pattern 7: 숨은 사고 과정을 최종 답변에 노출함",
    "Failure Pattern 8: 자체 점수와 실제 출력이 불일치함",
    "Failure Pattern 9: RequestX와 ApplyXSuccess의 소유자를 섞음",
    "Pattern 9: RequestX와 ApplyXSuccess의 소유자를 섞음",
    "Failure Pattern 10: 프로세스 성공 판정과 대상 효과 적용을 혼동함",
    "Pattern 10: 프로세스 성공 판정과 대상 효과 적용을 혼동함",
    "Failure Pattern 11: 내부 mutation 메서드를 외부 Blueprint API처럼 노출함",
    "Pattern 11: 내부 mutation 메서드를 외부 Blueprint API처럼 노출함",
    "Failure Pattern 12: AddX/CoolDown 같은 일반 mutation을 안전하다고 착각함",
    "Pattern 12: AddX/CoolDown 같은 일반 mutation을 안전하다고 착각함",
    "Failure Pattern 13: Unreal Damage API를 확인 없이 정확한 코드처럼 씀",
    "Pattern 13: Unreal Damage API를 확인 없이 정확한 코드처럼 씀",
    "Failure Pattern 14: 낮은 자체 점수를 그대로 출력하고 끝냄",
    "Pattern 14: 낮은 자체 점수를 그대로 출력하고 끝냄",
    "Failure Pattern 15: 질문 뒤에 의사결정을 미룸",
    "Pattern 15: 질문 뒤에 의사결정을 미룸",
    "Failure Pattern 16: 수행자 요구를 무시하고 Target-owned process로 되돌아감",
    "Failure Pattern 17: Interface가 구체 클래스 포인터에 의존함",
    "Failure Pattern 18: 같은 API 이름을 금지와 권장에 동시에 사용함",
    "Failure Pattern 19: CancelXRequest와 GetXProgress의 소유권을 설명하지 않음",
    "Failure Pattern 20: optional interface function의 기본값을 숨김",
    "Failure Pattern 21: 진행형 프로세스를 모든 Target Tick으로 처리함",
    "Failure Pattern 22: Blueprint interface Execute_ 호출과 virtual 호출을 섞음",
    "Failure Pattern 23: 같은 행동의 이름이 문서 안에서 바뀜",
    "Failure Pattern 24: 중복 요청, 취소, 실패 경로를 성공 경로 뒤에 숨김",
    "Failure Pattern 25: 자기평가와 수정안이 연결되지 않음",
    "Process Ownership Rules",
    "Default: Performer-Owned Process",
    "Target-Owned Process Is a Different Architecture",
    "Process Event vs State Event",
    "Recommended Flow",
    "Avoid Concrete Class Dependency In Interfaces",
    "Cancel, Duplicate, Failure Contract",
    "Timer Over Tick",
    "Receivable Action Interface Scope",
    "Transient Selection Ownership",
    "Process SSOT and State SSOT",
    "External Command vs Internal Mutation",
    "Progress Action Command Direction",
    "Unreal Damage API Caution",
    "Self-Contradiction Gate",
    "Declaration Consistency Gate",
    "Interface / Event Separation Gate",
    "Generic Setter Ban",
    "Code Example Mode Gate",
    "Damage Responsibility Gate",
    "RAG Citation Gate",
    "State Change Event Gate",
    "WeaponComponent Responsibilities",
    "Target Responsibilities",
    "Interface Boundary",
    "Preferred Flow",
    "API Naming",
    "Core Rule",
    "Purpose",
    "목적",
    "핵심 원칙",
    "데미지 책임 분리",
]


DEFAULT_SYSTEM_PROMPT = """You are an Unreal Engine 5.8 C++ assistant.
Use the provided context first. If context is insufficient, say what is missing.
Answer in Korean by default. Include C++ examples when useful and cite sources."""

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[가-힣]+")


def load_prompt(path: str | None) -> str:
    if not path:
        return DEFAULT_SYSTEM_PROMPT
    prompt_path = Path(path)
    if not prompt_path.exists():
        return DEFAULT_SYSTEM_PROMPT
    text = prompt_path.read_text(encoding="utf-8").strip()
    if "compact_system" in prompt_path.name:
        base = prompt_path.parent / "lmstudio_compact_mcp_base.md"
        if base.is_file():
            return base.read_text(encoding="utf-8").strip() + "\n\n---\n\n" + text
    return text


def make_fts_query(query: str) -> str:
    terms = TERM_RE.findall(query)
    terms = [term for term in terms if len(term) > 1]
    if not terms:
        terms = TERM_RE.findall(query) or [query]
    return " OR ".join(f'"{term}"' for term in terms[:24])


def search(index: Path, query: str, top_k: int) -> list[dict]:
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    fts_query = make_fts_query(query)
    rows = conn.execute(
        """
        select
            chunks.chunk_id,
            chunks.source,
            chunks.title,
            chunks.locator,
            chunks.chunk_index,
            chunks.text,
            bm25(chunks_fts) as score
        from chunks_fts
        join chunks on chunks_fts.rowid = chunks.rowid
        where chunks_fts match ?
        order by score
        limit ?
        """,
        (fts_query, top_k),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def source_type_label(source: str) -> str:
    return SOURCE_TYPE_LABELS.get(source, source or "Unknown source")


def infer_section(row: dict) -> str:
    text = str(row.get("text") or "")
    title = str(row.get("title") or "").strip()
    generic_labels = {"Purpose", "목적", "Core Rule", "핵심 원칙"}
    for label in SECTION_LABELS:
        if label in generic_labels:
            continue
        if label in text:
            return label
    headings = re.findall(r"(?:^|\s)#{2,6}\s+([^#]+?)(?=\s+#{1,6}\s+|$)", text)
    for heading in headings:
        heading = re.sub(r"\s+", " ", heading).strip()
        if heading.startswith("검색 키워드"):
            continue
        for label in SECTION_LABELS:
            numbered_label = re.compile(rf"^\d+\.\s+{re.escape(label)}\b")
            if heading == label or heading.startswith(label + " ") or numbered_label.match(heading):
                match = numbered_label.match(heading)
                return match.group(0) if match else label
        heading = re.split(r"\s+검색 키워드:|\s+-\s+|\s+```", heading, maxsplit=1)[0].strip()
        if heading and heading != title:
            return heading[:120]
    return f"chunk {row.get('chunk_index')}"


def citation_label(row: dict) -> str:
    source = str(row.get("source") or "")
    return f"{source_type_label(source)}: {row.get('title')} > {infer_section(row)}"


def make_context(rows: list[dict]) -> str:
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        metadata_line = (
            f"Resolved Mode: {row.get('resolved_mode', '')}; "
            f"Layer: {row.get('layer', '')}; "
            f"Project: {row.get('project', '')}; "
            f"Genre: {row.get('genre', '')}; "
            f"Extension: {row.get('extension', '')}; "
            f"Symbol: {row.get('symbol_kind', '')} {row.get('symbol_name', '')}; "
            f"Module: {row.get('module_name', '')}; "
            f"Error: {row.get('error_code', '')} {row.get('error_file', '')}"
        )
        parts.append(
            "\n".join(
                [
                    f"[RAG Result {index}]",
                    f"Evidence Type: {source_type_label(str(row['source']))}",
                    f"Citation Label: {citation_label(row)}",
                    f"Title: {row['title']}",
                    f"Locator: {row['locator']}",
                    metadata_line,
                    f"Section: {infer_section(row)}",
                    f"Chunk: {row['chunk_index']}",
                    "Text:",
                    row["text"],
                ]
            )
        )
    return "\n\n---\n\n".join(parts)


def get_lmstudio_models(base_url: str, timeout: int) -> list[str]:
    request = Request(base_url.rstrip("/") + "/models", method="GET")
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return [item["id"] for item in result.get("data", []) if item.get("id")]


def resolve_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model

    models = get_lmstudio_models(args.lmstudio_url, args.timeout)
    if not models:
        raise SystemExit("No LM Studio models are available. Load a model in LM Studio first.")

    selected = models[0]
    if len(models) > 1:
        print("No --model supplied; using the first LM Studio model.", file=sys.stderr)
        print("Available models:", file=sys.stderr)
        for model in models:
            print(f"- {model}", file=sys.stderr)
    print(f"Using LM Studio model: {selected}", file=sys.stderr)
    return selected


def ask_lmstudio(args: argparse.Namespace, system_prompt: str, context: str) -> str:
    model = resolve_model(args)
    user_prompt = f"""Context:
{context}

Question:
{args.query}
"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": args.temperature,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        args.lmstudio_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=args.timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def apply_model_profile_from_args(args: argparse.Namespace) -> str:
    """Select a sampling profile from the loaded LM Studio model before sizing retrieval."""
    if not getattr(args, "ask_lmstudio", False) or getattr(args, "sampling_profile", ""):
        return ""
    model = resolve_model(args)
    args.model = model
    return set_sampling_profile_for_model(model)


def main(args: argparse.Namespace) -> None:
    index = Path(args.index)
    if not index.exists():
        raise SystemExit(f"index does not exist: {index}")

    apply_model_profile_from_args(args)
    policy = profile_agent_policy(args.sampling_profile)
    if args.top_k <= 0:
        args.top_k = int(policy.get("defaultTopK") or 6)
    if args.candidate_limit <= 0:
        scale = int(policy.get("candidateLimitScale") or 20)
        args.candidate_limit = max(40, args.top_k * scale)

    projects = args.project or active_project_names()

    rows = search_index(
        index,
        args.query,
        args.top_k,
        SearchOptions(
            mode=args.mode,
            sources=args.source,
            projects=projects,
            layers=args.layer,
            doc_types=args.doc_type,
            genres=args.genre,
            extensions=args.extension,
            required_terms=args.required_term,
            candidate_limit=args.candidate_limit,
        ),
    )
    if not rows:
        print("No matching chunks found.")
        return

    system_prompt_path = args.system_prompt
    if system_prompt_path == "auto":
        system_prompt_path = str(policy.get("recommendedSystemPrompt") or "prompts/unreal_cpp_assistant.md")
    system_prompt = load_prompt(system_prompt_path)
    resolved_mode = str(rows[0].get("resolved_mode") or args.mode)
    context = assemble_context(rows, args.query, resolved_mode)

    if args.ask_lmstudio:
        print(ask_lmstudio(args, system_prompt, context))
        return

    print("## Retrieved Context\n")
    print(context)
    print("\n## Prompt For LM Studio\n")
    if args.print_prompt:
        print(system_prompt)
    else:
        print(f"System prompt: {system_prompt_path} ({len(system_prompt)} chars; pass --print-prompt to print it)")
    print("\nUser question:")
    print(args.query)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search the RAG index and optionally call LM Studio.")
    parser.add_argument("query")
    parser.add_argument("--index", default="data/unreal58/rag.sqlite")
    parser.add_argument("--top-k", type=int, default=0, help="Defaults to the active model profile.")
    from rag_modes import MODE_ENUM

    parser.add_argument(
        "--mode",
        choices=list(MODE_ENUM),
        default="auto",
        help="Search intent. auto detects from the query.",
    )
    parser.add_argument("--source", action="append", default=[], help="Filter source. Repeat or use comma-separated values.")
    parser.add_argument("--project", action="append", default=[], help="Filter local project name.")
    parser.add_argument("--layer", action="append", default=[], help="Filter inferred RAG layer.")
    parser.add_argument("--doc-type", action="append", default=[], help="Filter inferred document type.")
    parser.add_argument("--genre", action="append", default=[], help="Filter inferred game genre.")
    parser.add_argument("--extension", action="append", default=[], help="Filter file extension such as .h, .cpp, .md.")
    parser.add_argument("--required-term", action="append", default=[], help="Require a literal term in the returned row.")
    parser.add_argument("--candidate-limit", type=int, default=0, help="Defaults to top_k * active profile scale.")
    parser.add_argument("--system-prompt", default="auto")
    parser.add_argument("--sampling-profile", default="", help="Override UNREAL_RAG_MODEL_PROFILE for this query.")
    parser.add_argument("--print-prompt", action="store_true", help="Print the full system prompt in query mode.")
    parser.add_argument("--ask-lmstudio", action="store_true")
    parser.add_argument("--lmstudio-url", default="http://localhost:1234/v1")
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
