"""Glossary term names whose everyday meaning matches their definition (addendum F7).

F2 gave every definition a domain phrase drawn from a per-family list ("rush shipment", "children's loan").
Nothing tied the phrase to the definition, so a phrase could contradict it or imply a time, status, channel or
place the definition never filters on. F7 names each term FROM its definition instead:

  threshold     high-/low-<measure> <noun>      "high-latency request"     (`gateway_latency_ms` > 900)
  not_in        other-<column> <noun>           "other-carrier shipment"   (`carrier_code` <> 'TNT')
  like_prefix   <column>-series <noun>          "device-model-series call record"
  time_active   recently active <entity>        (an entity with a row in the last N days)
  derived       begin-to-finish time            (an epoch difference) / uplink-to-downlink ratio
  value_list, or_cols, mixed
                a neutral coded label           "Tier-B order", "Cohort-K trip", "watchlist loan"

A term never contains a value the definition filters on, never a word that says when/where/how (a stop list
below), never spells out one of the datasource's own column names, and no head word is used more than
`MAX_HEAD` times. Everything is deterministic.

Not AI training or inference code.
"""
from __future__ import annotations

import random
import re
from collections import Counter

MAX_HEAD = 8
STEMS = ["Tier", "Class", "Group", "Band", "Cohort", "Segment", "Bucket", "Set", "Category", "Kind", "Type", "Family",
         "Panel", "Basket", "Pod"]
CODES = list("ABCDEFGHJKLMNPQRSTUVWXYZ") + [str(i) for i in range(1, 10)]
COINED = ["watchlist", "spotlight", "focus-list", "roster", "shortlist", "reference-set", "target-list", "sample-list"]
ACTIVE_WORDS = ["active", "recently active", "recently seen", "engaged", "current", "recent", "lately active"]
HIGH = ["high", "elevated", "top", "peak"]
LOW = ["low", "reduced", "minimal", "bottom"]
NOT_IN = ["other-{c}", "non-listed {c}", "remaining-{c}", "off-list {c}"]
SERIES = ["{c}-series", "{c}-family", "{c}-lineage", "{c}-line"]
TIME_FORMS = ["{s}-to-{e} time", "{s}-{e} duration", "{s}-to-{e} span", "{s}-to-{e} elapsed time", "time from {s} to {e}"]
RATIO_FORMS = ["{a}-to-{b} ratio", "{a}-per-{b} rate", "{a}-vs-{b} ratio", "{a}/{b} ratio"]

# words that would say when / where / how something happened; a term made of a column's own words is exempt
# for those words (the column IS what the definition filters on)
STOP = {"night", "late", "early", "weekend", "holiday", "daily", "weekly", "monthly", "overnight", "morning", "evening",
        "midnight", "seasonal", "overdue", "cancelled", "canceled", "failed", "diverted", "declined", "pending", "delayed",
        "refunded", "expired", "closed", "open", "mobile", "web", "online", "offline", "phone", "email", "app", "store",
        "international", "domestic", "local", "regional", "national", "global", "airport", "urban", "rural", "vip",
        "premium", "critical", "flagged", "notable", "priority", "key", "urgent", "rush", "express"}
_UNIT_WORDS = {"ms", "s", "sec", "secs", "seconds", "min", "mins", "minutes", "hrs", "hours", "usd", "eur", "gbp", "local",
               "pct", "percent", "bytes", "mb", "gb", "kb", "km", "mm", "cm", "psi", "gpm", "kwh", "mbps", "cents", "minor", "ts", "at", "on",
               "miles", "meters", "feet", "kg", "lbs", "gallons", "litres", "celsius", "mah", "dbm", "hz", "mhz", "amps", "volts"}
_FILLER = {"total", "num", "number", "value", "amount", "the", "a", "an"}


