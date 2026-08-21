from __future__ import annotations

from pathlib import Path

from harness.loader.generator import generate_rows
from harness.loader.ingest import HOST_DATA_DIR, build_ingest_sql, build_inline_ingest_sql
from harness.loader.spec import load_spec

SPECS = Path(__file__).resolve().parents[1] / "specs"


def test_local_ingest_sql_uses_extern_and_partitioned_by_all():
    spec = load_spec(SPECS / "example_events.json")
    generated = HOST_DATA_DIR / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    seed = generated / "example_events.json"
    seed.write_text("{}\n", encoding="utf-8")
    sql = build_ingest_sql(spec, seed, replace=False)
    assert "INSERT INTO \"example_events\"" in sql
    assert "EXTERN(" in sql
    assert '"type":"local"' in sql
    assert "PARTITIONED BY ALL TIME" in sql
    assert "MILLIS_TO_TIMESTAMP" in sql


def test_inline_ingest_sql_embeds_generated_rows():
    spec = load_spec(SPECS / "example_events.json")
    rows = generate_rows(spec)
    sql = build_inline_ingest_sql(spec, rows, replace=True)
    assert "REPLACE INTO \"example_events\" OVERWRITE ALL" in sql
    assert '"type":"inline"' in sql
    assert str(rows[0]["__time"]) in sql
