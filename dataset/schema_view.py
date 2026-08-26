"""A bound view of one schema, used by query templates.

Templates never name a column. They ask the view for a role -- a low-cardinality
dimension, a numeric measure, the multi-value dimension, the epoch-seconds column
-- and the view supplies a real column name plus, where relevant, a literal drawn
from that column's actual value pool. Binding literals to the pool is what keeps a
generated WHERE clause from filtering every row away.

Not AI training or inference code.
"""
from __future__ import annotations

import random

# Windows a question can ask about, paired with the phrasing that goes in the
# prompt. All of them sit inside the 30 days of seed data.
WINDOWS = [
    ("'24' HOUR", "the last 24 hours", "PT1H"),
    ("'2' DAY", "the last 2 days", "PT1H"),
    ("'3' DAY", "the past three days", "PT6H"),
    ("'7' DAY", "the last 7 days", "P1D"),
    ("'7' DAY", "the past week", "P1D"),
    ("'14' DAY", "the last fortnight", "P1D"),
    ("'21' DAY", "the last 21 days", "P1D"),
    ("'30' DAY", "the last 30 days", "P1D"),
]
GRAINS = [("PT1H", "hourly", "hour"), ("PT15M", "in 15 minute buckets", "bucket"),
          ("PT5M", "in 5 minute windows", "window"), ("P1D", "daily", "day"),
          ("P1W", "weekly", "week"), ("PT6H", "in six hour blocks", "block")]


# Column names Druid will not accept unquoted, determined by probing every
# column name in the index against the live cluster (see docs/DATASET.md).
# They are excluded from the generic roles so that ordinary templates never
# have to think about quoting, and surfaced separately for the templates whose
# whole job is to teach it.
RESERVED = {"value", "language"}


