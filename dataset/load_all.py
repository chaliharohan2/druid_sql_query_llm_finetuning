#!/usr/bin/env python3
"""Load every dataset spec into the running Druid cluster and register lookups."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from harness.client import DruidClient
from harness.loader.ingest import load_datasource, datasource_exists

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from families import FAMILIES  # noqa: E402

# host_tier belongs to the hand-written ds_sec_alerts; the rest come from the
# domain families, which carry their own key -> value maps.
HOSTS = [f"host-{i:03d}" for i in range(60)]
TIERS = ["gold", "silver", "bronze"]
LOOKUPS: dict[str, dict[str, str]] = {"host_tier": {h: TIERS[i % 3] for i, h in enumerate(HOSTS)}}
for _f in FAMILIES:
    if _f["lookup"]:
        _name, _col, _desc, _map = _f["lookup"]
        LOOKUPS[_name] = dict(_map)


def register_lookups(c: DruidClient) -> None:
    url = c._url("/druid/coordinator/v1/lookups/config")
    if c.session.get(url, timeout=20).status_code != 200:
        c.session.post(url, json={}, timeout=30)
        time.sleep(3)
    # Druid only accepts an update whose version sorts after the stored one.
    version = f"v{int(time.time())}"  # must sort after the stored version string
    payload = {"__default": {name: {"version": version, "lookupExtractorFactory":
               {"type": "map", "map": m}} for name, m in LOOKUPS.items()}}
    r = c.session.post(url, json=payload, timeout=30)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"lookup registration failed: HTTP {r.status_code} {r.text[:300]}")
    print(f"lookups registered: {sorted(LOOKUPS)} (Broker propagation takes 2-4 min)")


def main() -> int:
    c = DruidClient()
    if not c.health():
        print("Druid is not up. Run `make up` in druid-harness/.", file=sys.stderr)
        return 1
    register_lookups(c)
    for spec in sorted(ROOT.glob("specs/*.json")):
        name = json.loads(spec.read_text())["name"]
        if datasource_exists(c, name):
            print(f"  {name:22} already loaded, replacing")
        t0 = time.time()
        res = load_datasource(c, spec, replace=True)
        print(f"  {name:22} {res['row_count']:5d} rows  {time.time()-t0:5.1f}s")
    c.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
