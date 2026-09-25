"""Pad a schema toward a target column-width tier (plan P0-5).

v1's schemas top out around 24 columns; real enterprise prompts run 26-200.
Rather than inventing dozens of bespoke columns per domain, this bank supplies
generic enterprise "noise" columns -- audit trail, actor near-duplicate
families, workflow metadata -- of the kind that shows up in almost any
production table regardless of its domain. `widen()` samples from it without
repeats until a schema reaches its target width.

Near-duplicate column families (P0-5: "created_by_first_name /
created_by_full_name / created_by_email") are represented as named GROUPS so
a schema either gets a whole family or none of it, which is what makes them
recognisable as a family in the rendered prompt.

Not AI training or inference code: this only produces Druid fixtures.
"""
from __future__ import annotations

import random

from roles import ADDITIVE, BETTER, CATEGORY_NUMERIC, FLAG, NEUTRAL, NONADDITIVE, WORSE

# ------------------------------------------------------------- near-dup groups
# Each group: (group_name, [(col_name, ctype, desc, kind), ...])
# kind: "dim" (string, pooled), "hi_card" (string, high cardinality), "flag" (0/1)
NEAR_DUP_GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ("created_actor", [
        ("created_by_id", "string", "Identifier of the user or service that created the record.", "hi_card"),
        ("created_by_name", "string", "Display name of the creator.", "hi_card"),
        ("created_by_email", "string", "Email address of the creator.", "hi_card"),
    ]),
    ("updated_actor", [
        ("updated_by_id", "string", "Identifier of the last user or service to modify the record.", "hi_card"),
        ("updated_by_name", "string", "Display name of the last modifier.", "hi_card"),
        ("last_modified_source", "string", "System that last wrote the record.", "dim",),
    ]),
    ("owner_chain", [
        ("owner_id", "string", "Current owner of the record.", "hi_card"),
        ("previous_owner_id", "string", "Owner before the last reassignment, or blank.", "hi_card"),
        ("owning_team", "string", "Team the current owner belongs to.", "dim"),
    ]),
    ("workflow_status", [
        ("workflow_state", "string", "Current step in the processing workflow.", "dim"),
        ("workflow_substate", "string", "Finer-grained state within workflow_state.", "dim"),
        ("approval_status", "string", "Approval outcome, where applicable.", "dim"),
    ]),
    ("escalation", [
        ("escalation_level", "long", "How many times the record has been escalated.", "count"),
        ("sla_breached_flag", "long", "1 if the SLA on this record was breached, else 0.", "flag"),
        ("retry_count", "long", "Number of processing retries so far.", "count"),
    ]),
]

