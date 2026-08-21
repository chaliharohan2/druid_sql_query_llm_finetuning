"""Stdout summary for a batch validation run."""

from __future__ import annotations

from collections import Counter
from typing import Any


def normalize_error(message: str | None, status: str) -> str:
    if status == "VALID":
        return "(valid)"
    if status == "TIMEOUT":
        return "(timeout)"
    if not message:
        return "(no error message)"
    first_line = message.strip().splitlines()[0].strip()
    return first_line[:200] if first_line else "(no error message)"


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(record.get("status") or "UNKNOWN") for record in records)
    patterns = Counter(
        normalize_error(record.get("error_message"), str(record.get("status") or ""))
        for record in records
        if record.get("status") != "VALID"
    )
    assertions = [record for record in records if "assertion_passed" in record]
    assertion_fail = sum(1 for record in assertions if not record.get("assertion_passed"))
    return {
        "total": len(records),
        "valid": counts.get("VALID", 0),
        "invalid": counts.get("INVALID", 0),
        "timeout": counts.get("TIMEOUT", 0),
        "other": sum(
            count for status, count in counts.items() if status not in {"VALID", "INVALID", "TIMEOUT"}
        ),
        "error_patterns": patterns.most_common(10),
        "assertions": len(assertions),
        "assertion_failures": assertion_fail,
    }


def print_summary(records: list[dict[str, Any]], file=None) -> dict[str, Any]:
    stats = summarize(records)
    lines = [
        "Batch summary",
        f"  total:    {stats['total']}",
        f"  valid:    {stats['valid']}",
        f"  invalid:  {stats['invalid']}",
        f"  timeout:  {stats['timeout']}",
    ]
    if stats["other"]:
        lines.append(f"  other:    {stats['other']}")
    if stats["assertions"]:
        lines.append(
            f"  assertions: {stats['assertions']} "
            f"({stats['assertion_failures']} failed)"
        )
    if stats["error_patterns"]:
        lines.append("  top error patterns:")
        for pattern, count in stats["error_patterns"]:
            lines.append(f"    {count:4d}  {pattern}")
    text = "\n".join(lines)
    print(text, file=file)
    return stats
