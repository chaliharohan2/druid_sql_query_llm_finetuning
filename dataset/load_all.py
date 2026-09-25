#!/usr/bin/env python3
"""Load every dataset spec into the running Druid cluster and register lookups."""
from __future__ import annotations
import hashlib, json, sys, time
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


STATE = ROOT / ".load_state.json"


def _fingerprint(spec: Path) -> str:
    """Changes whenever the spec or its seed file changes, so a resumed run reloads only what is stale."""
    seed = (spec.parent / json.loads(spec.read_text())["seed"]["path"]).resolve()
    h = hashlib.sha1(spec.read_bytes())
    h.update(str(seed.stat().st_size).encode())
    h.update(hashlib.sha1(seed.read_bytes()).digest())
    return h.hexdigest()


def main() -> int:
    c = DruidClient()
    if not c.health():
        print("Druid is not up. Run `make up` in druid-harness/.", file=sys.stderr)
        return 1
    register_lookups(c)
    done = json.loads(STATE.read_text()) if STATE.exists() else {}
    for spec in sorted(ROOT.glob("specs/*.json")):
        name = json.loads(spec.read_text())["name"]
        fp = _fingerprint(spec)
        if done.get(name) == fp and datasource_exists(c, name):
            print(f"  {name:22} up to date, skipped")
            continue
        t0 = time.time()
        res = load_datasource(c, spec, replace=True)
        done[name] = fp
        STATE.write_text(json.dumps(done))  # row_count here can lag the publish; verify_load.py checks the real count
        print(f"  {name:22} loaded  {time.time()-t0:5.1f}s", flush=True)
    c.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
