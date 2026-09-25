"""sqlglot helpers shared by the gates.

Not AI training or inference code: this only validates generated SQL.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

# Positional unit-keyword arguments to these functions parse as bare
# `exp.Column` nodes with no table qualifier -- they are not column
# references at all. druid_sql_dataset_v2_plan.md G2 calls this out
# explicitly.
_UNIT_FUNCS = {"TIMESTAMPDIFF", "TIMESTAMPADD"}
_UNIT_WORDS = {"SECOND", "MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "QUARTER",
              "YEAR", "MILLISECOND", "MICROSECOND"}


def parse(sql: str) -> exp.Expression:
    return sqlglot.parse_one(sql, read="druid")


def table_aliases(tree: exp.Expression) -> dict[str, str]:
    """alias (or bare name) -> real table/datasource name."""
    out = {}
    for t in tree.find_all(exp.Table):
        real = t.name
        out[real] = real
        if t.alias:
            out[t.alias] = real
    return out


def referenced_columns(tree: exp.Expression) -> list[tuple[str | None, str]]:
    """(table_alias_or_None, column_name) for every real column reference.

    Filters out the unit-keyword false positives from TIMESTAMPDIFF/TIMESTAMPADD.
    """
    out = []
    for col in tree.find_all(exp.Column):
        parent = col.parent
        if (col.table == "" and col.name.upper() in _UNIT_WORDS
                and isinstance(parent, exp.Anonymous)
                and parent.this.upper() in _UNIT_FUNCS):
            continue
        out.append((col.table or None, col.name))
    return out


def string_literals(tree: exp.Expression) -> list[str]:
    return [lit.this for lit in tree.find_all(exp.Literal) if lit.is_string]


# Functions whose string arguments are structural (grain periods, Joda
# formats, JSON paths, interval units) rather than filter values drawn from
# the question. A literal that is merely an argument to one of these is not
# a G3 concern.
_STRUCTURAL_FUNCS = {"TIME_FLOOR", "TIME_CEIL", "TIME_SHIFT", "TIME_PARSE", "TIME_FORMAT",
                     "TIME_EXTRACT", "JSON_VALUE", "PARSE_JSON", "TRY_PARSE_JSON",
                     "LOOKUP"}
# Functions whose string arguments are filter values, just as `col = 'x'` is.
_VALUE_FUNCS = {"MV_CONTAINS", "MV_OVERLAP", "MV_FILTER_ONLY", "MV_FILTER_NONE",
                "MV_OFFSET_OF", "MV_ORDINAL_OF"}


def filter_literals(tree: exp.Expression) -> list[str]:
    """String literals that are the operand of a comparison predicate.

    This is what G3 (literal fidelity) should check -- not every string
    literal in the query, which would also catch grain periods like 'PT1H'
    or Joda format strings like 'yyyy-MM-dd', neither of which the question
    "names" in the sense the gate cares about.
    """
    out = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
        for lit in node.find_all(exp.Literal):
            if lit.is_string and not is_structural_literal(lit):
                out.append(lit.this)
    for node in tree.find_all(exp.Anonymous):
        if isinstance(node.this, str) and node.this.upper() in _VALUE_FUNCS:
            out.extend(lit.this for lit in node.find_all(exp.Literal) if lit.is_string)
    return out


def is_structural_literal(lit: exp.Literal) -> bool:
    node = lit.parent
    while node is not None:
        if isinstance(node, exp.Anonymous) and isinstance(node.this, str) \
                and node.this.upper() in _STRUCTURAL_FUNCS:
            return True
        if isinstance(node, (exp.TimeToStr, exp.StrToTime, exp.Interval)):
            return True
        node = node.parent
    return False


AGG_FUNCS = {"SUM": exp.Sum, "AVG": exp.Avg, "MIN": exp.Min, "MAX": exp.Max,
            "COUNT": exp.Count, "APPROXCOUNTDISTINCT": None}


def _bare_column(node: exp.Expression) -> str | None:
    """The column name if `node` is just a column (optionally CAST/ROUND-wrapped)."""
    while isinstance(node, (exp.Cast, exp.Round, exp.Paren)):
        node = node.this
    return node.name if isinstance(node, exp.Column) else None


def aggregations(tree: exp.Expression) -> list[tuple[str, list[str], bool, list[str]]]:
    """(func_name_upper, [column names inside it], is_distinct, [bare column arguments]).

    A bare argument is the aggregate applied straight to a column, e.g.
    SUM(latency_ms). AVG(end_ts - start_ts) has no bare argument: role rules
    are about aggregating a column as-is, not about arithmetic between columns.
    """
    out = []
    for node in tree.find_all(exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count):
        name = type(node).__name__.upper()
        is_distinct = bool(getattr(node, "args", {}).get("distinct"))
        cols = [c.name for c in node.find_all(exp.Column)]
        arg = node.this
        if isinstance(arg, exp.Distinct):
            arg = arg.expressions[0] if arg.expressions else arg
        bare = _bare_column(arg)
        out.append((name, cols, is_distinct, [bare] if bare else []))
    for node in tree.find_all(exp.Anonymous):
        fn = node.this.upper() if isinstance(node.this, str) else ""
        if fn in ("APPROX_COUNT_DISTINCT", "APPROX_QUANTILE_DS", "LATEST_BY",
                 "EARLIEST", "LATEST"):
            cols = [c.name for c in node.find_all(exp.Column)]
            bare = _bare_column(node.expressions[0]) if node.expressions else None
            out.append((fn, cols, False, [bare] if bare else []))
    return out


def has_construct(sql: str, token: str) -> bool:
    return token.upper() in sql.upper()
