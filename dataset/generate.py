#!/usr/bin/env python3
"""Assemble the example set from templates x schemas, then balance the mix.

Three balances are enforced, in this order:

  1. Schema split.  Whole families are held out for validation, so no validation
     schema shares a column vocabulary with anything the model trained on.
  2. Cluster mix.   Each quirk cluster gets a target share of the set; clusters
     that cannot fill their share (there are only so many schemas with a lookup)
     hand the remainder back.
  3. Prompt format. Formats are assigned most-constrained-example-first, each
     going to whichever eligible format is currently least used, which lands all
     twelve within a couple of examples of each other.

Output is examples.json; validate_bulk.py executes every query in it.

Not AI training or inference code: this produces training *data*.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import prompt_formats as pf  # noqa: E402
import templates as tp  # noqa: E402
from schema_view import SV  # noqa: E402

TRAIN_TARGET = 1000
VAL_TARGET = 150
SCHEMA_CAP_PCT = 0.03  # no single schema may exceed this share of the split

# Whole families held out of training. Chosen so the validation set can still
# exercise every enrichment shape the training set teaches: a JSON-string column
# (streaming_media), a lookup (cdn_edge), a multi-value dimension
# (support_tickets), a join to a dimension table (retail_pos + dim_stores), a
# plain numeric domain (crypto_trades) and one hand-written schema
# (game_sessions). Holding out whole families, not single variants, is what
# stops a validation schema sharing a column vocabulary with a training one.
# Only generated families are held out: all nine hand-written schemas carry
# hand-authored examples that belong in training.
HOLDOUT_FAMILIES = {"crypto_trades", "support_tickets", "streaming_media",
                    "cdn_edge", "retail_pos", "dim_stores"}

CLUSTER_WEIGHTS = {
    "time_bucketing": 10, "relative_time": 9, "grouping": 9, "order_by_restriction": 7,
    "approx_agg": 7, "time_extract_format": 6, "missing_function": 6, "reserved_alias": 6,
    "mvd": 5, "epoch_time_column": 5, "string_time_column": 5, "latest_earliest": 5,
    "filtered_agg": 5, "time_shift": 4, "json_string": 4, "string_ops": 4,
    "null_math": 4, "join": 4, "lookup": 3, "timestamp_literal": 3,
    "reserved_column": 3,
}


def seed_anchor(index: dict) -> datetime:
    """The instant the seed rows were built around, read back from the seeds."""
    meta = ROOT / "dataset_meta.json"
    if meta.exists():
        return datetime.fromisoformat(json.loads(meta.read_text())["anchor"])
    latest = 0
    for sid, d in index.items():
        path = ROOT / "seeds" / f"{d['datasource']}.json"
        with path.open() as fh:
            for line in fh:
                latest = max(latest, json.loads(line)[d["time_col"]])
    anchor = datetime.fromtimestamp(latest / 1000, tz=timezone.utc).replace(
        minute=0, second=0, microsecond=0)
    meta.write_text(json.dumps({"anchor": anchor.isoformat()}, indent=1) + "\n")
    return anchor


def eligible(t: dict, sv: SV) -> bool:
    if not sv.has(*t["needs"]):
        return False
    # A dimension table is a join partner, not a subject in its own right.
    return not sv.d.get("id", "").startswith("dim_")


def build_pool(index: dict, schema_ids: list[str], rng: random.Random) -> dict:
    """All (cluster -> [candidate]) pairs available for these schemas."""
    pool = defaultdict(list)
    for sid in schema_ids:
        sv = SV(index, sid, rng)
        for t in tp.T:
            if eligible(t, sv):
                pool[t["cluster"]].append((t, sid))
    return pool


def draw(index: dict, schema_ids: list[str], target: int, rng: random.Random,
         tag: str, forbidden: set[str] | None = None) -> list[dict]:
    pool = build_pool(index, schema_ids, rng)
    total_w = sum(CLUSTER_WEIGHTS[c] for c in pool)
    quota = {c: max(1, round(target * CLUSTER_WEIGHTS[c] / total_w)) for c in pool}
    cap = max(4, round(target * SCHEMA_CAP_PCT))

    out: list[dict] = []
    used_pairs: set[tuple[str, str]] = set()
    seen_questions: set[str] = set(forbidden or ())
    per_schema: Counter = Counter()
    shortfall = 0

    for cluster in sorted(pool, key=lambda c: len(pool[c])):
        want = quota[cluster] + shortfall
        cands = pool[cluster][:]
        rng.shuffle(cands)
        got = 0
        # Two passes: the first respects the per-schema cap, the second relaxes
        # it only for clusters that would otherwise come up short.
        for relax in (False, True):
            for t, sid in cands:
                if got >= want:
                    break
                if (t["id"], sid) in used_pairs:
                    continue
                if not relax and per_schema[sid] >= cap:
                    continue
                sv = SV(index, sid, rng)
                try:
                    q = t["fn"](sv)
                except Exception as exc:  # a template that cannot bind is skipped
                    print(f"  ! {t['id']} on {sid}: {exc}", file=sys.stderr)
                    continue
                if q["question"] in seen_questions:
                    continue
                used_pairs.add((t["id"], sid))
                seen_questions.add(q["question"])
                per_schema[sid] += 1
                got += 1
                out.append(_example(f"{tag}_{len(out):04d}", cluster, t, sid, sv, q))
            if got >= want:
                break
        shortfall = max(0, want - got)

    # Cluster quotas round down; top up to exactly `target` from whatever pairs
    # are still unused, preferring the clusters that are furthest below quota.
    spare = [(t, sid) for c in pool for t, sid in pool[c] if (t["id"], sid) not in used_pairs]
    rng.shuffle(spare)
    have = Counter(e["cluster"] for e in out)
    spare.sort(key=lambda ts: have[ts[0]["cluster"]] / max(1, quota[ts[0]["cluster"]]))
    for t, sid in spare:
        if len(out) >= target:
            break
        sv = SV(index, sid, rng)
        try:
            q = t["fn"](sv)
        except Exception:
            continue
        if q["question"] in seen_questions:
            continue
        used_pairs.add((t["id"], sid))
        seen_questions.add(q["question"])
        out.append(_example(f"{tag}_{len(out):04d}", t["cluster"], t, sid, sv, q))
    return out[:target]


def _example(eid: str, cluster: str, t: dict, sid: str, sv: SV, q: dict) -> dict:
    used = [sid]
    if "partner" in t["needs"]:
        used.append(sv.r["partner"]["schema"])
    return {"id": eid, "cluster": cluster, "template": t["id"],
            "query_schemas": used, "schemas": list(used),
            "question": q["question"], "sql": q["sql"],
            "gates": {"must_contain": list(q["must"]), "expect_rows": q["rows"]},
            "trap": q["trap"]}


def add_distractors(examples: list[dict], pool: list[str], rng: random.Random,
                    share: float = 0.25) -> None:
    """Put tables the query never touches into a quarter of the prompts.

    Real schema blocks are pasted in wholesale, not trimmed to the answer. A model
    trained only on prompts where every table is needed learns to use every table.
    """
    for e in examples:
        if rng.random() >= share:
            continue
        extra = [s for s in pool if s not in e["query_schemas"]]
        if not extra:
            continue
        picked = rng.sample(extra, min(rng.randint(1, 2), len(extra)))
        e["schemas"] = list(e["query_schemas"]) + picked
        rng.shuffle(e["schemas"])
        e["distractors"] = picked


def assign_formats(examples: list[dict], index: dict, rng: random.Random) -> None:
    """Spread examples across the twelve formats as evenly as eligibility allows."""
    counts: Counter = Counter({name: 0 for name in pf.FORMATS})
    annotated = []
    for e in examples:
        need = pf.requirements(index, e["query_schemas"], e["sql"])
        ok = [n for n in pf.FORMATS if need <= pf.FORMATS[n][2]]
        annotated.append((len(ok), rng.random(), e, need, ok))
    annotated.sort(key=lambda x: (x[0], x[1]))  # most constrained first
    for _, _, e, need, ok in annotated:
        choice = min(ok, key=lambda n: (counts[n], rng.random()))
        counts[choice] += 1
        e["format"] = choice
        e["format_needs"] = sorted(need)


def main() -> int:
    index = json.loads((ROOT / "schema_index.json").read_text())
    tp.ANCHOR = seed_anchor(index)
    rng = random.Random(20260826)

    held = [s for s, d in index.items() if d["family"] in HOLDOUT_FAMILIES]
    train_ids = [s for s in index if s not in held]
    # A held-out fact table keeps its held-out partner; nothing else crosses over.
    val_ids = [s for s in held if not s.startswith("dim_")]

    print(f"anchor {tp.ANCHOR.isoformat()}")
    print(f"{len(index)} schemas: {len(train_ids)} train, {len(held)} held out "
          f"({len(val_ids)} of them usable as query subjects)")

    # The 64 hand-authored examples reviewed in batch 01 are kept: they are the
    # highest-quality part of the set and every schema they use is in training.
    sys.path.insert(0, str(ROOT / "examples"))
    import batch01  # noqa: E402
    hand = [{"id": f"hw_{e['id']}", "cluster": e["cluster"], "template": "handwritten",
             "query_schemas": e["schemas"], "schemas": list(e["schemas"]),
             "question": e["question"], "sql": e["sql"],
             "gates": e["gates"], "trap": e["trap"]} for e in batch01.E]
    assert all(s in train_ids for e in hand for s in e["query_schemas"]), \
        "a hand-authored example references a held-out schema"

    train = hand + draw(index, train_ids, TRAIN_TARGET - len(hand), rng, "tr")
    # Validation questions must not repeat a training question verbatim, even
    # though the schema behind them differs.
    val = draw(index, val_ids, VAL_TARGET, rng, "va",
               forbidden={e["question"] for e in train})
    add_distractors(train, train_ids, rng)
    add_distractors(val, val_ids, rng)
    assign_formats(train, index, rng)
    assign_formats(val, index, rng)

    (ROOT / "examples.json").write_text(json.dumps(
        {"anchor": tp.ANCHOR.isoformat(),
         "holdout_families": sorted(HOLDOUT_FAMILIES),
         "train_schemas": sorted(train_ids), "val_schemas": sorted(val_ids),
         "train": train, "val": val}, indent=1) + "\n")

    for name, rows in (("train", train), ("val", val)):
        print(f"\n{name}: {len(rows)} examples, {sum(1 for r in rows if r['trap'])} traps")
        cl = Counter(r["cluster"] for r in rows)
        print("  clusters: " + ", ".join(f"{c}={n}" for c, n in cl.most_common()))
        fm = Counter(r["format"] for r in rows)
        print("  formats:  " + ", ".join(f"{c}={n}" for c, n in fm.most_common()))
        sc = Counter(r["schemas"][0] for r in rows)
        print(f"  schemas:  {len(sc)} used, max {sc.most_common(1)[0][1]} per schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
