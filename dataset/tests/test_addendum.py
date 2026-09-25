"""Unit tests for the addendum fixes (F1 rules, F2 glossary, F5 nullability, F6 cleanup, G10).

Run from dataset/:  ../.venv/bin/python3 -m unittest discover -s tests -v
No Druid cluster and no model calls needed.
"""
from __future__ import annotations

import json
import random
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import addendum_apply as AA  # noqa: E402
import addendum_lib as AL  # noqa: E402
import glossary_terms as GT  # noqa: E402
import notes_v3  # noqa: E402
import prompt_formats as pf  # noqa: E402
import rule_bank as RB  # noqa: E402
import validators as V  # noqa: E402

INDEX = json.loads((ROOT / "schema_index.json").read_text())
OLD = json.loads((ROOT / "schema_index.pre_addendum.json").read_text())
OLD_HEADS = {"flagged", "priority", "notable", "key"}


class RuleBank(unittest.TestCase):
    def test_thirty_phrasings_per_rule_type_that_all_render(self):
        for kind, bank in RB.RULE_BANK.items():
            self.assertGreaterEqual(len(bank), 30, kind)
            self.assertEqual(len(bank), len(set(bank)), f"duplicate phrasing in {kind}")
            self.assertGreaterEqual(len(RB.CLAUSES[kind]), 8)
            for t in bank + RB.CLAUSES[kind]:
                t.format(c="`x`", b="x", n="events", cw="x ids", w="test records", a_c="a `x`", a_b="a x")  # no unknown slot

    def test_every_phrasing_keeps_the_constraint(self):
        """The wording changes, the constraint must not: G6 keys on the structured metadata, so a phrasing that
        dropped its constraint would teach the opposite of what G6 enforces."""
        need = {"nullable_metric": r"NULL|populated", "exact_distinct": r"exact|precis|COUNT\(DISTINCT|accura|approx",
                "unique_by": r"DISTINCT|COUNT", "exclude_flag": r"\b0\b|zero|= 1", "duration_end": r"NULL|populated|set|recorded|finished|closed"}
        for kind, rx in need.items():
            for t in RB.RULE_BANK[kind] + RB.CLAUSES[kind]:
                self.assertRegex(t, "(?i)" + rx, t)
                self.assertTrue(any(k in t for k in ("{c}", "{b}", "{a_c}", "{a_b}")), f"phrasing names no column: {t}")

    def test_no_phrasing_is_over_five_percent(self):
        rng = random.Random(1)
        d = INDEX["hospital_admissions_v1"]
        counts: dict[str, Counter] = {}
        for _ in range(4000):
            for ins in d["instructions"]:
                _, key = RB.render_rule(ins, d, rng)
                counts.setdefault(ins["kind"], Counter())[key] += 1
        for kind, c in counts.items():
            self.assertLess(max(c.values()) / sum(c.values()), 0.05, kind)

    def test_five_layouts(self):
        s = ["Rule one.", "Rule two."]
        self.assertEqual(RB.layout_lines("plain", "OV", s, "upper"), ["OV", "- Rule one.", "- Rule two."])
        self.assertEqual(RB.layout_lines("instructions", "OV", s, "upper")[1], "### INSTRUCTIONS")
        self.assertEqual(RB.layout_lines("instructions", "OV", s, "title")[1], "### Instructions")
        self.assertEqual(RB.layout_lines("instructions", "OV", s, "title")[2], "* Rule one.")
        self.assertEqual(RB.layout_lines("rules_bold", "OV", s, "upper")[1], "**Rules**")
        self.assertEqual(RB.layout_lines("numbered", "OV", s, "upper")[2], "2. Rule two.")
        self.assertEqual(RB.layout_lines("embedded", "OV", s, "upper"), ["OV Rule one. Rule two."])
        self.assertEqual({n for n, _ in RB.LAYOUTS}, {"plain", "instructions", "rules_bold", "numbered", "embedded"})
        self.assertTrue(all(w >= 0.10 for _, w in RB.LAYOUTS if _ != "instructions"))


