"""Deterministic seed-row generation."""

from __future__ import annotations

import random
from typing import Any

from harness.loader.spec import ColumnSpec, DatasourceSpec

# 2024-01-01T00:00:00Z
BASE_TIME_MILLIS = 1_704_067_200_000
MINUTE_MILLIS = 60_000


def generate_rows(spec: DatasourceSpec) -> list[dict[str, Any]]:
    if spec.seed.mode != "generate":
        raise ValueError("generate_rows requires seed.mode=generate")
    rng = random.Random(spec.seed.random_seed)
    rows: list[dict[str, Any]] = []
    for index in range(spec.seed.row_count or 0):
        row: dict[str, Any] = {}
        for column in spec.columns:
            row[column.name] = _value_for(column, index, rng)
        rows.append(row)
    return rows


def _value_for(column: ColumnSpec, index: int, rng: random.Random) -> Any:
    if column.is_time:
        return BASE_TIME_MILLIS + index * MINUTE_MILLIS
    if column.type == "long":
        return rng.randint(0, 1000)
    if column.type == "float":
        return round(rng.random() * 100.0, 4)
    if column.type == "double":
        return rng.random() * 100.0
    if column.type == "string":
        return f"{column.name}_{rng.randint(0, 5)}"
    raise ValueError(f"Unsupported column type {column.type!r}")