def esc(v) -> str:
    """A SQL literal for a pool value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class SV:
    """Role-addressed view of one schema in the index."""

    def __init__(self, index: dict, sid: str, rng: random.Random):
        self.index, self.id, self.rng = index, sid, rng
        self.d = index[sid]
        self.r = self.d["roles"]
        self.pools = self.d["pools"]
        self.ds = self.d["datasource"]
        self.t = "__time"
        self.domain = self.d["domain"]
        self._dims = [c for c in self.r["dims"] if c not in RESERVED]
        self._hi = [c for c in self.r["hi_card"] if c not in RESERVED]
        self._metrics = [m for m in self.r["metrics"] if m["name"] not in RESERVED]
        self.reserved_cols = [c for c, _, _ in self.d["columns"] if c in RESERVED]
        self.reserved_num = [c for c, t, _ in self.d["columns"]
                             if c in RESERVED and t in ("long", "double", "float")]

    # ---------------------------------------------------------------- roles
    def has(self, *needs: str) -> bool:
        for n in needs:
            if n == "metrics" and not self._metrics:
                return False
            if n == "two_metrics" and len(self._metrics) < 2:
                return False
            if n == "double" and not self._of_type("double"):
                return False
            if n == "dims" and not self._dims:
                return False
            if n == "two_dims" and len(self._dims) < 2:
                return False
            if n == "hi_card" and not self._hi:
                return False
            if n == "reserved" and not self.reserved_cols:
                return False
            if n == "reserved_num" and not self.reserved_num:
                return False
            if n in ("mvd", "json", "lookup", "partner", "epoch_ms", "epoch_s",
                     "str_space", "str_iso") and not self.r.get(n):
                return False
        return True

    def _of_type(self, ctype: str) -> list[str]:
        return [m["name"] for m in self._metrics if m["type"] == ctype]

    # ------------------------------------------------------------- columns
    def dim(self) -> str:
        return self.rng.choice(self._dims)

    def dim_nonkey(self) -> str:
        """A dimension other than the join key -- grouping a join by its own key is trivial."""
        key = self.r.get("dim_key")
        pool = [c for c in self._dims if c != key] or self._dims
        return self.rng.choice(pool)

    def dims(self, n: int) -> list[str]:
        return self.rng.sample(self._dims, min(n, len(self._dims)))

    def met(self, ctype: str | None = None) -> str:
        names = [m["name"] for m in self._metrics]
        return self.rng.choice(self._of_type(ctype) or names) if ctype else self.rng.choice(names)

    def mets(self, n: int) -> list[str]:
        names = [m["name"] for m in self._metrics]
        return self.rng.sample(names, min(n, len(names)))

    def hi(self) -> str:
        return self.rng.choice(self._hi)

    def reserved(self) -> str:
        """A column whose name is a SQL reserved word, for the templates that teach quoting."""
        return self.rng.choice(self.reserved_cols)

    def reserved_numeric(self) -> str:
        return self.rng.choice(self.reserved_num)

    def lit(self, col: str):
        pool = self.pools.get(col)
        return esc(self.rng.choice(pool)) if pool else "''"

    def lits(self, col: str, n: int) -> str:
        pool = self.pools.get(col) or [""]
        vals = self.rng.sample(pool, min(n, len(pool)))
        return ", ".join(esc(v) for v in vals)

    def prefix(self, col: str) -> str:
        """A LIKE prefix that actually matches rows in this column."""
        pool = self.pools.get(col) or [""]
        v = str(self.rng.choice(pool))
        return esc(v[:max(1, len(v) // 2)] + "%")

    # ------------------------------------------------------------- extras
    @property
    def mvd(self) -> str:
        return self.r["mvd"]

    def mvd_val(self) -> str:
        return esc(self.rng.choice(self.pools[self.r["mvd"]]))

    def mvd_vals(self, n: int) -> str:
        return ", ".join(esc(v) for v in self.rng.sample(self.pools[self.r["mvd"]], n))

    @property
    def json(self) -> str:
        return self.r["json"]

    def json_key(self) -> str:
        return self.rng.choice(sorted(self.r["json_keys"]))

    def json_val(self, key: str) -> str:
        return esc(self.rng.choice(self.r["json_keys"][key]))

    @property
    def lookup(self) -> tuple[str, str]:
        return tuple(self.r["lookup"])

    def partner(self) -> tuple["SV", str, str]:
        p = self.r["partner"]
        return SV(self.index, p["schema"], self.rng), p["local"], p["remote"]

    # -------------------------------------------------------------- phrases
    def window(self):
        return self.rng.choice(WINDOWS)

    def wide_window(self):
        """A window of at least a week, for questions that also filter on a value."""
        return self.rng.choice([w for w in WINDOWS if "HOUR" not in w[0]
                                and int(w[0].split("'")[1]) >= 7])

    def grain(self):
        return self.rng.choice(GRAINS)

    def noun(self) -> str:
        """What a row of this table is, in plain words."""
        return NOUNS.get(self.d["family"], "records")


NOUNS = {
    "app_logs": "log records", "api_gateway": "API requests", "cdn_edge": "edge requests",
    "streaming_media": "playback heartbeats", "ride_hailing": "trips", "food_delivery": "orders",
    "logistics": "parcel scans", "energy_meter": "meter readings",
    "clinical_telemetry": "observations", "workforce": "shifts", "email_campaigns": "email events",
    "inventory": "stock movements", "crypto_trades": "trades", "support_tickets": "ticket updates",
    "fleet_gps": "vehicle samples", "payment_gateway": "authorisation attempts",
    "ml_inference": "predictions", "retail_pos": "till lines", "network_flows": "network flows",
    "web_events": "events", "ad_impressions": "impressions", "iot_readings": "sensor readings",
    "orders": "orders", "products": "products", "fin_txn": "transactions",
    "sec_alerts": "alerts", "game_sessions": "sessions", "telco_cdr": "call records",
    "dim_drivers": "drivers", "dim_carriers": "carriers", "dim_warehouses": "warehouses",
    "dim_segments": "segments", "dim_stores": "stores",
}
