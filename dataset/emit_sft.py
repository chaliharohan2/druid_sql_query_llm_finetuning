#!/usr/bin/env python3
"""Render validated examples into chat-format SFT files.

Only examples that passed all four gates are written. Each record is rendered
through the prompt format assigned in generate.py, so the twelve formats stay
evenly represented in the file the trainer actually reads.

Writes train.jsonl, val.jsonl and dataset_stats.json.

Not AI training or inference code: this writes training *data*.
"""
from __future__ import annotations

import json
import sys
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import prompt_formats as pf  # noqa: E402


def render(e: dict, index: dict, seeds: dict) -> dict:
    turns = pf.render(e["format"], index, e["schemas"], e["question"], seeds,
                      order_seed=zlib.crc32(e["id"].encode()))
    messages = [{"role": role, "content": text} for role, text in turns]
    messages.append({"role": "assistant", "content": e["sql"]})
    return {"messages": messages,
            "meta": {"id": e["id"], "cluster": e["cluster"], "template": e["template"],
                     "schemas": e["query_schemas"], "prompt_tables": e["schemas"],
                     "distractors": e.get("distractors", []), "format": e["format"]}}


def stats(records: list[dict]) -> dict:
    """Character lengths, as a proxy for tokens the trainer will see."""
    prompt = [sum(len(m["content"]) for m in r["messages"][:-1]) for r in records]
    with_distractors = sum(1 for r in records if r["meta"]["distractors"])
    answer = [len(r["messages"][-1]["content"]) for r in records]
    prompt.sort()
    return {
        "records": len(records),
        "with_distractor_tables": with_distractors,
        "prompt_chars": {"min": prompt[0], "p50": prompt[len(prompt) // 2],
                         "p95": prompt[int(len(prompt) * 0.95)], "max": prompt[-1],
                         "mean": round(sum(prompt) / len(prompt))},
        "answer_chars": {"min": min(answer), "max": max(answer),
                         "mean": round(sum(answer) / len(answer))},
        "clusters": dict(Counter(r["meta"]["cluster"] for r in records).most_common()),
        "formats": dict(Counter(r["meta"]["format"] for r in records).most_common()),
        "schemas": dict(Counter(s for r in records for s in r["meta"]["schemas"]).most_common()),
        "prompt_tables": dict(Counter(s for r in records
                                      for s in r["meta"]["prompt_tables"]).most_common()),
    }


def main() -> int:
    data = json.loads((ROOT / "examples.json").read_text())
    index = json.loads((ROOT / "schema_index.json").read_text())
    report = json.loads((ROOT / "validation_report.json").read_text())
    seeds = pf.load_seeds()

    out_stats = {"anchor": data["anchor"],
                 "holdout_families": data["holdout_families"],
                 "train_schemas": data["train_schemas"],
                 "val_schemas": data["val_schemas"]}

    for split in ("train", "val"):
        res = report[split]
        kept = [e for e in data[split] if not res.get(e["id"], {"fails": ["missing"]})["fails"]]
        dropped = len(data[split]) - len(kept)
        records = [render(e, index, seeds) for e in kept]
        path = ROOT / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        out_stats[split] = stats(records)
        out_stats[split]["dropped_by_gates"] = dropped
        print(f"{path.name}: {len(records)} records ({dropped} dropped by gates)")

    # The whole point of the split: no schema may appear on both sides.
    overlap = set(out_stats["train"]["prompt_tables"]) & set(out_stats["val"]["prompt_tables"])
    assert not overlap, f"schema leakage between train and val: {sorted(overlap)}"
    print(f"schema-disjoint: {len(out_stats['train']['schemas'])} train schemas, "
          f"{len(out_stats['val']['schemas'])} val schemas, 0 shared")

    (ROOT / "dataset_stats.json").write_text(json.dumps(out_stats, indent=1) + "\n")
    for split in ("train", "val"):
        s = out_stats[split]
        print(f"  {split}: prompt chars p50={s['prompt_chars']['p50']} "
              f"p95={s['prompt_chars']['p95']} max={s['prompt_chars']['max']}, "
              f"answer mean={s['answer_chars']['mean']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
