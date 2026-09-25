#!/usr/bin/env python3
"""Draft additional invented domain families with the teacher LLM (plan Section 5.1:
>=40 train domains + >=10 held out; v1 had 28).

The model writes each family in the same structure as `families.py`; every
draft is validated in code before it is accepted, and the accepted set is
written to `families_v2.json`, which `families.py` appends to FAMILIES. The
output is committed, so regenerating the dataset does not depend on the LLM.

Not AI training or inference code: this only drafts Druid fixture definitions.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import teacher  # noqa: E402
from families import FAMILIES  # noqa: E402

OUT = ROOT / "families_v2.json"
ID_KINDS = ["hex10", "hex12", "hex16", "pod", "host", "driver", "hub", "sku", "bin", "ip", "bed"]

DOMAINS = [
    ("hospital_admissions", "Hospital patient admissions", "admissions", "admission"),
    ("insurance_claims", "Insurance claims processing", "claims", "claim"),
    ("hotel_bookings", "Hotel reservations", "bookings", "booking"),
    ("airline_flights", "Airline flight operations", "flight legs", "flight leg"),
    ("cell_tower_kpis", "Mobile network cell-tower KPIs", "tower samples", "tower sample"),
    ("library_loans", "Public library lending", "loans", "loan"),
    ("university_enrollment", "University course enrolment", "enrolments", "enrolment"),
    ("real_estate_listings", "Real-estate listing activity", "listing events", "listing event"),
    ("restaurant_reservations", "Restaurant table reservations", "reservations", "reservation"),
    ("atm_network", "Bank ATM network transactions", "ATM transactions", "ATM transaction"),
    ("warranty_claims", "Consumer-electronics warranty claims", "warranty claims", "warranty claim"),
    ("wind_turbines", "Wind-farm turbine telemetry", "turbine readings", "turbine reading"),
    ("farm_sensors", "Agricultural field sensors", "field readings", "field reading"),
    ("factory_quality", "Manufacturing quality inspections", "inspections", "inspection"),
    ("recruiting_pipeline", "Recruiting and hiring pipeline", "candidate events", "candidate event"),
    ("legal_matters", "Law-firm matter time entries", "time entries", "time entry"),
    ("event_ticketing", "Live-event ticket sales", "ticket sales", "ticket sale"),
    ("fitness_app", "Fitness app workouts", "workouts", "workout"),
    ("content_moderation", "Social-platform content moderation", "moderation actions", "moderation action"),
    ("ci_pipeline", "CI/CD build pipeline runs", "build runs", "build run"),
    ("service_desk", "IT and customer service desk", "service requests", "service request"),
    ("vehicle_maintenance", "Fleet vehicle maintenance", "work orders", "work order"),
    ("water_utility", "Water utility network metering", "meter reads", "meter read"),
    ("podcast_analytics", "Podcast listening analytics", "listen events", "listen event"),
]

SYSTEM = f"""You design invented, realistic analytics datasets for a Druid text-to-SQL training set.
Output strict JSON only: {{"families": [ ... ]}}. Each family object has exactly these fields:
  key, domain, purpose, time_name, time_desc,
  entity: [name, description, [pool values]],
  dims: [[name, description, [pool values]], ...],
  ids: [[name, description, kind], ...],
  metrics: [[name, "long"|"double", description, gen_spec], ...],
  mvd: null | [name, description, [tag pool]],
  jsn: null | [name, description, {{key: [values]}}],
  lookup: null | [lookup_name, keyed_on_column, description, {{entity_value: mapped_value}}],
  partner: null.
gen_spec is ["uni", lo, hi] | ["logn", mu, sigma] | ["pick", [values]] (numbers only).
Rules:
- snake_case names, unique within a family, never "__time"; time_name must not end in _ms or _s.
- entity + 4 to 6 dims, each pool has 3 to 10 short distinct non-empty strings.
- exactly 2 ids; kind must be one of {ID_KINDS}, chosen so it is a plausible identifier.
- 3 to 6 metrics with realistic units in the name or description; mix additive amounts
  (counts, bytes, cost) and non-additive ones (latency, rate, score, temperature).
- about half the families have mvd, about a third have jsn (its description must end with
  "Keys: a, b, c." matching the dict keys), about half have a lookup whose map covers EVERY entity pool value.
- Use invented content only; never real company names.
- Column descriptions are one plain sentence each; do not mention SQL functions.
Vary the naming conventions and vocabulary between families; do not reuse the example's column names."""


def example_json() -> str:
    f = next(x for x in FAMILIES if x["key"] == "api_gateway")
    keep = {k: f[k] for k in ("key", "domain", "purpose", "time_name", "time_desc", "entity", "dims",
                              "ids", "metrics", "mvd", "jsn", "lookup", "partner")}
    return json.dumps(keep)


