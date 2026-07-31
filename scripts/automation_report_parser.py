#!/usr/bin/env python
"""Fail-closed parsing for Unreal Automation report exports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REPORT_FILENAMES = {
    "index.json",
    "report.json",
    "results.json",
    "automationreport.json",
}
_TEST_CONTAINER_KEYS = {"tests", "testresults", "automationtests"}
_NAME_KEYS = (
    "fulltestpath",
    "testfullpath",
    "testpath",
    "testname",
    "testdisplayname",
    "displayname",
    "name",
)
_STATE_KEYS = ("state", "status", "result", "outcome")
_PASS_STATES = {
    "pass",
    "passed",
    "success",
    "succeeded",
    "successwithwarnings",
    "succeededwithwarnings",
}
_FAIL_STATES = {"fail", "failed", "failure", "error", "errors", "interrupted"}
_NOT_RUN_STATES = {
    "notrun",
    "notstarted",
    "skipped",
    "skip",
    "pending",
    "inprocess",
    "running",
    "unknown",
}


def _normal_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normal_state(value: object) -> str:
    if isinstance(value, bool):
        return "passed" if value else "failed"
    normalized = _normal_key(value)
    if normalized in _PASS_STATES:
        return "passed"
    if normalized in _FAIL_STATES:
        return "failed"
    if normalized in _NOT_RUN_STATES:
        return "notRun"
    return "unknown"


def _casefolded_items(record: dict[str, Any]) -> dict[str, Any]:
    return {_normal_key(key): value for key, value in record.items()}


def _direct_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    items = _casefolded_items(record)
    for key in keys:
        if key in items:
            return items[key]
    return None


def _state_from_record(record: dict[str, Any]) -> str:
    raw_state = _direct_value(record, _STATE_KEYS)
    if raw_state is not None:
        return _normal_state(raw_state)
    success = _casefolded_items(record).get("success")
    if isinstance(success, bool):
        return "passed" if success else "failed"
    child_states: list[str] = []
    for key, value in record.items():
        if _normal_key(key) not in {"deviceinstances", "devices", "platformresults"}:
            continue
        values = value if isinstance(value, list) else [value]
        for child in values:
            if isinstance(child, dict):
                child_states.append(_state_from_record(child))
    if not child_states:
        return "unknown"
    if any(state == "failed" for state in child_states):
        return "failed"
    if any(state in {"notRun", "unknown"} for state in child_states):
        return "notRun"
    return "passed"


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _record_from_value(value: Any, *, fallback_name: str = "") -> dict[str, Any] | None:
    if isinstance(value, str) and fallback_name:
        return {
            "name": fallback_name,
            "state": _normal_state(value),
            "errors": 0,
            "errorsMalformed": False,
        }
    if not isinstance(value, dict):
        return None
    name = _direct_value(value, _NAME_KEYS)
    if name is not None and not isinstance(name, (str, int, float)):
        return None
    resolved_name = str(name or fallback_name).strip()
    if not resolved_name:
        return None
    state = _state_from_record(value)
    if state == "unknown" and not any(
        key in _casefolded_items(value) for key in (*_STATE_KEYS, "success")
    ):
        return None
    items = _casefolded_items(value)
    raw_errors = items.get("errors")
    errors = _nonnegative_int(raw_errors)
    return {
        "name": resolved_name,
        "state": state,
        "errors": errors or 0,
        "errorsMalformed": raw_errors is not None and errors is None,
    }


def _records_from_container(container: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(container, list):
        for item in container:
            record = _record_from_value(item)
            if record is not None:
                records.append(record)
    elif isinstance(container, dict):
        direct = _record_from_value(container)
        if direct is not None:
            records.append(direct)
        else:
            for name, value in container.items():
                record = _record_from_value(value, fallback_name=str(name))
                if record is not None:
                    records.append(record)
    return records


def _collect_test_records(payload: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _normal_key(key) in _TEST_CONTAINER_KEYS:
                    records.extend(_records_from_container(child))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for record in records:
        identity = (
            record["name"].casefold(),
            record["state"],
            int(record.get("errors") or 0),
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(record)
    return unique


def _collect_report_messages(payload: Any) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            items = _casefolded_items(value)
            message = items.get("message")
            severity = items.get("type", items.get("severity", items.get("level", "")))
            event = items.get("event")
            if isinstance(event, dict):
                event_items = _casefolded_items(event)
                message = event_items.get("message", message)
                severity = event_items.get(
                    "type",
                    event_items.get("severity", event_items.get("level", severity)),
                )
            if isinstance(message, str) and message.strip():
                messages.append(
                    {"severity": str(severity or ""), "message": message.strip()}
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        identity = (message["severity"].casefold(), message["message"])
        if identity not in seen:
            seen.add(identity)
            unique.append(message)
    return unique


def _summary_count(payload: Any, key: str) -> tuple[bool, int | None]:
    containers = [payload]
    if isinstance(payload, dict):
        for candidate_key, value in payload.items():
            if _normal_key(candidate_key) in {"summary", "testsummary"}:
                containers.append(value)
    for container in containers:
        if not isinstance(container, dict):
            continue
        items = _casefolded_items(container)
        if key not in items:
            continue
        value = items[key]
        if isinstance(value, bool):
            return True, None
        if isinstance(value, int) and value >= 0:
            return True, value
        if isinstance(value, str) and value.strip().isdigit():
            return True, int(value.strip())
        return True, None
    return False, None


def _locate_report_file(report_path: Path) -> tuple[Path | None, list[str]]:
    if report_path.is_file():
        return report_path, []
    if not report_path.is_dir():
        return None, [f"automation report path does not exist: {report_path}"]
    json_files = sorted(
        (item for item in report_path.rglob("*.json") if item.is_file()),
        key=lambda item: (len(item.relative_to(report_path).parts), str(item).casefold()),
    )
    named = [item for item in json_files if item.name.casefold() in _REPORT_FILENAMES]
    if named:
        shallowest_depth = len(named[0].relative_to(report_path).parts)
        shallowest = [
            item
            for item in named
            if len(item.relative_to(report_path).parts) == shallowest_depth
        ]
        if len(shallowest) != 1:
            return None, [
                "automation report is ambiguous; multiple primary JSON reports were found: "
                + ", ".join(str(item) for item in shallowest)
            ]
        return shallowest[0], []
    candidate_files: list[Path] = []
    malformed: list[Path] = []
    for item in json_files:
        try:
            payload = json.loads(item.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            malformed.append(item)
            continue
        if _collect_test_records(payload):
            candidate_files.append(item)
    if len(candidate_files) == 1:
        return candidate_files[0], []
    if len(candidate_files) > 1:
        return None, [
            "automation report is ambiguous; multiple JSON files contain test results: "
            + ", ".join(str(item) for item in candidate_files)
        ]
    if malformed:
        return None, [
            "automation report JSON is malformed or unreadable: "
            + ", ".join(str(item) for item in malformed)
        ]
    return None, [f"no Automation report JSON was found under: {report_path}"]


def _selector_matches(selector: str, test_name: str) -> bool:
    wanted = selector.strip().casefold()
    actual = test_name.strip().casefold()
    if wanted.startswith("group:"):
        return False
    if wanted.startswith("startswith:"):
        prefix = wanted[len("startswith:") :].strip()
        if prefix and not prefix.endswith("."):
            prefix += "."
        return bool(prefix) and actual.startswith(prefix)
    match_from_start = wanted.startswith("^")
    match_from_end = wanted.endswith("$")
    if match_from_start:
        wanted = wanted[1:]
    if match_from_end:
        wanted = wanted[:-1]
    if match_from_start and match_from_end:
        return actual == wanted
    if match_from_start:
        return actual.startswith(wanted)
    if match_from_end:
        return actual.endswith(wanted)
    return bool(wanted) and wanted in actual


def parse_automation_report(
    report_path: str | Path,
    *,
    requested_filter: str,
) -> dict[str, Any]:
    """Parse a UE Automation export and require executed, passing requested tests."""

    location = Path(report_path)
    report_file, locate_issues = _locate_report_file(location)
    if report_file is None:
        return {
            "ok": False,
            "issues": locate_issues,
            "reportPath": str(location),
            "tests": [],
            "messages": [],
            "testCount": 0,
            "matchedTestCount": 0,
            "passedCount": 0,
            "failedCount": 0,
            "notRunCount": 0,
        }
    try:
        payload = json.loads(report_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "issues": [f"automation report JSON is malformed or unreadable: {exc}"],
            "reportPath": str(report_file),
            "tests": [],
            "messages": [],
            "testCount": 0,
            "matchedTestCount": 0,
            "passedCount": 0,
            "failedCount": 0,
            "notRunCount": 0,
        }

    tests = _collect_test_records(payload)
    messages = _collect_report_messages(payload)
    selectors = [item.strip() for item in requested_filter.split("+") if item.strip()]
    matched = [
        test
        for test in tests
        if any(_selector_matches(selector, test["name"]) for selector in selectors)
    ]
    passed_count = sum(test["state"] == "passed" for test in tests)
    failed_count = sum(test["state"] == "failed" for test in tests)
    not_run_count = sum(test["state"] in {"notRun", "unknown"} for test in tests)
    test_error_count = sum(int(test.get("errors") or 0) for test in tests)
    malformed_error_counts = sum(bool(test.get("errorsMalformed")) for test in tests)
    report_error_count = sum(
        _normal_key(message["severity"]) in {"error", "fatal", "critical"}
        for message in messages
    )
    issues: list[str] = []
    if not tests:
        issues.append("automation report contains no recognizable test results")
    for selector in selectors:
        if selector.casefold().startswith("group:"):
            issues.append(
                "requested Automation group membership cannot be verified from the "
                f"report alone: {selector}"
            )
            continue
        selector_matches = [
            test for test in tests if _selector_matches(selector, test["name"])
        ]
        if not selector_matches:
            issues.append(f"requested Automation test did not execute: {selector}")
        elif not all(test["state"] == "passed" for test in selector_matches):
            issues.append(f"requested Automation test did not pass: {selector}")
    if failed_count:
        issues.append(f"automation report contains {failed_count} failed test result(s)")
    if not_run_count:
        issues.append(
            f"automation report contains {not_run_count} non-terminal or unexecuted test result(s)"
        )
    if test_error_count:
        issues.append(
            f"automation report test records declare {test_error_count} error(s)"
        )
    if malformed_error_counts:
        issues.append(
            f"automation report contains {malformed_error_counts} malformed test error count(s)"
        )
    summary_fields = {
        key: _summary_count(payload, key)
        for key in (
            "succeeded",
            "succeededwithwarnings",
            "failed",
            "notrun",
            "inprocess",
        )
    }
    for key, (found, value) in summary_fields.items():
        if not found:
            issues.append(f"automation report summary is missing required field: {key}")
        elif value is None:
            issues.append(
                f"automation report summary field is malformed: {key}"
            )
    summary_failed = summary_fields["failed"][1]
    summary_not_run = summary_fields["notrun"][1]
    summary_in_process = summary_fields["inprocess"][1]
    if summary_failed:
        issues.append(f"automation report summary declares {summary_failed} failed test(s)")
    if summary_not_run:
        issues.append(
            f"automation report summary declares {summary_not_run} test(s) not run"
        )
    if summary_in_process:
        issues.append(
            f"automation report summary declares {summary_in_process} test(s) still in process"
        )
    summary_succeeded = summary_fields["succeeded"][1]
    summary_succeeded_with_warnings = summary_fields["succeededwithwarnings"][1]
    summary_values = (
        summary_succeeded,
        summary_succeeded_with_warnings,
        summary_failed,
        summary_not_run,
        summary_in_process,
    )
    if all(value is not None for value in summary_values):
        summary_total = sum(int(value or 0) for value in summary_values)
        if summary_total != len(tests):
            issues.append(
                "automation report summary count does not match its test records: "
                f"{summary_total} != {len(tests)}"
            )
        if int(summary_succeeded or 0) + int(summary_succeeded_with_warnings or 0) != passed_count:
            issues.append(
                "automation report succeeded counts do not match passing test records"
            )
        if int(summary_failed or 0) != failed_count:
            issues.append("automation report failed count does not match failed test records")
        if int(summary_not_run or 0) + int(summary_in_process or 0) != not_run_count:
            issues.append(
                "automation report not-run counts do not match non-terminal test records"
            )
    if report_error_count:
        issues.append(
            f"automation report contains {report_error_count} error/fatal event(s)"
        )
    return {
        "ok": not issues,
        "issues": issues,
        "reportPath": str(report_file),
        "requestedSelectors": selectors,
        "tests": tests,
        "messages": messages,
        "testCount": len(tests),
        "matchedTestCount": len(matched),
        "passedCount": passed_count,
        "failedCount": failed_count,
        "notRunCount": not_run_count,
        "testErrorCount": test_error_count,
        "errorEventCount": report_error_count,
    }