# Everyday names for value lists that have one (written by hand, checked in code): the name says what the values
# have in common WITHOUT repeating any of them or one of their words. {n} is the table's noun. A definition that is
# not listed (or whose name a datasource already uses) gets a neutral coded label instead.
SEMANTIC = {
    ("countrycode", "gb,jp"): "UK-or-Japan {n}",
    ("machineclass", "conveyor,kiln,welder"): "heavy-machinery {n}",
    ("category", "grocery,home"): "everyday-goods {n}",
    ("rulename", "beaconing_dns,priv_esc_attempt,suspicious_powershell"): "attack-technique {n}",
    ("analystverdict", "false_positive,true_positive"): "triaged {n}",
    ("region", "apac,eu"): "Asia-Pacific-or-Europe {n}",
    ("env", "prod,staging"): "non-development {n}",
    ("apiname", "payments"): "money-movement {n}",
    ("cdnprovider", "akamai,cloudfront,fastly"): "third-party-CDN {n}",
    ("cdnprovider", "akamai,cloudfront"): "external-CDN {n}",
    ("city", "bogota,toronto"): "Colombian-or-Canadian {n}",
    ("cuisine", "bakery"): "pastry {n}",
    ("fulfilment", "dine_in"): "eat-in {n}",
    ("destcountry", "ie,it"): "Italy-or-Ireland {n}",
    ("destcountry", "gb,ie,pt"): "Ireland-UK-Portugal {n}",
    ("servicelevel", "standard"): "regular-service {n}",
    ("premisetype", "residential"): "household {n}",
    ("ward", "surgical"): "operating-ward {n}",
    ("pair", "btc-usd,sol-usd"): "dollar-quoted {n}",
    ("vehicleclass", "refuse,rigid,van"): "commercial-vehicle {n}",
    ("drivingevent", "harsh_accel,harsh_brake,overspeed"): "risky-driving {n}",
    ("fueltype", "cng,electric,hybrid"): "low-emission {n}",
    ("entrymode", "contactless"): "tap-to-pay {n}",
    ("accelerator", "a10g,h100,l4"): "GPU-served {n}",
    ("promotion", "bogof,clearance"): "discounted {n}",
    ("promotion", "clearance,loyalty,multibuy"): "promotional {n}",
    ("department", "oncology"): "cancer-care {n}",
    ("insurancetype", "private,self_pay"): "non-public-coverage {n}",
    ("filingchannel", "mail"): "postal {n}",
    ("brandname", "extended_stay"): "apartment-style {n}",
    ("originairport", "ord"): "Chicago departure",
    ("aircrafttype", "a350,b777"): "widebody {n}",
    ("aircrafttype", "b777"): "Boeing-widebody {n}",
    ("powerstatus", "generator"): "backup-power {n}",
    ("connectiontech", "5g_nsa,5g_sa"): "fifth-generation {n}",
    ("materialformat", "dvd"): "disc {n}",
    ("genreclassification", "biography"): "life-story {n}",
    ("salecondition", "short_sale"): "distressed {n}",
    ("partysize", "2"): "couple {n}",
    ("partysize", "3"): "trio {n}",
    ("authresult", "declined_bad_pin,declined_insufficient_funds"): "refused {n}",
    ("authresult", "declined_insufficient_funds"): "low-balance-refusal {n}",
    ("productcategory", "laptop"): "portable-computer {n}",
    ("turbinestate", "generating"): "power-producing {n}",
    ("gridconnection", "disconnected,testing"): "off-grid {n}",
    ("croptype", "corn,cotton,soybeans"): "field-crop {n}",
    ("croptype", "alfalfa,soybeans"): "legume {n}",
    ("powersource", "battery,solar"): "off-mains {n}",
    ("sensortype", "irrigation_meter"): "water-use {n}",
    ("department", "legal,marketing"): "non-technical {n}",
    ("practicearea", "corporate"): "business-law {n}",
    ("reportreason", "copyright"): "intellectual-property {n}",
    ("reportreason", "harassment,spam,violence"): "abuse-report {n}",
    ("contenttype", "comment,text_post"): "written-content {n}",
    ("supporttier", "tier_1"): "first-line {n}",
    ("servicetype", "brake_inspection,engine_repair"): "mechanical {n}",
    ("customerclass", "agricultural"): "farm-use {n}",
    ("showgenre", "history,news"): "factual {n}",
    ("listeningplatform", "web_player"): "browser-based {n}",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 4}



def words(col: str) -> list[str]:
    parts = re.split(r"[_\s]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", col))
    return [p.lower() for p in parts if p]


_KEEP_IF_SOLE = {"hours", "minutes", "mins", "seconds", "secs", "rpm", "hz", "mah", "dbm", "gpm", "psi", "kwh", "kg", "miles", "gallons"}
_PARTICLES = {"up", "out", "in", "off", "on", "at", "over", "down"}


def col_word(col: str, keep: int = 2) -> str:
    """`gateway_latency_ms` -> "gateway-latency"; `fareLocal` -> "fare"; `expected_length_of_stay` -> "length-of-stay";
    `laborHours` -> "labor-hours" (a unit stays when it is the only thing that says what the column measures)."""
    raw = words(col)
    w = [x for x in raw if x not in _UNIT_WORDS and x not in _FILLER and len(x) > 1] or raw
    if len(w) == 1:
        units = [x for x in raw if x in _KEEP_IF_SOLE]
        if units:
            w = w + units[:1]
    if "of" in w[1:-1]:  # length-of-stay, cost-of-goods: keep the whole idiom
        i = w.index("of", 1)
        return "-".join(w[i - 1:i + 2])
    return "-".join(w[-keep:])


def edge_word(col: str) -> str:
    """`submitted_ts` -> "submitted", `finishTs` -> "finish", `pickedUp` -> "picked"."""
    w = [x for x in words(col) if x not in _UNIT_WORDS] or words(col)
    if len(w) == 1:
        return w[0]
    if w[-1] in _PARTICLES or w[-1] in {"time", "date", "epoch"}:
        return w[-2]
    return w[-1]


def head(term: str) -> str:
    return term.split()[0].lower()