class LegacyRenderIsUnchanged(unittest.TestCase):
    def test_v2_prompts_reproduce_byte_for_byte_without_a_style(self):
        pre = ROOT / "v2_pre_addendum" / "train.jsonl"
        if not pre.exists():
            self.skipTest("no pre-addendum snapshot")
        seeds = pf.load_seeds()
        rows = [json.loads(l) for l in pre.read_text().splitlines()[:1200]]
        checked = 0
        for r in rows:
            m = r["meta"]
            if m.get("turns", 1) > 1 or m.get("wrapper") or m.get("source") == "handwritten":
                continue
            nf, ov = AA.detect_notes(r, OLD)
            turns = pf.render(m["format"], OLD, m["prompt_tables"], m["question"], seeds=seeds, notes_for=nf, overview_for=ov)
            self.assertEqual([t for _, t in turns], [x["content"] for x in r["messages"][:-1]], m["id"])
            checked += 1
        self.assertGreater(checked, 300)


class Glossary(unittest.TestCase):
    def test_terms_are_domain_phrases_not_the_old_four(self):
        terms = [e["term"] for d in INDEX.values() for e in d.get("glossary") or []]
        self.assertGreaterEqual(len(set(t.lower() for t in terms)), 300)
        heads = Counter(GT.head_word(t) for t in terms)
        self.assertLessEqual(max(heads.values()) / len(terms), 0.03)
        self.assertEqual(sum(v for k, v in heads.items() if k in OLD_HEADS), 0)

    def test_no_term_contains_another(self):
        terms = sorted({e["term"].lower() for d in INDEX.values() for e in d.get("glossary") or []})
        derived = {e["term"].lower() for d in INDEX.values() for e in d.get("glossary") or [] if e["shape"] == "derived"}
        for a in terms:
            for b in terms:
                if a != b and a not in derived and a in b:
                    self.fail(f"{a!r} is inside {b!r}")

    def test_primary_keeps_its_v2_predicate(self):
        for sid, d in INDEX.items():
            if d.get("glossary"):
                self.assertEqual(d["glossary"][0]["predicate"], OLD[sid]["glossary"][0]["predicate"], sid)
                self.assertNotEqual(d["glossary"][0]["term"], OLD[sid]["glossary"][0]["term"])

    def test_all_eight_shapes_exist_and_render_in_every_pattern(self):
        shapes = {e["shape"] for d in INDEX.values() for e in d.get("glossary") or []}
        self.assertEqual(shapes, set(GT.SHAPES))
        self.assertGreaterEqual(len(notes_v3.DEF_PATTERNS), 8)
        for d in INDEX.values():
            for e in d.get("glossary") or []:
                item = {"term": e["term"], "phrase": e["phrases"][0], "cond": e["conds"][0] if e.get("conds") else None}
                for pid, _, kind, fn in notes_v3.DEF_PATTERNS:
                    if kind == "filter" and not item["cond"]:
                        continue
                    out = "\n".join(fn([item]))
                    self.assertIn(e["term"].lower(), out.lower())

    def test_every_definition_uses_every_literal_it_names(self):
        for d in INDEX.values():
            for e in d.get("glossary") or []:
                for v in e.get("values") or []:
                    if e["shape"] in ("value_list", "or_cols", "not_in", "mixed"):
                        self.assertTrue(any(f"{v}" in p for p in e["phrases"]), (e["term"], v))

    def test_glossary_applied_by_shape(self):
        ok = lambda g, sql: V.glossary_applied(g, re.sub(r"\s+", " ", sql.replace('"', "")))[0]  # noqa: E731
        g = {"shape": "or_cols", "columns": ["a", "b"], "values": ["x"], "numbers": [4], "binding_column": "a", "predicate": "a = 'x' OR b = 4"}
        self.assertTrue(ok(g, "SELECT COUNT(*) FROM t WHERE a = 'x' OR b = 4"))
        self.assertFalse(ok(g, "SELECT COUNT(*) FROM t WHERE a = 'x'"))
        n = {"shape": "not_in", "columns": ["c"], "values": ["p", "q"], "binding_column": "c", "predicate": "c NOT IN ('p','q')"}
        self.assertTrue(ok(n, "SELECT 1 FROM t WHERE c NOT IN ('p', 'q')"))
        self.assertFalse(ok(n, "SELECT 1 FROM t WHERE c IN ('p', 'q')"))
        l = {"shape": "like_prefix", "columns": ["c"], "values": ["ab-"], "binding_column": "c", "predicate": "c LIKE 'ab-%'"}
        self.assertTrue(ok(l, "SELECT 1 FROM t WHERE c LIKE 'ab-%'"))
        self.assertFalse(ok(l, "SELECT 1 FROM t WHERE c = 'ab-'"))
        th = {"shape": "threshold", "columns": ["m"], "values": [], "numbers": [7.5], "binding_column": "m", "predicate": "m > 7.5"}
        self.assertTrue(ok(th, "SELECT 1 FROM t WHERE m > 7.5"))
        self.assertFalse(ok(th, "SELECT 1 FROM t WHERE m > 8"))
        ta = {"shape": "time_active", "columns": ["u"], "values": [], "window_days": 90, "binding_column": "u", "predicate": "x"}
        self.assertTrue(ok(ta, "SELECT COUNT(DISTINCT u) FROM t WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY"))
        self.assertFalse(ok(ta, "SELECT COUNT(DISTINCT u) FROM t"))
        de = {"shape": "derived", "derived_kind": "time", "divisor": 60, "columns": ["s", "e"], "values": [], "binding_column": "s", "predicate": "x"}
        self.assertTrue(ok(de, "SELECT AVG((e - s) / 60) FROM t"))
        self.assertFalse(ok(de, "SELECT AVG(e - s) FROM t"))

    def test_g6_matches_a_term_as_a_phrase_not_a_substring(self):
        d = dict(INDEX["water_utility_v0"])
        g = dict(d["glossary"][0], term="estimated meter read")
        d["glossary"] = [g]
        d["instructions"] = []
        _, reasons, *_ = V.g6_instructions("SELECT 1 FROM " + d["datasource"], "average estimated meter reads", [d])
        self.assertTrue(reasons, "the term (plural) is used, so its predicate must be demanded")
        _, reasons, *_ = V.g6_instructions("SELECT 1 FROM " + d["datasource"], "average estimated meter readings", [d])
        self.assertFalse(reasons)


