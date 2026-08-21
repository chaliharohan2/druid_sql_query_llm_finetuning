from __future__ import annotations

from pathlib import Path

import pytest

from harness.client import DruidClient
from harness.loader.ingest import drop_datasource, load_datasource

HARNESS_ROOT = Path(__file__).resolve().parents[1]
SPECS = HARNESS_ROOT / "specs"


@pytest.fixture(scope="session")
def client() -> DruidClient:
    druid = DruidClient()
    if not druid.health():
        pytest.fail(
            "Druid cluster is not healthy at http://127.0.0.1:8888. "
            "Use `make test` (starts the cluster) or `make up` first."
        )
    return druid


@pytest.fixture
def loaded_metrics(client: DruidClient) -> str:
    spec = SPECS / "example_metrics.json"
    drop_datasource(client, "example_metrics")
    load_datasource(client, spec, replace=False)
    return "example_metrics"
