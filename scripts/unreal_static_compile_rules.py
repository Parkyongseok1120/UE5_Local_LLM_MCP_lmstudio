"""Bounded compile-readiness advisories grounded in project/installed headers.

These lexical checks do not authorize a write or replace UHT/UBT. In particular,
an exhausted header lookup is unknown, not proof that a member is absent.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import engine_header_evidence as engine
from unreal_static_model import Finding, CPP_HEADER_SUFFIXES, CPP_SOURCE_SUFFIXES
from unreal_static_scan import include_lines, line_number, iter_function_blocks


class CompileEvidence:
    def __init__(self, identity: dict):
        self.root = Path(identity["engineRoot"]) if identity.get("status") == "observed" and identity.get("engineRoot") else None
        self.identity = str(identity.get("sha256") or identity.get("version") or "unknown")
        self.index = None
        self.cache = {}
        self.lookups = 0
        self.deadline = None

    def headers(self):
        if self.index is None:
            reasons = set()
            self.index = engine._public_header_index(self.root, self.identity, None, engine._LOCAL_CACHE_GENERATION, reasons) if self.root else {}
            self.deadline = time.monotonic() + 3.0
        return self.index

    def member(self, owner: str, name: str):
        key = (owner, name)
        if key in self.cache:
            return self.cache[key]
        self.headers()
        if not self.root or self.lookups >= 24 or time.monotonic() >= self.deadline:
            return [], False
        self.lookups += 1
        result = engine.lookup_engine_header_evidence(self.root, [{"receiverType": owner, "symbol": name}],
            local_source_identity=self.identity, include_definitions=False)
        rows = result.get("results") or []
        coverage = result.get("coverage") or {}
        inheritance = coverage.get("inheritance") or {}
        complete = inheritance.get("verification") == "parents_searched" and not coverage.get("reasons")
        self.cache[key] = (rows, complete)
        return rows, complete


def _finding(path, root, text, offset, code, message, rows=()):
    return Finding("warning", str(path.relative_to(root)).replace("\\", "/"), line_number(text, offset), code, message,
        {"analysisKind": "bounded_lexical_compile_check", "confidence": "medium",
         "limitations": ["Not a compiler or macro expansion; unresolved declarations are not absence proof."],
         "evidenceRefs": [{"path": row.get("source"), "line": row.get("line")} for row in rows[:3]]})


def _signature(text, name):
    # Deliberately limited to simple signatures. No templates/default-expression
    # equivalence or covariant return reasoning is attempted.
    pattern = r"(?m)^[ \t]*(?P<ret>(?:(?:virtual|static|inline|FORCEINLINE|\w+_API)\s+)*[\w:*&]+)\s+" + re.escape(name) + r"\s*\((?P<args>[^(){};]*)\)\s*(?P<const>const\b)?"
    signatures = []
    for match in re.finditer(pattern, engine._lexical_source(text)):
        args = []
        for part in match["args"].split(","):
            part = part.split("=", 1)[0].strip()
            if not part or part == "void":
                continue
            if any(char in part for char in "<>()[]"):
                break
            part = re.sub(r"\b(?:struct|class|enum)\s+", "", part)
            trailing = re.search(r"[\s*&]([A-Za-z_]\w*)$", part)
            type_words = {"const", "volatile", "signed", "unsigned", "short", "long", "int", "char", "float", "double", "bool", "wchar_t"}
            unnamed_cv_type = bool(re.fullmatch(r"(?:const|volatile)\s+\w+", part))
            if trailing and trailing[1] not in type_words and not unnamed_cv_type:
                part = part[:trailing.start(1)].strip()
            part = re.sub(r"\b(\w+)\s+const\s*([&*])", r"const \1\2", part)
            if "&" not in part:
                # Value-parameter cv qualifiers are not part of the signature;
                # pointee const remains significant for pointer parameters.
                part = re.sub(r"\b(?:const|volatile)\b", "", part) if "*" not in part else re.sub(r"(?<=\*)\s*(?:const|volatile)\b", "", part)
            args.append(re.sub(r"\s+", "", part))
        else:
            ret = re.sub(r"\b(?:virtual|static|inline|FORCEINLINE|\w+_API)\s+", "", match["ret"]).strip()
            signatures.append((ret, tuple(args), bool(match["const"])))
    return signatures


def _classes(text):
    masked = engine._lexical_source(text)
    for match in re.finditer(r"\b(?:class|struct)\s+(?:\w+_API\s+)?([AUF]\w*)\b[^;{}]*\{", masked):
        name = match[1]
        region = engine._owner_region(masked, name)
        if region:
            yield name, region, engine._type_parents(text, name) or []


def validate_compile_contracts(root: Path, context, identity: dict) -> list[Finding]:
    evidence = CompileEvidence(identity)
    findings = []
    headers = {p: context.text_for(p) for p in context.paths if p.suffix.lower() in CPP_HEADER_SUFFIXES}
    owners = {}
    duplicates = {}
    for path, text in headers.items():
        for name, region, parents in _classes(text):
            owners.setdefault(name, []).append((path, text, region, parents))
        masked = engine._lexical_source(text)
        for match in re.finditer(r"\bU(?:CLASS|STRUCT|ENUM|INTERFACE)\s*\([^;{}]*?\)\s*(?:class|struct|enum(?:\s+class)?)\s+(?:\w+_API\s+)?(\w+)\b[^;{}]*\{", masked):
            duplicates.setdefault(match[1], []).append((path, text, match.start()))
    for name, declarations in duplicates.items():
        if len(declarations) > 1:
            for path, text, offset in declarations:
                findings.append(_finding(path, root, text, offset, "REFLECTED_TYPE_NAME_DUPLICATE",
                    f"Reflected type {name} has multiple project definitions; check UHT naming and conditional compilation."))

    def inherited(parents, member, visited=()):
        rows, complete = [], True
        for parent in parents:
            if parent in visited or len(visited) >= 8:
                complete = False
                continue
            local = owners.get(parent, [])
            if len(local) == 1:
                p, text, _, bases = local[0]
                declaration = engine._bounded_declaration(text, member, parent)
                if declaration:
                    rows.append({**declaration, "source": str(p), "specifiers": engine._declaration_specifiers(declaration["excerpt"], member)})
                else:
                    found, done = inherited(bases, member, (*visited, parent))
                    rows.extend(found); complete &= done
            elif local:
                complete = False
            else:
                found, done = evidence.member(parent, member)
                rows.extend(found); complete &= done
        return rows, complete

    for path in context.paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES | CPP_SOURCE_SUFFIXES:
            continue
        text = context.text_for(path)
        masked = engine._lexical_source(text)
        includes = [(n, value) for n, value in include_lines(text)
                    if re.match(r"\s*#\s*include\b", masked.splitlines()[n - 1])]
        if path.suffix.lower() in CPP_SOURCE_SUFFIXES and includes:
            paired = [p for p in headers if p.stem == path.stem]
            if len(paired) == 1 and Path(includes[0][1]).name != paired[0].name:
                findings.append(_finding(path, root, text, 0, "CPP_FIRST_INCLUDE_MISMATCH",
                    f"The corresponding {paired[0].name} is not the first include; check this module's IWYU setting."))
        if evidence.root:
            index = evidence.headers()
            for n, include in includes[:64]:
                if include.endswith(".generated.h") or any(p.name == Path(include).name for p in headers):
                    continue
                candidates = index.get(Path(include).stem.lower(), [])
                # Public include roots, not arbitrary filename suffixes such as Engine/Foo.h.
                valid = []
                for candidate in candidates:
                    parts = candidate.relative_to(evidence.root).parts
                    for marker in ("Public", "Classes"):
                        if marker in parts:
                            valid.append("/".join(parts[parts.index(marker) + 1:]))
                if include in valid:
                    continue
                if not candidates and not include.startswith(("Engine/", "GameFramework/", "EnhancedInput", "KeysTypes")):
                    continue  # May be a third-party/custom include directory.
                code = "ENGINE_INCLUDE_PATH_MISMATCH" if valid else "ENGINE_INCLUDE_UNRESOLVED"
                offset = sum(len(line) for line in text.splitlines(keepends=True)[:n - 1])
                findings.append(_finding(path, root, text, offset, code,
                    f'Include "{include}" was not resolved at a standard installed-engine public include root.'
                    + (f" Observed candidates: {', '.join(sorted(set(valid))[:3])}." if valid else " Check the declaration/header with engine_local or UBT; absence is not proven.")))

        if path in headers:
            for owner, region, parents in _classes(text):
                body = masked[region[1] + 1:region[2] - 1]
                base_offset = region[1] + 1
                for match in re.finditer(r"\bUPROPERTY\s*\(([^;{}]*?)\)\s*([^;{}]+);", body):
                    spec, declaration = match[1], match[2]
                    prop = re.search(r"\b(\w+)\s*(?:=[^;]*)?$", declaration.strip())
                    if prop:
                        rows, _ = inherited(parents, prop[1])
                        reflected = [r for r in rows if (r.get("specifiers", {}).get("reflection") or {}).get("macro") == "UPROPERTY"]
                        if reflected:
                            findings.append(_finding(path, root, text, base_offset + match.start(), "UPROPERTY_SHADOWS_PARENT",
                                f"{owner} redeclares inherited reflected property {prop[1]}.", reflected))
                    notify = re.search(r"\bReplicatedUsing\s*=\s*(\w+)", spec)
                    if notify and not engine._bounded_declaration(text, notify[1], owner):
                        rows, complete = inherited(parents, notify[1])
                        if not rows and complete:
                            findings.append(_finding(path, root, text, base_offset + match.start(), "REPNOTIFY_DECLARATION_NOT_FOUND",
                                f"ReplicatedUsing={notify[1]} has no declaration in the inspected owner/parent headers. Confirm with UHT."))
                for match in re.finditer(r"\bUFUNCTION\s*\([^;{}]*?\)\s*(?:virtual\s+)?[\w:*&]+\s+(\w+)\s*\(", body):
                    rows, _ = inherited(parents, match[1])
                    reflected = [r for r in rows if (r.get("specifiers", {}).get("reflection") or {}).get("macro") == "UFUNCTION"]
                    if reflected:
                        findings.append(_finding(path, root, text, base_offset + match.start(), "INHERITED_UFUNCTION_REDECLARED",
                            f"{owner} repeats UFUNCTION on inherited reflected method {match[1]}; retain the C++ override without repeating reflection.", reflected))
                for match in re.finditer(r"(?m)^[ \t]*(?:virtual\s+)?[\w:*&]+\s+(\w+)\s*\([^;{}]*\)\s*(?:const\s*)?override\b", body):
                    name = match[1]; wanted = _signature(match[0], name)
                    rows, _ = inherited(parents, name)
                    source_name = name
                    if not rows and name.endswith("_Implementation"):
                        source_name = name.removesuffix("_Implementation")
                        candidates, _ = inherited(parents, source_name)
                        rows = [row for row in candidates if "BlueprintNativeEvent" in
                                (row.get("specifiers", {}).get("reflection") or {}).get("arguments", "")]
                    if any(row.get("sameOwnerDeclarationCount") != 1 for row in rows):
                        continue  # The bounded excerpt does not contain a complete overload set.
                    observed = [sig for row in rows for sig in _signature(row.get("excerpt", ""), source_name)]
                    if len(wanted) == 1 and observed and wanted[0] not in observed:
                        # Pointer/reference return covariance requires compiler type information.
                        mismatch = all(wanted[0][1:] != sig[1:] or
                            (wanted[0][0] != sig[0] and not any(c in wanted[0][0] + sig[0] for c in "*&")) for sig in observed)
                        if mismatch:
                            findings.append(_finding(path, root, text, base_offset + match.start(), "ENGINE_OVERRIDE_SIGNATURE_MISMATCH",
                                f"{owner}::{name} does not match the inspected parent signature (parameters, const or return type).", rows))

        # Explicit Type::Member uses only. Unresolved receiver inference is not
        # turned into fabricated API facts; complex expressions remain for UBT.
        for match in list(re.finditer(r"\b([AUFE]\w+)::(\w+)\b", masked))[:24]:
            owner, member = match.groups()
            if owner in owners or member == owner or member.startswith("Static"):
                continue
            rows, complete = evidence.member(owner, member)
            if not rows and complete:
                findings.append(_finding(path, root, text, match.start(), "ENGINE_MEMBER_NOT_OBSERVED",
                    f"{owner}::{member} was not declared in the inspected owner/parent headers; check generated APIs/macros with UBT."))
        for _, function_start, body in iter_function_blocks(masked):
            opening = masked.find("{", function_start)
            function = masked[function_start:opening + 1 + len(body)]
            for match in list(re.finditer(r"\b([AU]\w+)\s*\*\s*(\w+)\b", function))[:24]:
                owner, variable = match.groups()
                # Only unambiguous local/parameter bindings. Never carry a
                # receiver type into another function or beyond a closed scope.
                declarations = re.findall(r"\b[\w:<>]+\s*[*&]?\s+" + re.escape(variable) + r"\b", function)
                if owner in owners or len(declarations) != 1:
                    continue
                tail = function[match.end():].split("}", 1)[0]
                for call in list(re.finditer(r"\b" + re.escape(variable) + r"\s*->\s*(\w+)\s*\(", tail))[:4]:
                    rows, complete = evidence.member(owner, call[1])
                    if not rows and complete:
                        findings.append(_finding(path, root, text, function_start + match.end() + call.start(), "ENGINE_MEMBER_NOT_OBSERVED",
                            f"{owner}::{call[1]} was not declared in the inspected owner/parent headers; confirm the receiver type and UBT diagnostics."))
    return findings
