"""Unit tests for the v2 generator fixes (plan deliverable 1: P0-1 and P0-2).

Run from dataset/:  ../.venv/bin/python3 -m unittest discover -s tests -v
No Druid cluster needed.
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import generate as G  # noqa: E402
import prompt_formats as pf  # noqa: E402
import templates as T  # noqa: E402
import validators as V  # noqa: E402
from schema_view import SV  # noqa: E402

INDEX = json.loads((ROOT / "schema_index.json").read_text())
T.ANCHOR = G.seed_anchor(INDEX)


class LiteralFidelityP01(unittest.TestCase):
    """P0-1: a filter value is drawn once and shows up identically in question and SQL."""

    def test_every_template_names_the_values_it_filters_on(self):
        checked, bad = 0, {}
        for t in T.T:
            for sid in INDEX:
                for k in range(2):
                    sv = SV(INDEX, sid, random.Random(f"{t['id']}{sid}{k}"))
                    if not G.eligible(t, sv):
                        continue
                    try:
                        q = t["fn"](sv)
                    except Exception:
                        continue
                    checked += 1
                    ok, reasons = V.g3_literal_fidelity(q["question"], q["sql"])
                    if not ok:
                        bad.setdefault(t["id"], reasons[0])
        self.assertGreater(checked, 5000)
        self.assertEqual(bad, {})

    def test_g3_catches_a_swapped_value(self):
        ok, _ = V.g3_literal_fidelity("orders where role is 'driver'",
                                      "SELECT COUNT(*) FROM t WHERE role = 'cleaner'")
        self.assertFalse(ok)

    def test_g3_accepts_mv_contains_and_like_prefix(self):
        self.assertTrue(V.g3_literal_fidelity("rows tagged 'recs_v3'",
                                              "SELECT 1 FROM t WHERE MV_CONTAINS(tags, 'recs_v3')")[0])
        self.assertTrue(V.g3_literal_fidelity("names starting with 'vari'",
                                              "SELECT 1 FROM t WHERE name LIKE 'vari%'")[0])

    def test_g3_rejects_unrequested_filter_unless_an_instruction_supplies_it(self):
        sql = "SELECT 1 FROM t WHERE tier = 'gold'"
        self.assertFalse(V.g3_literal_fidelity("how many rows", sql)[0])
        self.assertTrue(V.g3_literal_fidelity("how many rows", sql, allowed_extra={"gold"})[0])


def _index_with_roles():
    return {"t": {"datasource": "t", "columns": [], "roles": {
        "metrics": [
            {"name": "bytes", "role": "measure_additive", "polarity": "neutral"},
            {"name": "latency_ms", "role": "measure_nonadditive", "polarity": "higher_is_worse"},
            {"name": "status_code", "role": "category_numeric", "polarity": "neutral"}],
        "hi_card": ["user_id"], "epoch_cols": ["end_time", "start_time"]}}}


class RoleComplianceP02(unittest.TestCase):
    """P0-2: the role of a column decides which aggregations are legal."""

    def check(self, sql, **kw):
        return V.g5_role_compliance(sql, ["t"], _index_with_roles(), **kw)[0]

    def test_additive_measure_may_be_summed(self):
        self.assertTrue(self.check('SELECT SUM(bytes) AS "b" FROM t'))

    def test_nonadditive_measure_may_not_be_summed(self):
        self.assertFalse(self.check('SELECT SUM(latency_ms) AS "l" FROM t'))
        self.assertTrue(self.check('SELECT AVG(latency_ms) AS "l" FROM t'))

    def test_sum_of_nonadditive_allowed_when_a_total_is_requested(self):
        self.assertTrue(self.check('SELECT SUM(latency_ms) AS "l" FROM t', allow_sum_total=True))

    def test_category_code_and_id_columns_get_no_math(self):
        self.assertFalse(self.check('SELECT AVG(status_code) AS "s" FROM t'))
        self.assertFalse(self.check('SELECT SUM(user_id) AS "u" FROM t'))

    def test_raw_epoch_column_aggregate_is_rejected_but_a_difference_is_fine(self):
        self.assertFalse(self.check('SELECT AVG(end_time) AS "e" FROM t'))
        self.assertTrue(self.check('SELECT AVG((end_time - start_time) / 1000.0) AS "secs" FROM t '
                                   'WHERE start_time IS NOT NULL AND end_time IS NOT NULL'))
        self.assertTrue(self.check('SELECT MAX(end_time) AS "last_end" FROM t'))

    def test_polarity_table(self):
        import roles
        self.assertEqual(roles.classify_metric("latency_ms")["polarity"], roles.WORSE)
        self.assertEqual(roles.classify_metric("dst_port")["role"], roles.CATEGORY_NUMERIC)
        self.assertEqual(roles.classify_metric("dst_port")["polarity"], roles.NEUTRAL)


class ResultAgreementG4(unittest.TestCase):
    def test_alias_names_do_not_matter(self):
        self.assertTrue(V._rows_match([{"a": 1, "b": 2.0000001}], [{"x": 1, "y": 2.0}]))

    def test_different_values_or_row_counts_fail(self):
        self.assertFalse(V._rows_match([{"a": 1}], [{"a": 2}]))
        self.assertFalse(V._rows_match([{"a": 1}], [{"a": 1}, {"a": 1}]))

    def test_select_list_order_is_ignored(self):
        self.assertTrue(V._rows_match([{"a": "x", "b": 3}], [{"n": 3, "l": "x"}]))

    def test_numeric_strings_equal_numbers(self):
        self.assertTrue(V._rows_match([{"h": 5, "n": 2}], [{"x": "05", "y": 2.0}]))
        self.assertFalse(V._rows_match([{"h": 5}], [{"x": "06"}]))

    def test_an_extra_display_column_still_agrees(self):
        self.assertTrue(V._rows_match([{"city": "a", "avg": 12.5}, {"city": "b", "avg": 11.0}],
                                      [{"c": "b"}, {"c": "a"}]))
        self.assertFalse(V._rows_match([{"city": "a", "avg": 12.5}], [{"c": "z"}]))

    def test_row_order_is_ignored(self):
        self.assertTrue(V._rows_match([{"a": 1}, {"a": 2}], [{"z": 2}, {"z": 1}]))

    def test_table_swap_is_whole_identifier_only(self):
        out = V._swap_tables('SELECT * FROM "ds_a" JOIN ds_a_daily ON 1', {"ds_a": "ds_a__g4b"})
        self.assertEqual(out, 'SELECT * FROM "ds_a__g4b" JOIN ds_a_daily ON 1')


class StyleAndTimeGates(unittest.TestCase):
    def test_business_question_naming_a_column_fails_g7(self):
        self.assertFalse(V.g7_style_truth("sum bytes_sent by device", ["bytes_sent"], "business")[0])
        self.assertTrue(V.g7_style_truth("total data sent by device", ["bytes_sent"], "business")[0])

    def test_calendar_phrase_must_use_calendar_window_g8(self):
        rolling = "SELECT 1 FROM t WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY"
        calendar = ("SELECT 1 FROM t WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP,'P1M'),'P1M',-1) "
                    "AND __time < TIME_FLOOR(CURRENT_TIMESTAMP,'P1M')")
        self.assertFalse(V.g8_time_phrase("orders last month", rolling)[0])
        self.assertTrue(V.g8_time_phrase("orders last month", calendar)[0])


def _g2_index():
    return {"t": {"datasource": "t", "columns": [["__time", "long", ""], ["a", "string", ""], ["m", "long", ""]]},
            "o": {"datasource": "o", "columns": [["__time", "long", ""], ["x", "string", ""]]}}


class GroundingG2(unittest.TestCase):
    def check(self, sql, ids=("t", "o")):
        return V.g2_grounded(sql, list(ids), _g2_index())

    def test_invented_column_fails(self):
        self.assertFalse(self.check('SELECT fake AS "f" FROM t GROUP BY 1')[0])

    def test_column_from_a_distractor_table_fails(self):
        self.assertFalse(self.check('SELECT x AS "x" FROM t GROUP BY 1')[0])

    def test_subquery_and_window_outputs_resolve(self):
        sql = ('SELECT "a", "rn" FROM (SELECT a, ROW_NUMBER() OVER (PARTITION BY a ORDER BY MAX(m) DESC) AS "rn" '
               'FROM t GROUP BY 1) WHERE "rn" <= 3')
        self.assertTrue(self.check(sql)[0], self.check(sql))

    def test_cte_resolves(self):
        sql = 'WITH "s" AS (SELECT a AS "aa", COUNT(*) AS "n" FROM t GROUP BY 1) SELECT "aa", "n" FROM "s" ORDER BY 2 DESC'
        self.assertTrue(self.check(sql)[0], self.check(sql))

    def test_a_self_named_alias_does_not_launder_an_invented_column(self):
        self.assertFalse(self.check('SELECT "fake" FROM (SELECT fake AS "fake" FROM t)')[0])

    def test_qualified_join_columns(self):
        self.assertTrue(self.check('SELECT o.x AS "x", COUNT(*) AS "n" FROM t JOIN o ON t.a = o.x GROUP BY 1')[0])
        self.assertFalse(self.check('SELECT t.x AS "x" FROM t JOIN o ON t.a = o.x GROUP BY 1')[0])


class OutputStyleG0(unittest.TestCase):
    def test_conforming_query(self):
        self.assertTrue(V.g0_style('SELECT a AS "a", COUNT(*) AS "n" FROM t GROUP BY 1 ORDER BY 2 DESC')[0])

    def test_violations(self):
        self.assertFalse(V.g0_style('SELECT a AS a FROM t')[0])                      # unquoted alias
        self.assertFalse(V.g0_style('SELECT a AS "a", COUNT(*) AS "n" FROM t GROUP BY a')[0])
        self.assertFalse(V.g0_style('SELECT a AS "a", COUNT(*) AS "n" FROM t GROUP BY 1 ORDER BY "n"')[0])
        self.assertFalse(V.g0_style('SELECT a AS "a" FROM t;')[0])
        self.assertFalse(V.g0_style('SELECT 1 AS "a"; SELECT 2 AS "b"')[0])

    def test_scan_may_order_by_time(self):
        self.assertTrue(V.g0_style('SELECT __time AS "t", a AS "a" FROM t ORDER BY __time DESC LIMIT 5')[0])


class InstructionsG6(unittest.TestCase):
    D = {"datasource": "t", "instructions": [
        {"kind": "nullable_metric", "binding_column": "m", "text": ""},
        {"kind": "exact_distinct", "binding_column": "u", "text": ""},
        {"kind": "exclude_flag", "binding_column": "is_test_record", "text": ""}],
        "glossary": [{"term": "priority ticket", "binding_column": "a", "values": ["p1", "p2"],
                      "predicate": "a = 'p1' OR a = 'p2'"}]}

    def run_g6(self, sql, q="how many"):
        return V.g6_instructions(sql, q, [self.D])

    def test_obeyed_rules_count_as_changing(self):
        sql = ('SELECT AVG(m) AS "x", COUNT(DISTINCT u) AS "u" FROM t WHERE m IS NOT NULL AND is_test_record = 0')
        ok, reasons, req, forb, changing = self.run_g6(sql)
        self.assertTrue(ok, reasons)
        self.assertEqual(changing, 3)

    def test_violations_are_reported(self):
        ok, reasons, *_ = self.run_g6('SELECT AVG(m) AS "x", APPROX_COUNT_DISTINCT(u) AS "u" FROM t')
        self.assertFalse(ok)
        self.assertGreaterEqual(len(reasons), 3)

    def test_glossary_term_binds_only_when_used_in_the_question(self):
        sql = 'SELECT COUNT(*) AS "n" FROM t WHERE is_test_record = 0'
        self.assertTrue(self.run_g6(sql, "count tickets")[0])
        self.assertFalse(self.run_g6(sql, "count priority tickets")[0])
        good = 'SELECT COUNT(*) AS "n" FROM t WHERE is_test_record = 0 AND a IN (\'p1\', \'p2\')'
        self.assertTrue(self.run_g6(good, "count priority tickets")[0])

    def test_rules_of_a_table_the_query_does_not_read_are_ignored(self):
        self.assertTrue(V.g6_instructions('SELECT COUNT(*) AS "n" FROM other', "q", [self.D])[0])


class FeatureDetection(unittest.TestCase):
    def test_detects_what_is_actually_in_the_sql(self):
        f = V.detect_features('SELECT APPROX_COUNT_DISTINCT(u) AS "u" FROM t WHERE __time >= CURRENT_TIMESTAMP - INTERVAL \'7\' DAY')
        self.assertEqual(f & {"approx", "rolling_window", "json_value"}, {"approx", "rolling_window"})

    def test_epoch_arith_needs_the_pair_columns(self):
        roles = [{"epoch_pairs": [["s_ts", "e_ts", "ms"]]}]
        self.assertIn("epoch_arith", V.detect_features('SELECT AVG((e_ts - s_ts) / 1000.0) AS "d" FROM t', roles))
        self.assertNotIn("epoch_arith", V.detect_features('SELECT AVG(e_ts) AS "d" FROM t', roles))


class GateRegressions(unittest.TestCase):
    """Cases the curated run exposed."""

    def test_timestampdiff_unit_is_not_a_column(self):
        sql = 'SELECT AVG(TIMESTAMPDIFF(HOUR, __time, TIME_PARSE(a))) AS "h" FROM t'
        self.assertTrue(V.g2_grounded(sql, ["t"], _g2_index())[0])

    def test_unnest_alias_columns_resolve(self):
        sql = 'SELECT u.tag AS "tag", COUNT(*) AS "n" FROM t AS d, UNNEST(MV_TO_ARRAY(d.a)) AS u(tag) GROUP BY 1'
        self.assertTrue(V.g2_grounded(sql, ["t"], _g2_index())[0])

    def test_enumerated_values_named_loosely_are_accepted_but_other_values_are_not(self):
        self.assertTrue(V.g3_literal_fidelity("page view count yesterday", "SELECT 1 FROM t WHERE e = 'page_view'")[0])
        self.assertTrue(V.g3_literal_fidelity("decline rate per category", "SELECT 1 FROM t WHERE r = 'declined'")[0])
        self.assertTrue(V.g3_literal_fidelity("roaming users", "SELECT 1 FROM t WHERE p <> 'none'")[0])
        self.assertTrue(V.g3_literal_fidelity("the 10.0 network range", "SELECT 1 FROM t WHERE ip LIKE '10.0.%'")[0])
        self.assertFalse(V.g3_literal_fidelity("orders where role is driver", "SELECT 1 FROM t WHERE role = 'cleaner'")[0])
        self.assertFalse(V.g3_literal_fidelity("count of events", "SELECT 1 FROM t WHERE e = 'purchase'")[0])

    def test_grouping_sets_are_exempt_from_the_ordinal_rule(self):
        sql = 'SELECT a AS "a", COUNT(*) AS "n" FROM t GROUP BY GROUPING SETS ((a), ()) ORDER BY 1'
        self.assertTrue(V.g0_style(sql)[0])


class PilotThreeRegressions(unittest.TestCase):
    def test_cte_alias_reexposed_in_the_outer_query_is_grounded(self):
        sql = 'WITH g AS (SELECT a AS x FROM t GROUP BY 1) SELECT x AS "x", COUNT(*) AS "n" FROM g GROUP BY 1'
        self.assertTrue(V.g2_grounded(sql, ["t"], _g2_index())[0])

    def test_month_formatted_string_equals_a_month_start_timestamp(self):
        self.assertTrue(V._rows_match([{"m": "2026-03", "n": 4}], [{"m": "2026-03-01T00:00:00.000Z", "n": 4}]))
        self.assertFalse(V._rows_match([{"m": "2026-03"}], [{"m": "2026-04-01T00:00:00.000Z"}]))

    def test_time_scope_checker(self):
        import generate_v2 as G
        roll = "SELECT TIME_SHIFT(TIME_FLOOR(__time, 'P1D'), 'P1D', 1) AS \"d\" FROM t WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY"
        self.assertEqual(G.check_time_scope("rolling", roll), [])
        self.assertNotEqual(G.check_time_scope("none", roll), [])
        self.assertNotEqual(G.check_time_scope("last_period", roll), [])
        last = ("SELECT 1 FROM t WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1) "
                "AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')")
        self.assertEqual(G.check_time_scope("last_period", last), [])
        self.assertEqual(G.check_time_scope("none", 'SELECT COUNT(*) AS "n" FROM t'), [])

    def test_infrastructure_errors_are_not_rejections(self):
        self.assertTrue(V._INFRA_PAT.search("<title>Error 502 Bad Gateway</title>"))
        self.assertFalse(V._INFRA_PAT.search("Unknown function 'FOO' (line 1)"))


class ServingFormatParity(unittest.TestCase):
    """md_sections must stay byte-identical to prompt.py's serving-time renderer."""

    def test_md_sections_matches_prompt_py_even_with_notes_and_blanks(self):
        import prompt as serving
        for sid in list(INDEX)[:40]:
            expected = serving.system_prompt(INDEX, [sid])
            got = pf.render("md_sections", INDEX, [sid], "q", notes_for={sid})[0][1]
            self.assertEqual(got, expected, sid)

    def test_md_sections_never_carries_notes(self):
        self.assertNotIn("md_sections", pf.NOTES_CAPABLE)


if __name__ == "__main__":
    unittest.main()