class NotesStyle(unittest.TestCase):
    def test_deterministic_and_layout_shared_by_the_prompt(self):
        ids = ["ride_hailing_v1", "hospital_admissions_v1"]
        a = pf.render("enterprise_backtick", INDEX, ids, "q", notes_for=set(ids), overview_for=set(ids), notes_style=notes_v3.NotesStyle("s1"))
        b = pf.render("enterprise_backtick", INDEX, ids, "q", notes_for=set(ids), overview_for=set(ids), notes_style=notes_v3.NotesStyle("s1"))
        self.assertEqual(a, b)
        st = notes_v3.NotesStyle("s1")
        pf.render("yaml", INDEX, ids, "q", notes_for=set(ids), overview_for=set(ids), notes_style=st)
        self.assertEqual({v["layout"] for v in st.shown.values()}, {st.layout})

    def test_collision_removes_a_term_but_not_the_primary(self):
        d = INDEX["hospital_admissions_v1"]
        extra = d["glossary"][1]["term"]
        head = extra.split()[0]
        self.assertIn(extra, notes_v3.colliding_terms(d, [f"how many {head} things"]))
        st = notes_v3.NotesStyle("x", exclude_terms=notes_v3.colliding_terms(d, [f"how many {head} things"]))
        self.assertNotIn(extra, [e["term"] for e in st.pick_entries(d)])
        self.assertEqual(st.pick_entries(d)[0]["term"], d["glossary"][0]["term"])

    def test_forced_term_survives_exclusion_and_min_terms(self):
        d = INDEX["hospital_admissions_v1"]
        forced = d["glossary"][2]["term"]
        st = notes_v3.NotesStyle("x", exclude_terms=[forced], force_terms=[forced], min_terms=3)
        terms = [e["term"] for e in st.pick_entries(d)]
        self.assertIn(forced, terms)
        self.assertGreaterEqual(len(terms), 3)


class TermSubstitution(unittest.TestCase):
    def test_plural_case_possessive_and_clipped_forms(self):
        old, new = "priority warranty claim", "repeat warranty claim"
        self.assertEqual(AA.substitute("How many priority warranty claims?", old, new)[0], "How many repeat warranty claims?")
        self.assertEqual(AA.substitute("Priority warranty claim costs", old, new)[0], "Repeat warranty claim costs")
        self.assertEqual(AA.substitute("the priority warranty claim's cost", old, new)[0], "the repeat warranty claim's cost")
        self.assertEqual(AA.substitute("count priority claims by month", old, new)[0], "count repeat warranty claims by month")
        self.assertEqual(AA.substitute("priority tickets", old, new)[1], 0)


