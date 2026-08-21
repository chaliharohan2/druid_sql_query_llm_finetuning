from __future__ import annotations

from pathlib import Path

import pytest

from harness.loader.generator import generate_rows
from harness.loader.spec import load_spec, parse_spec

SPECS = Path(__file__).resolve().parents[1] / "specs"


def test_load_example_events_spec():
    spec = load_spec(SPECS / "example_events.json")
    assert spec.name == "example_events"
    assert spec.time_column.name == "__time"
    assert spec.seed.mode == "generate"
    assert spec.seed.row_count == 20


def test_generator_is_deterministic():
    spec = load_spec(SPECS / "example_metrics.json")
    first = generate_rows(spec)
    second = generate_rows(spec)
    assert first == second
    assert len(first) == 20
    assert first[0]["__time"] == 1_704_067_200_000


def test_spec_requires_time_column():
    with pytest.raises(ValueError, match="exactly one"):
        parse_spec(
            {
                "name": "bad",
                "columns": [{"name": "x", "type": "string"}],
                "seed": {"mode": "generate", "row_count": 1, "random_seed": 1},
            }
        )


def test_spec_rejects_parquet_format():
    with pytest.raises(ValueError, match="seed.format"):
        parse_spec(
            {
                "name": "bad",
                "columns": [{"name": "__time", "type": "long", "is_time": True}],
                "seed": {"mode": "file", "path": "x.parquet", "format": "parquet"},
            }
        )