class Namer:
    def __init__(self, index: dict, avoid_text: str = "", seed: int = 20260926):
        self.index = index
        self.avoid = avoid_text.lower()
        self.rng = random.Random(seed)
        self.used: set[str] = set()
        self.heads: Counter = Counter()

    def ok(self, term: str, d: dict, e: dict, allow_stop_from_cols: set[str], noun: str = "") -> bool:
        t = term.lower()
        if t in self.used or self.heads[head(term)] >= MAX_HEAD:
            return False
        if any(u in t or t in u for u in self.used):
            return False
        for v in list(e.get("values") or []) + [str(n) for n in e.get("numbers") or []]:
            v = str(v).lower()
            if len(v) >= 3 and v in t:
                return False
        mod = t[: -len(noun) - 1] if noun and t.endswith(" " + noun.lower()) else t  # the noun is the table's own word
        toks = set(re.split(r"[-\s/]+", mod))
        if (toks & STOP) - allow_stop_from_cols:
            return False
        vt = set()
        for v in list(e.get("values") or []):
            vt |= _tokens(str(v))
        if vt & _tokens(mod):
            return False  # shares a word with a value the definition filters on
        colnames = {c[0].lower() for c in d["columns"]}
        if toks & colnames:
            return False  # would spell out one of the datasource's own column names (G7)
        if self.avoid and re.search(rf"\b{re.escape(t)}(?:e?s)?(?:'s)?(?!\w)", self.avoid):
            return False
        return True

    def take(self, cands: list[str], d: dict, e: dict, noun: str = "") -> str | None:
        allow = {w for c in e.get("columns", []) for w in words(c)}
        for term in cands:
            if self.ok(term, d, e, allow, noun):
                self.used.add(term.lower())
                self.heads[head(term)] += 1
                return term
        return None

    def neutral(self, sing: str, d: dict, e: dict) -> str:
        cands = [f"{s}-{c} {sing}" for s in self.rng.sample(STEMS, len(STEMS)) for c in self.rng.sample(CODES, 6)]
        cands = self.rng.sample(cands, len(cands))
        if self.rng.random() < 0.18:
            cands = [f"{w} {sing}" for w in self.rng.sample(COINED, len(COINED))] + cands
        term = self.take(cands, d, e, sing)
        assert term, "no neutral label left"
        return term

    def name(self, sid: str, e: dict, sing: str) -> str:
        d = self.index[sid]
        shape = e["shape"]
        rng = self.rng
        cands: list[str] = []
        root = ""
        sem_noun = ""
        if shape == "value_list":
            key = (re.sub(r"_", "", e["columns"][0].lower()), ",".join(sorted(str(v).lower() for v in set(e["values"]))))
            label = SEMANTIC.get(key)
            if label:
                cands = [label.format(n=sing)]
                sem_noun = sing if "{n}" in label else ""
        if shape == "threshold":
            high = e["op"] in (">", ">=")
            cw = col_word(e["columns"][0])
            cands = [f"{w}-{cw} {sing}" for w in (HIGH if high else LOW)]
        elif shape == "not_in":
            cw = col_word(e["columns"][0])
            cands = [f"{f.format(c=cw)} {sing}" for f in NOT_IN]
        elif shape == "like_prefix":
            cw = col_word(e["columns"][0])
            cands = [f"{f.format(c=cw)} {sing}" for f in SERIES]
        elif shape == "time_active":
            ent = e["entity_col"]
            root = re.sub(r"(?:_id|id|_ref|ref|_uid|uid|_hash|hash)$", "", ent, flags=re.I)
            root = " ".join(words(root)) or "entity"
            cands = [f"{w} {root}" for w in rng.sample(ACTIVE_WORDS, len(ACTIVE_WORDS))]
        elif shape == "derived":
            if e.get("derived_kind") == "time":
                s, en = edge_word(e["columns"][0]), edge_word(e["columns"][1])
                if s != en:
                    cands = [f.format(s=s, e=en) for f in TIME_FORMS]
                cands.append("elapsed time")
            else:
                a, b = col_word(e["columns"][0]), col_word(e["columns"][1])
                if a != b:
                    cands = [f.format(a=a, b=b) for f in RATIO_FORMS]
        term = self.take(cands, d, e, sing if shape in ('threshold', 'not_in', 'like_prefix') else sem_noun) if cands else None
        fallback_noun = root if shape == "time_active" else "record" if shape == "derived" else sing
        return term or self.neutral(fallback_noun, d, e)


def rename_all(index: dict, built: dict[str, list[dict]], avoid_text: str = "") -> dict[str, list[tuple[str, str]]]:
    """Give every entry of `built` its F7 name in place (keeping the generation-time name as `gen_term`).
    Returns {schema id: [(gen term, new term), ...]}."""
    namer = Namer(index, avoid_text)
    out: dict[str, list[tuple[str, str]]] = {}
    for sid in sorted(built):
        entries = built[sid]
        if not entries:
            continue
        sing = entries[0]["term"].split(" ", 1)[1] if " " in entries[0]["term"] else "record"
        pairs = []
        for e in entries:
            new = namer.name(sid, e, sing if e["shape"] not in ("derived", "time_active") else sing)
            e["gen_term"] = e["term"]
            e["definition_text"] = e["definition_text"].replace(e["term"][0].upper() + e["term"][1:], new[0].upper() + new[1:], 1)
            e["term"] = new
            pairs.append((e["gen_term"], new))
        out[sid] = pairs
    return out