class Cleanup(unittest.TestCase):
    def test_sql_in_question(self):
        for q in ["grouped by the TIME_FLOOR of __time", "the `action_taken` field", "sum(x) by day", "the LATEST modelVersion",
                  "APPROX_COUNT_DISTINCT of ids"]:
            self.assertTrue(AA.SQLISH.search(q), q)
        for q in ["How many orders shipped late last month?", "average handling time per agent", "by the month of __time",
                  "What is the latest status of each ticket?", "excluding 0 values (test data)"]:
            self.assertFalse(AA.SQLISH.search(q), q)

    def test_known_hidden_epoch_unit_rows_fail_g10(self):
        pre = ROOT / "v2_pre_addendum" / "train.jsonl"
        if not pre.exists():
            self.skipTest("no pre-addendum snapshot")
        want = {"v2_train_01551", "v2_train_03459"}
        seen = set()
        for l in pre.read_text().splitlines():
            r = json.loads(l)
            if r["meta"]["id"] in want:
                prompt = "\n".join(x["content"] for x in r["messages"][:-1])
                ok, _ = V.g10_epoch_unit(r["messages"][-1]["content"], prompt, r["meta"]["prompt_tables"], OLD)
                self.assertFalse(ok, r["meta"]["id"])
                seen.add(r["meta"]["id"])
        self.assertEqual(seen, want)

    def test_g10_accepts_a_unit_in_the_name_or_the_description(self):
        d = INDEX["ride_hailing_v1"]
        sql = 'SELECT COUNT(*) FROM "rideHailingRaw" WHERE MILLIS_TO_TIMESTAMP("closedOn" * 1000) >= TIMESTAMP \'2026-03-01 00:00:00\''
        self.assertFalse(V.g10_epoch_unit(sql, "closedOn (BIGINT): When the record was closed.", ["ride_hailing_v1"], INDEX)[0])
        self.assertTrue(V.g10_epoch_unit(sql, "closedOn (BIGINT): When the record was closed, in epoch seconds.", ["ride_hailing_v1"], INDEX)[0])
        self.assertTrue(V.g10_epoch_unit(sql, "Sample rows:\n closedOn | 1710000000", ["ride_hailing_v1"], INDEX)[0])
        self.assertEqual(d["datasource"], "rideHailingRaw")


class Nullability(unittest.TestCase):
    def rec(self, sql, tables):
        return {"meta": {"prompt_tables": tables, "unanswerable": False}, "messages": [{"content": sql}]}

    def test_share_time_column_protected_and_never_unflipped(self):
        d = INDEX["hospital_admissions_v1"]
        sql = 'SELECT AVG(("finishTs" - "beginTs") / 60) FROM "hospitalAdmissionsRaw" WHERE "patientId" IS NOT NULL'
        shares = []
        for seed in range(30):
            ov, st = AL.nullable_override(self.rec(sql, ["hospital_admissions_v1"]), INDEX, random.Random(seed), 1.0)
            nullable = set(ov["hospital_admissions_v1"])
            self.assertNotIn(d["time_col"], nullable)
            self.assertLessEqual(set(d["nullable_columns"]), nullable)
            self.assertNotIn("beginTs", nullable)  # duration arithmetic with no IS NOT NULL guard on it
            shares.append(len(nullable) / (len(d["columns"]) - 1))
        self.assertGreater(min(shares), 0.55)
        self.assertLess(max(shares), 0.85)


