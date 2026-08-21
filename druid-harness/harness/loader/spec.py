"""JSON datasource spec parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_TYPES = {"long", "float", "double", "string", "array<string>"}
ALLOWED_SEED_MODES = {"generate", "file"}
ALLOWED_FILE_FORMATS = {"csv", "json"}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    is_time: bool = False


@dataclass(frozen=True)
class SeedSpec:
    mode: str
    row_count: int | None = None
    random_seed: int = 42
    path: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class DatasourceSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    seed: SeedSpec
    spec_path: Path | None = None

    @property
    def time_column(self) -> ColumnSpec:
        matches = [c for c in self.columns if c.is_time]
        if len(matches) != 1:
            raise ValueError(f"Datasource {self.name!r} must have exactly one is_time column")
        return matches[0]


def load_spec(path: str | Path) -> DatasourceSpec:
    spec_path = Path(path).resolve()
    with spec_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Spec {spec_path} must be a JSON object")
    return parse_spec(raw, spec_path=spec_path)


def parse_spec(raw: dict[str, Any], spec_path: Path | None = None) -> DatasourceSpec:
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Spec field 'name' is required")
    columns_raw = raw.get("columns")
    if not isinstance(columns_raw, list) or not columns_raw:
        raise ValueError("Spec field 'columns' must be a non-empty list")
    columns = tuple(_parse_column(item, index) for index, item in enumerate(columns_raw))
    time_cols = [c for c in columns if c.is_time]
    if len(time_cols) != 1:
        raise ValueError("Spec must have exactly one column with is_time=true")
    seed_raw = raw.get("seed")
    if not isinstance(seed_raw, dict):
        raise ValueError("Spec field 'seed' is required")
    seed = _parse_seed(seed_raw, spec_path=spec_path)
    return DatasourceSpec(name=name.strip(), columns=columns, seed=seed, spec_path=spec_path)


def _parse_column(raw: Any, index: int) -> ColumnSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"columns[{index}] must be an object")
    name = raw.get("name")
    col_type = raw.get("type")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"columns[{index}].name is required")
    if not isinstance(col_type, str) or col_type.lower() not in ALLOWED_TYPES:
        raise ValueError(
            f"columns[{index}].type must be one of {sorted(ALLOWED_TYPES)}, got {col_type!r}"
        )
    is_time = bool(raw.get("is_time", False))
    return ColumnSpec(name=name.strip(), type=col_type.lower(), is_time=is_time)


def _parse_seed(raw: dict[str, Any], spec_path: Path | None) -> SeedSpec:
    mode = raw.get("mode")
    if mode not in ALLOWED_SEED_MODES:
        raise ValueError(f"seed.mode must be one of {sorted(ALLOWED_SEED_MODES)}")
    if mode == "generate":
        row_count = raw.get("row_count")
        if not isinstance(row_count, int) or row_count < 1:
            raise ValueError("seed.row_count must be a positive integer when mode is generate")
        random_seed = raw.get("random_seed", 42)
        if not isinstance(random_seed, int):
            raise ValueError("seed.random_seed must be an integer")
        return SeedSpec(mode=mode, row_count=row_count, random_seed=random_seed)
    path = raw.get("path")
    fmt = raw.get("format")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("seed.path is required when mode is file")
    if fmt not in ALLOWED_FILE_FORMATS:
        raise ValueError(f"seed.format must be one of {sorted(ALLOWED_FILE_FORMATS)}")
    return SeedSpec(mode=mode, path=path.strip(), format=fmt)
