#!/usr/bin/env python
"""Engine identity and version policy for the Unreal static validators.

The validators are lexical and their engine-fact tables are validated against
specific engine versions (``validatedEngineVersions`` in the contract file).
This module reads the project's engine identity through the single owners
(``workspace_paths`` for root resolution, ``engine_identity`` for reading) and
lowers the confidence of findings that depend on those tables when the engine
is not one of the validated versions. It never assumes a default version.

``EngineHeaders`` gives the engine-backed rules bounded access to the installed
engine's public headers (the same filename catalog and bounded reads as
``engine_header_evidence``). Every lookup answers found / absent / unknown;
only a complete, unlimited observation may answer absent, and an unavailable
engine answers unknown, so engine rules degrade to no finding.
"""
from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import engine_header_evidence as _evidence
from engine_identity import describe_unreal_engine_identity
from unreal_contracts import engine_version_tier
from unreal_static_model import Finding

# Findings whose correctness depends on the version-specific override table.
VERSION_DEPENDENT_FINDING_CODES = frozenset({"INVALID_UNREAL_LIFECYCLE_OVERRIDE"})


def _unknown(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unknown", "engine": "unreal", "reason": reason[:180], **extra}


def resolve_static_engine_identity(project_root: Path) -> dict[str, Any]:
    """Identity of the engine the project at ``project_root`` is associated with."""
    root = Path(project_root)
    descriptors = sorted(root.glob("*.uproject")) if root.is_dir() else []
    if len(descriptors) != 1:
        return _unknown("expected exactly one .uproject at the project root", projectRoot=str(root))
    try:
        payload = json.loads(descriptors[0].read_text(encoding="utf-8-sig"))
        association = payload.get("EngineAssociation", "") if isinstance(payload, dict) else ""
        if not isinstance(association, str):
            return _unknown("EngineAssociation is not a string", projectRoot=str(root))
        from workspace_paths import resolve_engine_root_for_association

        resolution = resolve_engine_root_for_association(association.strip(), root)
    except (OSError, ValueError) as exc:
        return _unknown(f"project descriptor unreadable: {exc}", projectRoot=str(root))
    if resolution.get("ok") is not True or not resolution.get("engineRoot"):
        return _unknown(str(resolution.get("error") or "engine root not resolved"),
                        projectRoot=str(root), engineAssociation=association.strip())
    identity = describe_unreal_engine_identity(Path(str(resolution["engineRoot"])))
    return {**identity, "engineAssociation": association.strip()}


def apply_engine_version_policy(findings: list[Finding], identity: dict[str, Any],
                                contracts: dict[str, Any] | None) -> list[Finding]:
    """Downgrade version-dependent findings unless the engine is validated."""
    if not contracts or not any(str(f.code) in VERSION_DEPENDENT_FINDING_CODES for f in findings):
        return findings
    observed = identity.get("version") if identity.get("status") == "observed" else None
    tier = engine_version_tier(contracts, observed)
    if tier == "validated":
        return findings
    validated = ", ".join(contracts["validatedEngineVersions"])
    result: list[Finding] = []
    for finding in findings:
        if str(finding.code) not in VERSION_DEPENDENT_FINDING_CODES:
            result.append(finding)
            continue
        metadata = dict(finding.metadata or {})
        limitations = list(metadata.get("limitations") or [])
        limitations.append(f"override table validated on {validated}")
        metadata.update({"confidence": "medium", "engineVersionTier": tier, "limitations": limitations})
        result.append(replace(finding, severity="warning", metadata=metadata))
    return result


ENGINE_ANALYSIS_KIND = "engine_header_lexical"
ENGINE_LIMITATIONS = (
    "engine headers are read lexically: macros, templates and conditional compilation are not expanded",
    "only public Runtime/Editor/Plugins headers are catalogued",
)
# Per validation run: the catalog is cached per process, reads are not.
_FILE_LIMIT_BYTES = 1_048_576
_RUN_READ_LIMIT_BYTES = 24 * 1_048_576
_RUN_FILE_LIMIT = 96
_RUN_SECONDS = 20.0
_CHAIN_MAX_TYPES = 32
_TYPE_CACHE: OrderedDict[tuple[str, str], "EngineType"] = OrderedDict()
_TYPE_CACHE_LIMIT = 512
_SPECIFIERS = re.compile(r"\b(?:virtual|static|inline|FORCEINLINE|FORCENOINLINE|explicit|constexpr|UE_NODISCARD|"
                         r"mutable|extern|[A-Z0-9_]+_API)\b")


@dataclass
class EngineMember:
    name: str
    kind: str  # "method" | "field"
    line: int
    reflected: str | None  # "UFUNCTION" | "UPROPERTY" | None
    return_type: str = ""
    params: list[str] = field(default_factory=list)
    const: bool = False
    override: bool = False
    value_type: str = ""  # a field's declared type


@dataclass
class EngineType:
    name: str
    header: Path
    parents: list[str]
    members: dict[str, list[EngineMember]]
    complete: bool


@dataclass
class MemberLookup:
    status: str  # "found" | "absent" | "unknown"
    members: list[EngineMember] = field(default_factory=list)
    declared_in: str = ""
    header: Path | None = None
    chain: list[str] = field(default_factory=list)
    reason: str = ""


def _top_level(text: str, chars: str) -> list[int]:
    depth, found = 0, []
    for index, char in enumerate(text):
        if depth == 0 and char in chars:
            found.append(index)
        if char in "(<[{":
            depth += 1
        elif char in ")>]}" and text[index - 1:index] != "-":
            depth = max(0, depth - 1)
    return found


def split_parameters(params: str) -> list[str]:
    parts, start = [], 0
    for index in _top_level(params, ","):
        parts.append(params[start:index])
        start = index + 1
    parts.append(params[start:])
    return [part.strip() for part in parts if part.strip() and part.strip() != "void"]


def normalize_parameter_type(param: str) -> str:
    """``struct FDamageEvent const& Event = X`` and ``const FDamageEvent &`` compare equal."""
    value = param.split("=")[0]
    value = re.sub(r"\b(?:struct|class|enum)\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    # Drop a trailing parameter name (not a lone type such as ``float``).
    named = re.match(r"^(.*?[\w>*&\s])\s*\b([A-Za-z_]\w*)\s*(\[[^\]]*\])?$", value)
    if named and re.search(r"[\w>*&]", named.group(1)) and named.group(1).strip() not in {"", "const", "unsigned", "signed"}:
        value = named.group(1)
    const = bool(re.search(r"\bconst\b", value))
    core = re.sub(r"\bconst\b", "", value)
    core = re.sub(r"\s+", "", core)
    return ("const " if const else "") + core


def normalize_return_type(value: str) -> str:
    value = _SPECIFIERS.sub(" ", re.sub(r"\b(?:struct|class|enum)\s+", "", value))
    return re.sub(r"\s+", "", value)


def _strip_member_segment(segment: str) -> tuple[str, str | None]:
    macros = re.findall(r"\b(UPROPERTY|UFUNCTION)\s*\(", segment)
    value = "\n".join(line for line in segment.split("\n") if not line.lstrip().startswith("#"))
    value = _evidence._without_reflection_annotations(value)
    value = re.sub(r"\b(?:public|protected|private)\s*:", " ", value)
    value = re.sub(r"\btemplate\s*<[^;{}]*?>\s*(?=[A-Za-z_])", " ", value)
    return value.strip(), (macros[-1] if macros else None)


def _parse_member(segment: str, owner: str, line: int) -> list[EngineMember]:
    value, reflected = _strip_member_segment(segment)
    if not value or "operator" in value:
        return []
    if re.match(r"(?:typedef|using|friend|static_assert|namespace)\b", value):
        return []
    if re.fullmatch(r"(?:class|struct|union|enum(?:\s+class)?)\s+(?:[A-Z0-9_]+_API\s+)?\w+(?:\s*:[^;]*)?", value, re.S):
        return []
    parens = [i for i in _top_level(value, "(")]
    equals = [i for i in _top_level(value, "=") if value[i + 1:i + 2] != "=" and value[i - 1:i] not in "=!<>"]
    if parens and (not equals or equals[0] > parens[0]):
        head = value[:parens[0]].rstrip()
        name_match = re.search(r"(~?[A-Za-z_]\w*)$", head)
        if not name_match:
            return []
        name = name_match.group(1)
        if name in {owner, "~" + owner} or name.startswith("~") or (name.isupper() and head == name):
            return []
        depth, close = 0, None
        for index in range(parens[0], len(value)):
            depth += (value[index] == "(") - (value[index] == ")")
            if depth == 0:
                close = index
                break
        if close is None:
            return []
        tail = value[close + 1:]
        return [EngineMember(name, "method", line, reflected,
                             return_type=normalize_return_type(head[:name_match.start()]),
                             params=split_parameters(value[parens[0] + 1:close]),
                             const=bool(re.match(r"\s*const\b", tail)),
                             override=bool(re.search(r"\b(?:override|final)\b", tail)))]
    declaration = value[:equals[0]] if equals else value
    declaration = re.sub(r":\s*\d+\s*$", "", declaration.strip())
    members, value_type = [], ""
    for index, part in enumerate(split_parameters(declaration)):
        part = re.sub(r"\[[^\]]*\]\s*$", "", part).strip()
        name_match = re.search(r"([A-Za-z_]\w*)$", part)
        if not name_match or (index == 0 and not re.search(r"\S\s*[\s*&>]\s*" + re.escape(name_match.group(1)) + "$", part)):
            continue
        if index == 0:
            value_type = re.sub(r"\s+", " ", _SPECIFIERS.sub(" ", part[:name_match.start()])).strip()
        members.append(EngineMember(name_match.group(1), "field", line, reflected, value_type=value_type))
    return members


def parse_class_members(text: str, type_name: str) -> tuple[list[str], dict[str, list[EngineMember]]] | None:
    """Direct bases and depth-0 members of the single definition of ``type_name``."""
    masked = _evidence._lexical_source(text)
    region = _evidence._owner_region(masked, type_name)
    if not region:
        return None
    start, opening, end = region
    parents = [base.split("::")[-1] for base in re.findall(
        r"\b(?:public|protected|private)\s+(?:virtual\s+)?([A-Za-z_]\w*(?:::\w+)*)", masked[start:opening])][:8]
    if not parents and re.search(r"\w\s*:\s*[A-Za-z_]", masked[start:opening]):
        # ``struct FFoo : FBase`` (implicit access) still has a base to search.
        parents = re.findall(r"\w\s*:\s*([A-Za-z_]\w*)", masked[start:opening])[:1]
    members: dict[str, list[EngineMember]] = {}
    index, segment_start, paren = opening + 1, opening + 1, 0
    while index < end - 1:
        char = masked[index]
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif paren == 0 and char in ";{}":
            segment = masked[segment_start:index]
            for member in _parse_member(segment, type_name, 0):
                pattern = r"\b" + re.escape(member.name) + (r"\s*\(" if member.kind == "method" else r"\b")
                hits = list(re.finditer(pattern, segment))
                offset = (hits[0] if member.kind == "method" else hits[-1]).start() if hits else 0
                member.line = text.count("\n", 0, segment_start + offset) + 1
                members.setdefault(member.name, []).append(member)
            if char == "{":
                depth = 1
                index += 1
                while index < end - 1 and depth:
                    depth += (masked[index] == "{") - (masked[index] == "}")
                    index += 1
                index -= 1
            segment_start = index + 1
        index += 1
    return parents, members


class EngineHeaders:
    """Bounded engine-header facts for one validation run."""

    def __init__(self, root: Path, digest: str, version: str = "") -> None:
        self.root = root
        self.digest = digest
        self.version = version
        self._deadline = time.monotonic() + _RUN_SECONDS
        self._index: dict[str, list[Path]] | None = None
        self._index_complete = False
        self._index_failed = False
        self._read_bytes = 0
        self._files = 0
        self._missing: dict[tuple[str, tuple[str, ...]], bool] = {}

    @classmethod
    def from_identity(cls, identity: dict[str, Any] | None) -> "EngineHeaders | None":
        identity = identity or {}
        if identity.get("status") != "observed" or not identity.get("sha256"):
            return None
        root = Path(str(identity.get("engineRoot") or ""))
        if not str(identity.get("engineRoot") or "") or not (root / "Engine").is_dir():
            return None
        return cls(root.resolve(), str(identity["sha256"]), str(identity.get("version") or ""))

    def uri(self, path: Path | None) -> str:
        return _evidence._engine_uri(Path(path), self.root) if path else ""

    def index(self) -> dict[str, list[Path]] | None:
        if self._index is None and not self._index_failed:
            reasons: set[str] = set()
            try:
                self._index = _evidence._public_header_index(self.root, self.digest, None,
                                                             _evidence._LOCAL_CACHE_GENERATION, reasons)
            except OSError:
                self._index_failed = True
                return None
            self._index_complete = not reasons
        return self._index

    @property
    def index_complete(self) -> bool:
        return self.index() is not None and self._index_complete

    # -- includes -----------------------------------------------------------------------------
    def _include_root_relative(self, path: Path) -> str:
        posix = path.as_posix()
        for marker in ("/Public/", "/Classes/", "/Internal/"):
            position = posix.rfind(marker)
            if position >= 0:
                return posix[position + len(marker):]
        return path.name

    def _strictly_includable(self, path: Path, include: str) -> bool:
        posix = path.as_posix().casefold()
        wanted = include.casefold()
        if any(posix.endswith(marker + wanted) for marker in ("/public/", "/classes/", "/internal/")):
            return True
        if not posix.endswith("/" + wanted):
            return False
        include_root = path
        for _ in include.split("/"):
            include_root = include_root.parent
        return any(include_root.glob("*.Build.cs"))

    def resolve_include(self, include: str) -> dict[str, Any]:
        """``resolved``, ``wrong_path`` (file exists elsewhere), ``not_found`` or ``unknown``."""
        normalized = include.replace("\\", "/").lstrip("/")
        index = self.index()
        if index is None or normalized.startswith("../") or "/./" in normalized:
            return {"status": "unknown"}
        name = Path(normalized).name
        candidates = [path for path in index.get(Path(name).stem.casefold(), []) if path.name.casefold() == name.casefold()]
        if any(self._strictly_includable(path, normalized) for path in candidates):
            return {"status": "resolved", "matches": [path for path in candidates if self._strictly_includable(path, normalized)]}
        if "/" in normalized and any(path.as_posix().casefold().endswith("/" + normalized.casefold()) for path in candidates):
            # A module may add its own include path; not provable either way.
            return {"status": "unknown"}
        developer = self.root / "Engine" / "Source" / "Developer"
        if developer.is_dir():
            for module in developer.iterdir():
                if any((module / folder / normalized).is_file() for folder in ("Public", "Classes")):
                    return {"status": "resolved", "matches": []}
        if candidates:
            ranked = sorted(candidates, key=lambda path: (_evidence._candidate_header_rank(path), len(path.parts)))
            suggestions = list(dict.fromkeys(self._include_root_relative(path) for path in ranked))[:3]
            return {"status": "wrong_path", "suggestions": suggestions, "matches": ranked[:3]}
        if not self._index_complete:
            return {"status": "unknown"}
        return {"status": "not_found", "suggestions": self._similar_headers(Path(name).stem)}

    def _similar_headers(self, stem: str) -> list[str]:
        import difflib

        index = self.index() or {}
        names = difflib.get_close_matches(stem.casefold(), list(index), n=3, cutoff=0.75)
        found = []
        for key in names:
            path = sorted(index[key], key=_evidence._candidate_header_rank)[0]
            found.append(self._include_root_relative(path))
        return found

    def include_paths(self, includes: list[str]) -> list[Path]:
        paths: list[Path] = []
        for include in includes:
            resolution = self.resolve_include(include)
            paths.extend(resolution.get("matches") or [])
        return list(dict.fromkeys(paths))

    # -- types and members --------------------------------------------------------------------
    def _budget_left(self) -> bool:
        return (time.monotonic() < self._deadline and self._files < _RUN_FILE_LIMIT
                and self._read_bytes < _RUN_READ_LIMIT_BYTES)

    def type_info(self, type_name: str, hints: list[Path] | tuple[Path, ...] = ()) -> EngineType | None | str:
        """The engine definition of ``type_name``; None when not located; "limit" when budget ran out."""
        key = (self.digest, type_name)
        if key in _TYPE_CACHE:
            _TYPE_CACHE.move_to_end(key)
            return _TYPE_CACHE[key]
        hint_key = (type_name, tuple(str(path) for path in hints))
        if hint_key in self._missing:
            return None
        index = self.index()
        if index is None:
            return "limit"
        candidates = list(dict.fromkeys([*hints, *_evidence._type_candidates(index, type_name)[:4]]))
        for header in candidates:
            if not self._budget_left():
                return "limit"
            observation = _evidence._read_bounded_header(header, self.root, _RUN_READ_LIMIT_BYTES - self._read_bytes,
                                                         limit=_FILE_LIMIT_BYTES)
            if isinstance(observation, str):
                continue
            raw, complete = observation
            self._files += 1
            self._read_bytes += len(raw)
            text = raw.decode("utf-8-sig", errors="replace")
            if not re.search(r"\b" + re.escape(type_name) + r"\b", text):
                continue
            parsed = parse_class_members(text, type_name)
            if parsed is None:
                continue
            info = EngineType(type_name, header, parsed[0], parsed[1], complete)
            _TYPE_CACHE[key] = info
            while len(_TYPE_CACHE) > _TYPE_CACHE_LIMIT:
                _TYPE_CACHE.popitem(last=False)
            return info
        self._missing[hint_key] = True
        return None

    def lookup_member(self, owner: str, symbol: str, hints: list[Path] | tuple[Path, ...] = ()) -> MemberLookup:
        """Breadth-first over ``owner`` and its engine bases; absent only when every type was read in full."""
        queue: list[tuple[str, list[str]]] = [(owner, [owner])]
        visited: set[str] = set()
        while queue:
            type_name, chain = queue.pop(0)
            if type_name in visited:
                continue
            visited.add(type_name)
            if len(visited) > _CHAIN_MAX_TYPES:
                return MemberLookup("unknown", chain=chain, reason="type_limit")
            info = self.type_info(type_name, hints)
            if info == "limit":
                return MemberLookup("unknown", chain=chain, reason="read_limit")
            if info is None:
                return MemberLookup("unknown", chain=chain, reason=f"type_not_located:{type_name}")
            assert isinstance(info, EngineType)
            if symbol in info.members:
                return MemberLookup("found", info.members[symbol], type_name, info.header, chain)
            if not info.complete:
                return MemberLookup("unknown", chain=chain, reason=f"header_truncated:{type_name}")
            queue.extend((parent, chain + [parent]) for parent in info.parents)
        return MemberLookup("absent", chain=[owner, *sorted(visited - {owner})])

    def metadata(self, rule_id: str, confidence: str, evidence: list[dict[str, Any]],
                 limitations: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
        return {"ruleId": rule_id, "analysisKind": ENGINE_ANALYSIS_KIND, "confidence": confidence,
                "engineVersion": self.version, "evidenceRefs": evidence,
                "assumptions": ["the installed engine is the one this project builds against"],
                "limitations": [*ENGINE_LIMITATIONS, *limitations]}


# Engine names that model-written code has invented in real failed builds. Each entry is
# (pattern, owner type, member group or None, replacement advice). The installed engine,
# when readable, can only withdraw a finding (a definition or member was found).
KNOWN_INVALID_ENGINE_SYMBOLS: tuple[tuple[re.Pattern[str], str, int | None, str], ...] = (
    (re.compile(r"\bEKeyEvent\s*::\s*\w+"), "EKeyEvent", None,
     "EKeyEvent has no usable enumerators (Enhanced Input only forward-declares it). "
     "UInputComponent::BindKey takes EInputEvent, e.g. IE_Pressed or IE_Released."),
    (re.compile(r"\bFCollisionObjectTypes\b"), "FCollisionObjectTypes", None,
     "There is no FCollisionObjectTypes. Object-type traces take FCollisionObjectQueryParams, e.g. "
     "FCollisionObjectQueryParams(FCollisionObjectQueryParams::AllStaticObjects), or ECollisionChannel values such as ECC_WorldStatic."),
    (re.compile(r"\bEInputValueType\b"), "EInputValueType", None,
     "The Enhanced Input value type enum is EInputActionValueType (InputActionValue.h)."),
    (re.compile(r"\bUEnhancedInputUser\b"), "UEnhancedInputUser", None,
     "There is no UEnhancedInputUser. Add mapping contexts through UEnhancedInputLocalPlayerSubsystem "
     "(EnhancedInputSubsystems.h), obtained with ULocalPlayer::GetSubsystem."),
    (re.compile(r"\bFKey\s*::\s*([A-Z][A-Za-z0-9_]*)\b(?!\s*\()"), "FKey", 1,
     "Key constants are static members of EKeys (InputCoreTypes.h), e.g. EKeys::{member}."),
)


def validate_known_invalid_engine_symbols(path: Path, text: str, root: Path,
                                          engine: "EngineHeaders | None" = None,
                                          hints: list[Path] | tuple[Path, ...] = ()) -> list[Finding]:
    from cpp_parse_utils import mask_comments_and_strings

    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    masked = mask_comments_and_strings(text)
    for pattern, owner, member_group, advice in KNOWN_INVALID_ENGINE_SYMBOLS:
        for match in pattern.finditer(masked):
            member = match.group(member_group) if member_group else ""
            verified = "not_checked"
            if engine is not None:
                if member:
                    lookup = engine.lookup_member(owner, member, hints)
                    if lookup.status == "found":
                        continue
                    verified = "member_absent" if lookup.status == "absent" else "unknown"
                else:
                    info = engine.type_info(owner, hints)
                    if isinstance(info, EngineType):
                        continue
                    verified = "definition_not_located" if info is None else "unknown"
            symbol = f"{owner}::{member}" if member else owner
            findings.append(Finding(
                "error", rel, text.count("\n", 0, match.start()) + 1, "KNOWN_INVALID_ENGINE_SYMBOL",
                f"{symbol} is not an Unreal Engine API. {advice.format(member=member)}",
                metadata={"ruleId": "KNOWN_INVALID_ENGINE_SYMBOL", "analysisKind": "lexical",
                          "confidence": "high" if verified in {"member_absent", "definition_not_located"} else "medium",
                          "engineVerification": verified,
                          "evidenceRefs": [{"table": "KNOWN_INVALID_ENGINE_SYMBOLS", "symbol": symbol}],
                          "limitations": ["table of names seen invented in real failed builds; not exhaustive"]}))
    return findings


def engine_base_chain(class_name: str, bases: dict[str, str], project_classes: set[str] | dict[str, Any]) -> list[str] | None:
    """``[class, project bases..., first engine base]`` or None when the chain never leaves the project."""
    chain = [class_name]
    while len(chain) < 12:
        parent = bases.get(chain[-1])
        if not parent or parent in chain:
            return None
        chain.append(parent)
        if parent not in project_classes:
            return chain
    return None


__all__ = [
    "VERSION_DEPENDENT_FINDING_CODES",
    "ENGINE_ANALYSIS_KIND",
    "EngineHeaders",
    "EngineMember",
    "EngineType",
    "KNOWN_INVALID_ENGINE_SYMBOLS",
    "MemberLookup",
    "apply_engine_version_policy",
    "engine_base_chain",
    "normalize_parameter_type",
    "normalize_return_type",
    "parse_class_members",
    "resolve_static_engine_identity",
    "split_parameters",
    "validate_known_invalid_engine_symbols",
]