class F7Names(unittest.TestCase):
    def test_terms_are_named_from_their_definitions(self):
        import term_names as TN
        terms = [(sid, e) for sid, d in INDEX.items() for e in d.get("glossary") or []]
        self.assertGreaterEqual(len({e["term"].lower() for _, e in terms}), 300)
        heads = Counter(GT.head_word(e["term"]) for _, e in terms)
        self.assertLessEqual(max(heads.values()) / len(terms), 0.03)
        for sid, e in terms:
            t = e["term"].lower()
            for v in list(e.get("values") or []) + [str(n) for n in e.get("numbers") or []]:
                if len(str(v)) >= 3:
                    self.assertNotIn(str(v).lower(), t, (e["term"], v))
            noun = INDEX[sid]["glossary"][0]["gen_term"].split(" ", 1)[-1].lower()
            mod = t[: -len(noun) - 1] if t.endswith(" " + noun) else t
            vt = set().union(*[TN._tokens(str(v)) for v in e.get("values") or []]) if e.get("values") else set()
            self.assertFalse(vt & TN._tokens(mod), (e["term"], vt))
            own = {w for c in e["columns"] for w in TN.words(c)}
            self.assertFalse((set(re.split(r"[-\s/]+", mod)) & TN.STOP) - own, e["term"])
            neutral = r"^([a-z]+-[a-z0-9] |watchlist |spotlight |focus-list |roster |shortlist |reference-set |target-list |sample-list )"
            if e["shape"] == "threshold":
                self.assertRegex(t, r"^(high|elevated|top|peak|low|reduced|minimal|bottom)-|" + neutral)
            if e["shape"] == "not_in":
                self.assertRegex(t, r"^(other-|non-listed |remaining-|off-list )|" + neutral)

    def test_column_words(self):
        import term_names as TN
        self.assertEqual(TN.col_word("gateway_latency_ms"), "gateway-latency")
        self.assertEqual(TN.col_word("fareLocal"), "fare")
        self.assertEqual(TN.col_word("expected_length_of_stay"), "length-of-stay")
        self.assertEqual(TN.col_word("laborHours"), "labor-hours")
        self.assertEqual(TN.edge_word("pickedUp"), "picked")
        self.assertEqual(TN.edge_word("finishTs"), "finish")


class TextBugs(unittest.TestCase):
    def test_no_doubled_backticks_in_any_phrasing(self):
        for kind, bank in RB.RULE_BANK.items():
            for t in bank + RB.CLAUSES[kind]:
                self.assertNotRegex(t, r"`[^`]*\{c\}[^`]*`", t)  # {c} already carries its own backticks
        rng = random.Random(0)
        for d in INDEX.values():
            for ins in d.get("instructions") or []:
                for _ in range(20):
                    self.assertNotRegex(RB.render_rule(ins, d, rng)[0], r"(?<!`)``(?!`)")

    def test_plurals(self):
        self.assertEqual(RB.col_words("recipientHash"), "recipient hashes")
        self.assertEqual(RB.col_words("correlation_hash"), "correlation hashes")
        self.assertEqual(RB.col_words("address"), "addresses")
        self.assertEqual(RB.col_words("policy"), "policies")
        self.assertEqual(RB.col_words("driver_id"), "driver ids")
        self.assertEqual(AA.fix_plurals("Count exact unique recipientHashs and hashs"), "Count exact unique recipientHashes and hashes")

    def test_articles_follow_the_column_name(self):
        self.assertEqual(RB.with_article("order_id", "`order_id`"), "an `order_id`")
        self.assertEqual(RB.with_article("user_id", "`user_id`"), "a `user_id`")
        ins = {"kind": "nullable_metric", "binding_column": "order_total", "applies_to": "metric", "text": ""}
        d = {"noun": "orders"}
        for i, t in enumerate(RB.RULE_BANK["nullable_metric"]):
            if "{a_c}" in t:
                self.assertIn("an `order_total`", t.format(**RB._slots(ins, d, random.Random(0))))
        self.assertEqual(GT.article("estimated trip"), "an")
        self.assertEqual(AA.substitute("had a priority trip", "priority trip", "estimated trip")[0], "had an estimated trip")
        self.assertEqual(AA.substitute("had an expedited trip", "expedited trip", "Tier-B trip")[0], "had a Tier-B trip")
        self.assertEqual(AA.substitute("A priority trip", "priority trip", "estimated trip")[0], "An estimated trip")

    def test_time_active_definitions_use_the_right_article(self):
        for d in INDEX.values():
            for e in d.get("glossary") or []:
                if e["shape"] == "time_active":
                    first = TN_words(e["entity_col"])
                    self.assertTrue(e["phrases"][0].startswith(GT.article(first) + " `"), e["phrases"][0])


def TN_words(col):
    import term_names as TN
    return TN.words(col)[0]


if __name__ == "__main__":
    unittest.main()