def validate(f: dict, taken_keys: set[str]) -> list[str]:
    errs = []
    need = ("key", "domain", "purpose", "time_name", "time_desc", "entity", "dims", "ids", "metrics")
    for k in need:
        if k not in f:
            errs.append(f"missing {k}")
    if errs:
        return errs
    ident = re.compile(r"^[a-z][a-z0-9_]*$")
    if not ident.match(f["key"]) or f["key"] in taken_keys:
        errs.append(f"bad or duplicate key {f['key']!r}")
    names = [f["time_name"], f["entity"][0]] + [d[0] for d in f["dims"]] + [i[0] for i in f["ids"]] \
        + [m[0] for m in f["metrics"]]
    if f.get("mvd"):
        names.append(f["mvd"][0])
    if f.get("jsn"):
        names.append(f["jsn"][0])
    if len(set(names)) != len(names):
        errs.append("duplicate column names")
    for n in names:
        if not ident.match(n) or n == "__time":
            errs.append(f"bad column name {n!r}")
    if f["time_name"].endswith(("_ms", "_s")):
        errs.append("time_name has an encoding suffix")
    pools = [f["entity"][2]] + [d[2] for d in f["dims"]]
    for p in pools:
        if not (3 <= len(p) <= 12) or len(set(p)) != len(p) or any(not isinstance(v, str) or not v for v in p):
            errs.append("bad dim pool")
    if not (4 <= len(f["dims"]) <= 6):
        errs.append("dims count outside 4-6")
    if len(f["ids"]) != 2 or any(i[2] not in ID_KINDS for i in f["ids"]):
        errs.append("ids must be 2 with valid kinds")
    if not (3 <= len(f["metrics"]) <= 6):
        errs.append("metrics count outside 3-6")
    for m in f["metrics"]:
        g = m[3]
        ok = (m[1] in ("long", "double") and isinstance(g, list) and
              ((g[0] in ("uni", "logn") and len(g) == 3 and all(isinstance(x, (int, float)) for x in g[1:]))
               or (g[0] == "pick" and isinstance(g[1], list) and g[1] and all(isinstance(x, (int, float)) for x in g[1]))))
        if not ok:
            errs.append(f"bad metric spec {m[0]}")
        elif g[0] == "uni" and not g[1] < g[2]:
            errs.append(f"uni lo>=hi {m[0]}")
    if f.get("mvd") and not (len(f["mvd"]) == 3 and len(f["mvd"][2]) >= 3):
        errs.append("bad mvd")
    if f.get("jsn"):
        j = f["jsn"]
        if not j[0].endswith("_json"):
            j[0] += "_json"
        keys = list(j[2])
        if not all(isinstance(v, list) and v for v in j[2].values()) or not j[1].rstrip().endswith(
                "Keys: " + ", ".join(keys) + "."):
            errs.append("jsn description must end with 'Keys: ...' listing the dict keys in order")
    if f.get("lookup"):
        lk = f["lookup"]
        if lk[1] != f["entity"][0] or set(lk[3]) != set(f["entity"][2]):
            errs.append("lookup must be keyed on the entity and cover every entity value")
    f["partner"] = None
    return errs


def draft(batch: list[tuple]) -> list[dict]:
    listing = "\n".join(f"- key={k}; domain={d}; rows are {n}" for k, d, n, _ in batch)
    prompt = (f"Example family (structure only, do not copy):\n{example_json()}\n\n"
              f"Write one family for each of these domains:\n{listing}")
    text = teacher.generate(teacher.TEACHER_WRITER, SYSTEM, prompt, temperature=0.9,
                            max_output_tokens=32000, retries=4)
    return teacher._extract_json(text)["families"]


def main() -> int:
    accepted: list[dict] = json.loads(OUT.read_text()) if OUT.exists() else []
    taken = {f["key"] for f in FAMILIES} | {f["key"] for f in accepted}
    todo = [d for d in DOMAINS if d[0] not in taken]
    for attempt in range(3):
        if not todo:
            break
        for i in range(0, len(todo), 4):
            batch = todo[i:i + 4]
            try:
                drafts = draft(batch)
            except teacher.TeacherError as exc:
                print("draft failed:", str(exc)[:200])
                continue
            for f in drafts:
                meta = next((d for d in batch if d[0] == f.get("key")), None)
                if meta is None:
                    continue
                errs = validate(f, {x["key"] for x in accepted} | {x["key"] for x in FAMILIES})
                if errs:
                    print(f"  reject {f['key']}: {errs[:3]}")
                    continue
                f["noun"], f["noun_singular"] = meta[2], meta[3]
                accepted.append(f)
                print(f"  accepted {f['key']}")
            OUT.write_text(json.dumps(accepted, indent=1) + "\n")
        taken = {f["key"] for f in FAMILIES} | {f["key"] for f in accepted}
        todo = [d for d in DOMAINS if d[0] not in taken]
    print(f"{len(accepted)} new families; still missing: {[d[0] for d in todo]}")
    return 0 if not todo else 1


if __name__ == "__main__":
    sys.exit(main())
