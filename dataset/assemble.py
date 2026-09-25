#!/usr/bin/env python3
"""Turn raw generation pools into the final v2 split files (plan Sections 5-7, 9, P2-2).

  train_pool.jsonl       -> v2/train.jsonl
  val_pool.jsonl         -> v2/val_heldout_domain.jsonl
  test_shape_pool.jsonl  -> v2/test_heldout_shape.jsonl
  prodlike_pool.jsonl    -> v2/test_prodlike.jsonl

Steps, in order:
  1. near-duplicate questions within a target table are dropped (cosine > 0.88);
  2. skeleton cap: no skeleton exceeds 0.5% of the train split;
  3. pair cap: no pair of Druid feature families exceeds 3% of train;
  4. test_heldout_shape drops any example whose skeleton also occurs in train;
  5. ~10% of answerable train rows get an output-wrapper variant (P2-2, D2);
  6. hard checks: no datasource shared between splits, ids unique.

Nothing here calls a model. Not AI training or inference code.
"""
from __future__ import annotations

import argparse
import itertools
import sys as _sys
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "v2"

WRAPPERS = {
    "json_sql": ('Output format: respond with a JSON object of the form {"sql": "<query>"} and nothing else.',
                 lambda sql: json.dumps({"sql": sql})),
    "json_query": ('Output format: respond with a JSON object of the form {"query": "<query>"} and nothing else.',
                   lambda sql: json.dumps({"query": sql})),
    "json_dialect": ('Output format: respond as JSON, {"dialect": "druid", "sql": "<query>"}, and nothing else.',
                     lambda sql: json.dumps({"dialect": "druid", "sql": sql})),
    "fenced": ("Output format: return the query inside a ```sql fenced block and nothing else.",
               lambda sql: f"```sql\n{sql}\n```"),
    "tags": ("Output format: put the query between <sql> and </sql> tags and nothing else.",
             lambda sql: f"<sql>\n{sql}\n</sql>"),
}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def question_of(rec: dict) -> str:
    return rec["messages"][-2]["content"] if rec["meta"].get("question") is None else rec["meta"]["question"]


def _vec(q: str) -> Counter:
    toks = re.findall(r"[a-z0-9_]+", q.lower())
    return Counter(toks) + Counter(zip(toks, toks[1:]))


def _cos(a: Counter, b: Counter) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def dedupe(recs: list[dict], threshold: float = 0.88) -> tuple[list[dict], int]:
    kept, dropped = [], 0
    by_table: dict[str, list[Counter]] = {}
    for r in recs:
        q = r["meta"].get("question") or r["messages"][-2]["content"][-400:]
        v = _vec(q)
        bucket = by_table.setdefault(r["meta"]["target_tables"][0], [])
        if any(_cos(v, o) > threshold for o in bucket):
            dropped += 1
            continue
        bucket.append(v)
        kept.append(r)
    return kept, dropped


def skeleton_cap(recs: list[dict], share: float = 0.005) -> tuple[list[dict], int]:
    """Iterate because dropping rows shrinks the split, and so the cap."""
    cur = recs
    for _ in range(6):
        cap = max(2, math.ceil(share * len(cur)))
        seen: Counter = Counter()
        nxt = []
        for r in cur:
            if r["meta"].get("unanswerable"):
                nxt.append(r)
                continue
            h = r["meta"]["skeleton_hash"]
            if seen[h] < cap:
                seen[h] += 1
                nxt.append(r)
        if len(nxt) == len(cur):
            break
        cur = nxt
    return cur, len(recs) - len(cur)


def pair_cap(recs: list[dict], share: float = 0.03) -> tuple[list[dict], int]:
    cap = max(3, math.ceil(share * len(recs)))
    counts: Counter = Counter()
    kept = []
    for r in recs:
        pairs = list(itertools.combinations(sorted(r["meta"]["feature_families"]), 2))
        if any(counts[p] >= cap for p in pairs):
            continue
        counts.update(pairs)
        kept.append(r)
    return kept, len(recs) - len(kept)


