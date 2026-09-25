#!/usr/bin/env python3
"""One-line-per-pool progress: kept / rejected / keep rate / estimated spend / top rejecting gates."""
import json, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import teacher

TARGET = {"train_pool": 6000, "val_pool": 350, "test_shape_pool": 400, "prodlike_pool": 350, "followup_pool": 300}
total = 0.0
for pool, n in TARGET.items():
    kept = ROOT / f"{pool}.jsonl"
    if not kept.exists():
        continue
    k = sum(1 for _ in kept.open())
    rej_path = ROOT / f"{pool}_rejects.jsonl"
    rej = [json.loads(l) for l in rej_path.open()] if rej_path.exists() else []
    gates = Counter(g for r in rej for g in r["gates"]).most_common(4)
    usage = ROOT / f"{pool}_usage.json"
    cost = teacher.estimated_cost_usd(json.loads(usage.read_text())) if usage.exists() else 0.0
    total += cost
    print(f"{pool:16} {k:5d}/{n:<5d} rejected {len(rej):5d} keep {100*k/max(1,k+len(rej)):4.0f}%  ~${cost:6.0f}  top gates {gates}")
print(f"estimated spend across pools: ~${total:,.0f}")
