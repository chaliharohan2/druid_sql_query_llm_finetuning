"""Negative/trap-query assertions: expected INVALID for the right reason."""

from __future__ import annotations

import re
from typing import Any

from harness.validator.query import STATUS_INVALID, QueryResult


def apply_negative_assertion(record: dict[str, Any], result: QueryResult) -> dict[str, Any]:
    expected = record.get("expected_status")
    extra: dict[str, Any] = {}
    if expected is None:
        return extra
    if expected != STATUS_INVALID:
        extra["assertion_passed"] = False
        extra["assertion_detail"] = (
            f"Unsupported expected_status {expected!r}; only INVALID is supported"
        )
        return extra

    substring = record.get("expected_error_substring")
    pattern = record.get("expected_error_pattern")
    if not substring and not pattern:
        extra["assertion_passed"] = False
        extra["assertion_detail"] = (
            "expected_status=INVALID requires expected_error_substring and/or expected_error_pattern"
        )
        return extra

    if result.status != STATUS_INVALID:
        extra["assertion_passed"] = False
        extra["assertion_detail"] = f"expected INVALID but query was {result.status}"
        return extra

    message = result.error_message or ""
    failures: list[str] = []
    if substring is not None and str(substring) not in message:
        failures.append(f"substring {substring!r} not found in error message")
    if pattern is not None:
        try:
            matched = re.search(str(pattern), message) is not None
        except re.error as exc:
            extra["assertion_passed"] = False
            extra["assertion_detail"] = f"invalid expected_error_pattern: {exc}"
            return extra
        if not matched:
            failures.append(f"pattern {pattern!r} did not match error message")

    if failures:
        extra["assertion_passed"] = False
        extra["assertion_detail"] = "; ".join(failures)
    else:
        extra["assertion_passed"] = True
        extra["assertion_detail"] = "matched expected error"
    return extra
