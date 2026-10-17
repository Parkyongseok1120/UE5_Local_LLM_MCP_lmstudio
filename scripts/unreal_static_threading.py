#!/usr/bin/env python
"""Conservative thread, async-lifetime and subsystem checks (all advisory).

Every rule here is lexical: it recognizes an execution context or a
registration only from local source shape. Each finding carries metadata
(contract ids, analysisKind, confidence, limitations) so a reader does not
mistake a risk for a proven defect. When the context cannot be established
(unknown thread, unknown subsystem collection, cleanup delegated to a helper)
the rule stays silent rather than guessing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cpp_parse_utils import find_balanced_parens, mask_comments_and_strings
from unreal_contracts import contract_revision, load_contracts_or_none
from unreal_static_lifecycle import lifecycle_inheritance_chain
from unreal_static_model import Finding
from unreal_static_scan import find_matching_brace, line_number

_CONTRACTS = load_contracts_or_none()
_REVISION = contract_revision(_CONTRACTS) if _CONTRACTS else None

_LAUNCH = re.compile(
    r"\b(?P<call>AsyncTask|Async|ParallelFor|UE::Tasks::Launch|FFunctionGraphTask::CreateAndDispatchWhenReady"
    r"|ENQUEUE_RENDER_COMMAND)\s*\(")
_LAMBDA = re.compile(
    r"\[(?P<cap>[^\[\]]*)\]\s*(?:\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\))?\s*(?:mutable\s*)?"
    r"(?:->\s*[\w:<>*&\s]+?\s*)?\{")
_METHOD = re.compile(r"\b(?P<cls>[A-Za-z_]\w*)::(?P<method>~?[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:override\s*)?\{")
# Timer APIs check IsInGameThread in the engine (contract ue.timer.game-thread);
# object creation/destruction is game-thread project policy.
_TIMER_API = re.compile(r"\b(?:SetTimer|SetTimerForNextTick|ClearTimer|ClearAllTimersForObject|GetWorldTimerManager"
                        r"|GetTimerManager)\s*\(")
_OBJECT_API = re.compile(r"\b(?:SpawnActor\w*|NewObject|DestroyActor|CreateDefaultSubobject|DuplicateObject"
                         r"|LoadObject|StaticLoadObject)\s*[<(]|\bDestroy\s*\(\s*\)")
_WEAK_GUARD = re.compile(r"\bWeak\w*|\bTWeakObjectPtr\b|\bIsValid\s*\(|\.IsValid\s*\(\s*\)")
_SUBSYSTEM_KINDS = ("UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem", "ULocalPlayerSubsystem")
_SETUP_METHODS = {"Initialize", "PostInitialize", "OnWorldBeginPlay"}
_TEARDOWN_METHODS = {"Deinitialize", "PreDeinitialize", "OnWorldEndPlay"}
_REGISTRATIONS = (
    ("delegate", re.compile(r"\b(?:F\w*Delegates|FCoreDelegates|FCoreUObjectDelegates|FWorldDelegates)\s*::\s*\w+"
                            r"(?:\s*\(\s*\))?\s*\.\s*Add(?:UObject|Raw|SP|Lambda|Static|Weak\w*)?\s*\(")),
    ("ticker", re.compile(r"\bAddTicker\s*\(")),
    ("timer", re.compile(r"\bSetTimer\s*\(")),
)
_CLEANUPS = {
    "delegate": re.compile(r"\.\s*(?:RemoveAll|Remove|RemoveDynamic|Clear)\s*\("),
    "ticker": re.compile(r"\bRemoveTicker\s*\("),
    "timer": re.compile(r"\b(?:ClearTimer|ClearAllTimersForObject)\s*\("),
}
_ENGINE_CALL = re.compile(r"^(?:Super|Collection|check\w*|ensure\w*|UE_LOG|GetWorld|GetGameInstance|GetSubsystem|Cast"
                          r"|IsValid|FTSTicker|GetCoreTicker)$")


def _meta(rule: str, contract_ids: list[str], confidence: str, limitations: list[str],
          evidence: list[dict[str, Any]] | None = None, assumptions: list[str] | None = None) -> dict[str, Any]:
    return {"ruleId": rule, "contractRevision": _REVISION, "contractIds": contract_ids, "analysisKind": "lexical",
            "confidence": confidence, "evidenceRefs": evidence or [], "assumptions": assumptions or [],
            "limitations": limitations}


def _method_regions(masked: str) -> list[tuple[int, int, str, str]]:
    regions = []
    for match in _METHOD.finditer(masked):
        open_index = match.end() - 1
        close = find_matching_brace(masked, open_index)
        if close > open_index:
            regions.append((open_index, close, match.group("cls"), match.group("method")))
    return regions


def _enclosing(regions: list[tuple[int, int, str, str]], offset: int) -> tuple[int, int, str, str] | None:
    inside = [r for r in regions if r[0] < offset < r[1]]
    return min(inside, key=lambda r: r[1] - r[0]) if inside else None


def _object_kind(class_name: str, bases: dict[str, str]) -> str:
    """uobject / native / unknown for the class that owns a lambda."""
    if class_name in bases and lifecycle_inheritance_chain(bases[class_name], bases) is not None:
        return "uobject"
    if re.match(r"^[UA][A-Z]", class_name) and not class_name.endswith("Proxy"):
        return "uobject_by_prefix"
    if re.match(r"^F[A-Z]", class_name) or class_name.endswith("Proxy"):
        return "native"
    return "unknown"


def _first_arg(args: str) -> str:
    depth = 0
    for index, char in enumerate(args):
        depth += char in "(<[{"
        depth -= char in ")>]}"
        if char == "," and depth == 0:
            return args[:index]
    return args


def _launch_sites(masked: str) -> list[dict[str, Any]]:
    """Async/task/render launches with their lambda body and execution context."""
    sites = []
    for match in _LAUNCH.finditer(masked):
        call = match.group("call")
        open_index = match.end() - 1
        close = find_balanced_parens(masked, open_index)
        if close < 0:
            continue
        args = masked[open_index + 1:close]
        search_from, search_to = open_index, close
        if call == "ENQUEUE_RENDER_COMMAND":
            follow = re.match(r"\s*\(", masked[close + 1:])
            if not follow:
                continue
            open2 = close + 1 + follow.end() - 1
            close2 = find_balanced_parens(masked, open2)
            if close2 < 0:
                continue
            search_from, search_to, context = open2, close2, "render"
        elif call == "ParallelFor":
            context = "worker_sync"
        elif call == "AsyncTask":
            context = "game_thread_deferred" if "GameThread" in _first_arg(args) else "worker"
        elif call == "Async":
            context = "game_thread_deferred" if "TaskGraphMainThread" in _first_arg(args) else "worker"
        elif call == "FFunctionGraphTask::CreateAndDispatchWhenReady":
            context = "game_thread_deferred" if "GameThread" in args else "worker"
        else:
            context = "worker"
        lam = _LAMBDA.search(masked, search_from, search_to)
        if not lam:
            continue
        body_open = lam.end() - 1
        body_close = find_matching_brace(masked, body_open)
        if body_close < 0 or body_close > search_to:
            continue
        sites.append({"call": call, "context": context, "offset": match.start(), "captures": lam.group("cap"),
                      "bodyOpen": body_open, "bodyClose": body_close})
    return sites


def _without_game_thread_hops(masked: str, site: dict[str, Any], sites: list[dict[str, Any]]) -> str:
    """Body text with nested game-thread lambdas blanked: their code runs on the game thread."""
    body = list(masked[site["bodyOpen"] + 1:site["bodyClose"]])
    for nested in sites:
        if nested is site or nested["context"] != "game_thread_deferred":
            continue
        if site["bodyOpen"] < nested["offset"] < site["bodyClose"]:
            start = nested["bodyOpen"] + 1 - (site["bodyOpen"] + 1)
            end = nested["bodyClose"] - (site["bodyOpen"] + 1)
            for index in range(max(0, start), min(len(body), end)):
                body[index] = " "
    return "".join(body)


def validate_thread_contracts(path: Path, text: str, root: Path, bases: dict[str, str] | None = None) -> list[Finding]:
    """GAME_THREAD_API_OFF_THREAD, ASYNC_UOBJECT_LIFETIME_RISK, RENDER_THREAD_UOBJECT_ACCESS_REVIEW."""
    bases = bases or {}
    rel = str(path.relative_to(root))
    masked = mask_comments_and_strings(text)
    if not _LAUNCH.search(masked):
        return []
    regions = _method_regions(masked)
    sites = _launch_sites(masked)
    findings: list[Finding] = []
    for site in sites:
        line = line_number(text, site["offset"])
        enclosing = _enclosing(regions, site["offset"])
        owner = enclosing[2] if enclosing else ""
        kind = _object_kind(owner, bases) if owner else "unknown"
        captures = [c.strip() for c in site["captures"].split(",") if c.strip()]
        body = masked[site["bodyOpen"] + 1:site["bodyClose"]]
        if site["context"] in {"worker", "worker_sync"}:
            worker_body = _without_game_thread_hops(masked, site, sites)
            timer = _TIMER_API.search(worker_body)
            obj = _OBJECT_API.search(worker_body)
            hit = timer or obj
            if hit:
                api = hit.group(0).rstrip("(<").strip()
                findings.append(Finding(
                    "warning", rel, line, "GAME_THREAD_API_OFF_THREAD",
                    f"{site['call']} runs this lambda off the game thread, and it calls {api}. Do the work on "
                    "copied data and return to the game thread with AsyncTask(ENamedThreads::GameThread, ...) "
                    "before touching timers or UObjects.",
                    metadata=_meta("GAME_THREAD_API_OFF_THREAD",
                                   ["ue.timer.game-thread"] if timer else ["ue.thread.uobject-game-thread"],
                                   "high" if timer else "medium",
                                   ["the worker context is inferred from the launch call",
                                    "helpers called from the lambda are not followed"],
                                   [{"launch": site["call"], "api": api}])))
        if site["context"] in {"worker", "game_thread_deferred"} and kind in {"uobject", "uobject_by_prefix"}:
            captures_this = "this" in captures or ("=" in captures and "this->" in body) \
                or ("&" in captures and re.search(r"\bthis\b", body))
            if captures_this and not _WEAK_GUARD.search(site["captures"] + body):
                findings.append(Finding(
                    "warning", rel, line, "ASYNC_UOBJECT_LIFETIME_RISK",
                    f"{owner} captures this in a {site['call']} lambda that runs after the current call returns; "
                    f"the {owner} may be destroyed first. Capture a TWeakObjectPtr and check it before use.",
                    metadata=_meta("ASYNC_UOBJECT_LIFETIME_RISK", ["ue.thread.uobject-game-thread"],
                                   "high" if kind == "uobject" else "medium",
                                   ["a lifetime guarantee established elsewhere is not visible to this check"],
                                   [{"launch": site["call"], "owner": owner}],
                                   ["the owner is a UObject" + (" (by U/A name prefix)" if kind != "uobject" else "")])))
        if site["context"] == "render":
            touches = re.search(r"\b(?:GetWorld|GetOwner|GetGameInstance)\s*\(|\bCast\s*<", body)
            if (("this" in captures and kind in {"uobject", "uobject_by_prefix"}) or touches):
                findings.append(Finding(
                    "warning", rel, line, "RENDER_THREAD_UOBJECT_ACCESS_REVIEW",
                    "This render command reads gameplay objects on the rendering thread. Pass copied values or a "
                    "render proxy into the command instead.",
                    metadata=_meta("RENDER_THREAD_UOBJECT_ACCESS_REVIEW", ["policy.render.game-objects"], "medium",
                                   ["render proxies and copied values are recognized only by name and capture shape"],
                                   [{"owner": owner}])))
    return findings


def _subsystem_kind(class_name: str, bases: dict[str, str]) -> str | None:
    base = bases.get(class_name)
    chain = lifecycle_inheritance_chain(base, bases) if base else None
    if not chain:
        return None
    return next((name for name in chain if name in _SUBSYSTEM_KINDS), None)


def validate_subsystem_contracts(path: Path, text: str, root: Path, bases: dict[str, str] | None = None) -> list[Finding]:
    """SUBSYSTEM_INITIALIZATION_DEPENDENCY_REVIEW and SUBSYSTEM_CLEANUP_NOT_OBSERVED."""
    bases = bases or {}
    rel = str(path.relative_to(root))
    masked = mask_comments_and_strings(text)
    if not any(method in masked for method in _SETUP_METHODS):
        return []
    by_class: dict[str, list[tuple[int, int, str]]] = {}
    for open_index, close, cls, method in _method_regions(masked):
        by_class.setdefault(cls, []).append((open_index, close, method))
    findings: list[Finding] = []
    for cls, methods in by_class.items():
        kind = _subsystem_kind(cls, bases)
        if kind is None:
            continue
        for open_index, close, method in methods:
            if method != "Initialize":
                continue
            body = masked[open_index + 1:close]
            declared = set(re.findall(r"InitializeDependency\s*<\s*(\w+)\s*>", body)) \
                | set(re.findall(r"InitializeDependency\s*\(\s*(\w+)::StaticClass", body))
            for used in sorted(set(re.findall(r"GetSubsystem\s*<\s*(\w+)\s*>", body)) - {cls} - declared):
                if _subsystem_kind(used, bases) != kind:
                    continue  # different collection or unknown kind: outside InitializeDependency
                direct_use = re.search(r"GetSubsystem\s*<\s*" + used + r"\s*>\s*\([^)]*\)\s*->", body)
                stored = re.search(r"(\w+)\s*=\s*[^;]*GetSubsystem\s*<\s*" + used + r"\s*>", body)
                used_now = direct_use or (stored and re.search(r"\b" + stored.group(1) + r"\s*->", body[stored.end():]))
                if not used_now:
                    continue  # storing a reference does not depend on initialization order
                offset = open_index + 1 + body.find(used)
                findings.append(Finding(
                    "warning", rel, line_number(text, offset), "SUBSYSTEM_INITIALIZATION_DEPENDENCY_REVIEW",
                    f"{cls}::Initialize uses {used} from the same {kind} collection without "
                    f"Collection.InitializeDependency<{used}>(); its initialization order is not guaranteed.",
                    metadata=_meta("SUBSYSTEM_INITIALIZATION_DEPENDENCY_REVIEW", ["ue.subsystem.initialize-dependency"],
                                   "medium", ["subsystem collections are resolved from scanned project headers"],
                                   [{"subsystem": cls, "uses": used, "collection": kind}])))
        setup = [(o, c) for o, c, m in methods if m in _SETUP_METHODS]
        teardown = [masked[o + 1:c] for o, c, m in methods if m in _TEARDOWN_METHODS]
        teardown_text = "\n".join(teardown)
        helper_calls = {name for name in re.findall(r"(?<![\w:.>])([A-Za-z_]\w*)\s*\(", teardown_text)
                        if not _ENGINE_CALL.match(name) and name not in {"if", "for", "while", "switch", "return"}}
        if helper_calls:
            continue  # cleanup may be delegated to a helper this check does not follow
        for open_index, close in setup:
            body = masked[open_index + 1:close]
            for name, pattern in _REGISTRATIONS:
                hit = pattern.search(body)
                if not hit or _CLEANUPS[name].search(teardown_text):
                    continue
                findings.append(Finding(
                    "warning", rel, line_number(text, open_index + 1 + hit.start()), "SUBSYSTEM_CLEANUP_NOT_OBSERVED",
                    f"{cls} registers a {name} during setup, but no matching removal was observed in "
                    f"{'/'.join(sorted(_TEARDOWN_METHODS))}. Remove it when the {kind} ends.",
                    metadata=_meta("SUBSYSTEM_CLEANUP_NOT_OBSERVED", ["ue.subsystem.lifecycle"], "medium",
                                   ["cleanup in a parent class or another file is not observed"],
                                   [{"subsystem": cls, "registration": name}])))
    return findings


def merge_overlapping_cleanup_findings(findings: list[Finding], timer_findings: list[Finding]) -> list[Finding]:
    """One cause, one warning: an existing timer finding on the same line wins."""
    timer_lines = {(f.path, f.line) for f in timer_findings if f.code == "TIMER_SET_WITHOUT_CLEAR"}
    return [f for f in findings
            if not (f.code == "SUBSYSTEM_CLEANUP_NOT_OBSERVED" and (f.path, f.line) in timer_lines)]


__all__ = ["merge_overlapping_cleanup_findings", "validate_subsystem_contracts", "validate_thread_contracts"]