# Generic single columns: (name, ctype, desc, kind, [values-or-None])
GENERIC_DIMS: list[tuple[str, str, str, list[str]]] = [
    ("source_system", "string", "Upstream system that produced the record.",
     ["crm", "erp", "mobile_app", "batch_import", "partner_feed"]),
    ("data_quality_flag", "string", "Automated data-quality classification.",
     ["clean", "needs_review", "suspect", "backfilled"]),
    ("record_version", "string", "Schema version the record was written under.",
     ["v1", "v2", "v3"]),
    ("environment", "string", "Environment the writer ran in.",
     ["prod", "staging", "dev", "sandbox"]),
    ("locale_code", "string", "Locale of the originating client.",
     ["en-US", "en-GB", "de-DE", "fr-FR", "pt-BR", "ja-JP"]),
    ("timezone_name", "string", "IANA timezone of the originating client.",
     ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo", "Australia/Sydney"]),
    ("currency_code_v2", "string", "Currency code recorded by the newer billing pipeline.",
     ["USD", "EUR", "GBP", "JPY", "AUD"]),
    ("channel_detail", "string", "Finer-grained channel than the primary entity column.",
     ["organic", "paid", "referral", "direct", "affiliate"]),
    ("cost_center", "string", "Internal cost centre the record is booked against.",
     [f"cc-{i:03d}" for i in range(1, 25)]),
    ("business_unit", "string", "Business unit that owns this record.",
     ["retail", "wholesale", "digital", "operations", "platform"]),
    ("department_code", "string", "Department code, distinct from business_unit.",
     [f"dept-{i:02d}" for i in range(1, 15)]),
    ("integration_partner", "string", "Third-party integration that touched this record, or none.",
     ["none", "salesforce", "zendesk", "workday", "netsuite"]),
    ("risk_tier", "string", "Risk classification applied at write time.",
     ["low", "medium", "high", "unclassified"]),
]

GENERIC_HI_CARD: list[tuple[str, str]] = [
    ("correlation_id", "Cross-system identifier used to stitch related records together."),
    ("parent_record_id", "Identifier of the parent record, where applicable."),
    ("external_ref_id", "Identifier assigned by the upstream source system."),
    ("legacy_id", "Identifier carried over from a retired system. Not guaranteed unique."),
    ("batch_id", "Ingestion batch the record was loaded in."),
    ("checksum", "Row-level checksum used for change detection."),
    ("etag", "Opaque version tag used for optimistic concurrency."),
    ("request_trace_id", "Trace identifier of the request that produced the record."),
]

GENERIC_TEXT: list[tuple[str, str]] = [
    ("internal_notes", "Free-text notes left by an operator. Often blank."),
    ("tags_freeform", "Free-text label field, not a structured taxonomy."),
    ("error_message", "Error text captured if processing failed, else blank."),
]

GENERIC_FLAGS: list[tuple[str, str]] = [
    ("is_deleted", "1 if the record has been soft-deleted, else 0."),
    ("is_test_record", "1 if generated by a test harness rather than real activity, else 0."),
    ("is_backfilled", "1 if the row was written by a historical backfill job, else 0."),
    ("requires_review", "1 if a downstream process flagged this row for manual review, else 0."),
]

GENERIC_MEASURES: list[tuple[str, str, str, str]] = [
    ("processing_attempts", "long", "Number of times the record was processed.", ADDITIVE),
    ("queue_depth_at_write", "long", "Depth of the processing queue when this record was written.", NONADDITIVE),
    ("payload_size_bytes", "long", "Serialized payload size in bytes.", ADDITIVE),
    ("confidence_score_v2", "double", "Secondary confidence score from a newer model, 0 to 1.", NONADDITIVE),
    ("normalized_weight", "double", "Weighting factor applied during downstream aggregation.", NONADDITIVE),
]


WIDEN_NAMES: set[str] = (
    {c[0] for _, cols in NEAR_DUP_GROUPS for c in cols}
    | {c[0] for c in GENERIC_DIMS} | {c[0] for c in GENERIC_HI_CARD} | {c[0] for c in GENERIC_TEXT}
    | {c[0] for c in GENERIC_FLAGS} | {c[0] for c in GENERIC_MEASURES}
)


def is_filler(name: str) -> bool:
    """True for columns the widening bank adds (as opposed to a domain's own columns)."""
    return name in WIDEN_NAMES or name.startswith("custom_field_")


def _pool_for(name: str, values: list[str] | None, rng: random.Random) -> list[str]:
    if values:
        return list(values)
    return [f"{name}_{i}" for i in range(rng.randint(6, 30))]


def widen(existing_names: set[str], target_n: int, rng: random.Random) -> dict:
    """Draw filler columns (without name collisions) toward `target_n` extra columns.

    Returns a dict with `columns` (list of (name, ctype, desc)), `pools`,
    `dims` (names to add to roles["dims"]), `hi_card`, `metrics` (role dicts),
    `flags` (names with role flag_01), `category_numeric` (unused here, kept
    for symmetry), and `groups_used` (near-dup family names, for the overview
    prose to mention).
    """
    out = {"columns": [], "pools": {}, "dims": [], "hi_card": [], "metrics": [],
           "flags": [], "groups_used": []}
    remaining = target_n
    if remaining <= 0:
        return out

    groups = NEAR_DUP_GROUPS[:]
    rng.shuffle(groups)
    for gname, cols in groups:
        if remaining <= 0:
            break
        if any(c[0] in existing_names for c in cols):
            continue
        for name, ctype, desc, kind in cols:
            if remaining <= 0:
                break
            existing_names.add(name)
            out["columns"].append((name, ctype, desc))
            if kind == "flag":
                out["flags"].append(name)
                out["metrics"].append({"name": name, "type": ctype, "gen": ["pick", [0, 1]]})
            elif kind == "count":
                out["metrics"].append({"name": name, "type": ctype, "gen": ["uni", 0, 4]})
            elif kind == "hi_card":
                out["hi_card"].append(name)
                out["pools"][name] = [f"{name}-{i:04d}" for i in range(200)]
            else:
                out["dims"].append(name)
                out["pools"][name] = [f"{name}_{i}" for i in range(rng.randint(4, 8))]
            remaining -= 1
        out["groups_used"].append(gname)

    pools_left = [c for c in GENERIC_DIMS if c[0] not in existing_names]
    rng.shuffle(pools_left)
    for name, ctype, desc, values in pools_left:
        if remaining <= 0:
            break
        existing_names.add(name)
        out["columns"].append((name, ctype, desc))
        out["dims"].append(name)
        out["pools"][name] = list(values)
        remaining -= 1

    hi_left = [c for c in GENERIC_HI_CARD if c[0] not in existing_names]
    rng.shuffle(hi_left)
    for name, desc in hi_left:
        if remaining <= 0:
            break
        existing_names.add(name)
        out["columns"].append((name, "string", desc))
        out["hi_card"].append(name)
        out["pools"][name] = [f"{name.split('_')[0]}-{i:06x}" for i in range(300)]
        remaining -= 1

    text_left = [c for c in GENERIC_TEXT if c[0] not in existing_names]
    rng.shuffle(text_left)
    for name, desc in text_left:
        if remaining <= 0:
            break
        existing_names.add(name)
        out["columns"].append((name, "string", desc))
        out["dims"].append(name)
        # Genuinely blank values only happen for columns nullable_columns()
        # actually marks nullable (handled at row-generation time); a
        # non-nullable draw must never land on an empty string.
        out["pools"][name] = ["see ticket for detail", "n/a", "pending review", "resolved externally"]
        remaining -= 1

    flag_left = [c for c in GENERIC_FLAGS if c[0] not in existing_names]
    rng.shuffle(flag_left)
    for name, desc in flag_left:
        if remaining <= 0:
            break
        existing_names.add(name)
        out["columns"].append((name, "long", desc))
        out["flags"].append(name)
        out["metrics"].append({"name": name, "type": "long", "gen": ["pick", [0, 1]]})
        remaining -= 1

    meas_left = [c for c in GENERIC_MEASURES if c[0] not in existing_names]
    rng.shuffle(meas_left)
    for name, ctype, desc, role in meas_left:
        if remaining <= 0:
            break
        existing_names.add(name)
        out["columns"].append((name, ctype, desc))
        gen = ["uni", 0, 50] if ctype == "long" else ["uni", 0.0, 1.0]
        out["metrics"].append({"name": name, "type": ctype, "gen": gen})
        remaining -= 1

    # If the bank is exhausted before the target is reached (only happens for
    # the 81-200 tier), synthesize numbered generic dims -- still realistic:
    # real wide tables do carry sequentially-named custom fields.
    i = 1
    while remaining > 0:
        name = f"custom_field_{i}"
        if name not in existing_names:
            existing_names.add(name)
            out["columns"].append((name, "string", "Customer-defined custom field. No fixed schema."))
            out["dims"].append(name)
            out["pools"][name] = [f"cf_{i}_{j}" for j in range(rng.randint(3, 10))]
            remaining -= 1
        i += 1

    return out