def apply_wrappers(recs: list[dict], share: float, rng: random.Random) -> int:
    n = 0
    for r in recs:
        if r["meta"].get("unanswerable") or r["meta"].get("turns", 1) > 1 or rng.random() >= share:
            continue  # multi-turn rows keep plain SQL: their earlier assistant turn is plain SQL
        name = rng.choice(sorted(WRAPPERS))
        spec, fn = WRAPPERS[name]
        first = r["messages"][0]
        first["content"] = first["content"].rstrip() + "\n\n" + spec
        sql = r["messages"][-1]["content"]
        r["messages"][-1]["content"] = fn(sql)
        r["meta"]["wrapper"] = name
        r["meta"]["gold_sql"] = sql
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="train_pool")
    ap.add_argument("--val", default="val_pool")
    ap.add_argument("--test-shape", default="test_shape_pool")
    ap.add_argument("--prodlike", default="prodlike_pool")
    ap.add_argument("--followups", default="followup_pool", help="two-turn rows merged into train (P2-1)")
    ap.add_argument("--curated", default="curated", help="hand-written rows merged into train (kept first, never deduped away)")
    ap.add_argument("--followup-share", type=float, default=0.05,
                    help="cap on two-turn rows as a share of train (plan P2-1: about 5%%)")
    ap.add_argument("--audit-drop", action="store_true",
                    help="drop rows the second-opinion auditor flagged (<pool>_audit.jsonl from audit_pool.py)")
    ap.add_argument("--wrapper-share", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=20260924)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    OUT.mkdir(exist_ok=True)
    log: dict = {}

    pools = {"train": load(ROOT / f"{args.train}.jsonl"), "val_heldout_domain": load(ROOT / f"{args.val}.jsonl"),
             "test_heldout_shape": load(ROOT / f"{args.test_shape}.jsonl"),
             "test_prodlike": load(ROOT / f"{args.prodlike}.jsonl")}
    final: dict[str, list[dict]] = {}
    fus = load(ROOT / f"{args.followups}.jsonl")
    n_base = len(pools["train"])
    random.Random(args.seed).shuffle(fus)
    fus = fus[: int(args.followup_share * n_base)]
    pools["train"] += fus
    curated = load(ROOT / f"{args.curated}.jsonl")
    _sys.path.insert(0, str(ROOT))
    _sys.path.insert(0, str(ROOT.parent / "druid-harness"))
    import sampler
    index_all = json.loads((ROOT / "schema_index.json").read_text())
    verified_path = OUT / "prodlike_verified_ids.txt"
    verified = set(verified_path.read_text().split()) if verified_path.exists() else set()

    import validators as V

    def refresh_features(r) -> None:
        """Recompute feature families from the gold SQL with the current detector, so a refined detector
        applies to rows generated before the refinement."""
        m = r["meta"]
        if m.get("unanswerable") or m.get("source") == "handwritten":
            return
        sql = m.get("gold_sql") or r["messages"][-1]["content"]
        det = V.detect_features(sql, [index_all[t]["roles"] for t in m["prompt_tables"]])
        m["feature_families"] = sorted(det & sampler.DRUID_COUNTED)
        m["sql_features"] = sorted(det & set(sampler.FEATURE_FAMILIES_GENERAL))

    def too_long(r) -> bool:
        """Drop rows whose question breaks the word cap (rows generated before the cap existed)."""
        m = r["meta"]
        if m.get("unanswerable") or m.get("source") == "handwritten" or not m.get("question") or m.get("turns", 1) > 1:
            return False
        cap = sampler.WORD_CAPS.get(m.get("complexity"), 60)
        if m.get("question_style") == "terse":
            cap = min(cap, sampler.TERSE_WORD_CAP)
        return len(m["question"].split()) > cap

    audit_src = {"train": args.train, "val_heldout_domain": args.val, "test_heldout_shape": args.test_shape,
                 "test_prodlike": args.prodlike}
    n_audit_dropped: dict[str, int] = {}
    if args.audit_drop:
        for name, recs in pools.items():
            path = ROOT / f"{audit_src[name]}_audit.jsonl"
            if not path.exists():
                continue
            flagged = {json.loads(l)["attempt"] for l in path.read_text().splitlines()
                       if l.strip() and not json.loads(l)["ok"]}
            before = len(recs)
            recs[:] = [r for r in recs if r["meta"].get("turns", 1) > 1 or r["meta"]["attempt"] not in flagged]
            n_audit_dropped[name] = before - len(recs)

    for name, recs in pools.items():
        recs.sort(key=lambda r: (r["meta"]["attempt"], r["meta"].get("turns", 1)))
        n0 = len(recs)
        n_long = sum(1 for r in recs if too_long(r))
        recs[:] = [r for r in recs if not too_long(r)]
        for r in recs:
            refresh_features(r)
        # Unanswerable rows are cheap to keep, so the pool overshoots the ~4% target; trim the excess.
        un = [r for r in recs if r["meta"].get("unanswerable") and r["meta"].get("source") != "handwritten"]
        cap = int(0.045 * len(recs))
        if len(un) > cap:
            drop = {id(r) for r in random.Random(args.seed).sample(un, len(un) - cap)}
            recs[:] = [r for r in recs if id(r) not in drop]
        recs, d_dup = dedupe(recs)
        recs, d_skel = skeleton_cap(recs) if name != "test_prodlike" else (recs, 0)
        d_pair = 0
        if name == "train":
            recs, d_pair = pair_cap(recs)
        final[name] = recs
        log[name] = {"pool": n0, "dropped_by_audit": n_audit_dropped.get(name, 0), "dropped_over_word_cap": n_long, "dropped_duplicates": d_dup, "dropped_skeleton_cap": d_skel,
                     "dropped_pair_cap": d_pair}

    final["train"] = curated + final["train"]  # hand-written rows are exempt from the automatic caps
    log["curated_rows"] = len(curated)
    train_skel = {r["meta"]["skeleton_hash"] for r in final["train"] if not r["meta"].get("unanswerable")}
    shape = final["test_heldout_shape"]
    overlap = sum(1 for r in shape if r["meta"]["skeleton_hash"] in train_skel)
    log["test_heldout_shape"]["skeleton_overlap_with_train_before_drop"] = overlap
    final["test_heldout_shape"] = [r for r in shape if r["meta"]["skeleton_hash"] not in train_skel
                                   or r["meta"].get("unanswerable")]

    log["wrapped_train_rows"] = apply_wrappers(final["train"], args.wrapper_share, rng)

    # hard checks. Target domains must be disjoint across splits (a held-out split shares no
    # vocabulary with train). Distractor tables may come from anywhere -- the prod-like split draws
    # them from every family on purpose -- so they are reported, not forbidden.
    index = json.loads((ROOT / "schema_index.json").read_text())
    target_fams = {n: {index[r["meta"]["target_tables"][0]]["family"] for r in recs} for n, recs in final.items()}
    problems = []
    for a_, b_ in itertools.combinations(final, 2):
        shared = target_fams[a_] & target_fams[b_]
        if shared:
            problems.append(f"target families shared between {a_} and {b_}: {sorted(shared)}")
    cfg = json.loads((ROOT / "splits.json").read_text())
    held = {"val_heldout_domain": set(cfg["val_heldout_domain"]), "test_heldout_shape": set(cfg["test_heldout_shape"]),
            "test_prodlike": set(cfg["test_prodlike"])}
    all_held = set().union(*held.values())
    leaks = Counter()
    for name, recs in final.items():
        if name == "test_prodlike":
            continue  # its distractors are drawn from every family by design
        for r in recs:
            for t in r["meta"]["prompt_tables"]:
                fam = index[t]["family"]
                fam = cfg["dimension_owner"].get(fam, fam)
                if (name == "train" and fam in all_held) or (name != "train" and fam not in held[name]):
                    leaks[(name, fam)] += 1
    if leaks:
        problems.append(f"prompts contain tables from another split's domains: {dict(leaks)}")
    ids_seen = Counter(r["meta"].get("id") for recs in final.values() for r in recs if r["meta"].get("id"))
    log["target_families"] = {n: len(f) for n, f in target_fams.items()}
    if problems:
        for p_ in problems:
            print("ERROR:", p_, file=sys.stderr)
        log["errors"] = problems

    for name, recs in final.items():
        for i, r in enumerate(recs):
            r["meta"]["id"] = f"v2_{name}_{i:05d}"
            if name == "test_prodlike":
                r["meta"]["verified"] = r["meta"]["id"] in verified
        path = OUT / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
        log.setdefault(name, {})["final"] = len(recs)
        print(f"{name:22} {len(recs):6d} rows  -> {path.relative_to(ROOT)}")
    (OUT / "assemble_log.json").write_text(json.dumps(log, indent=1) + "\n")
    print(json.dumps(log, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
