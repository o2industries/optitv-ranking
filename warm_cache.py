"""
warm_cache.py -- fetch every active, in-window event ONCE and store the raw
payload in cache/{event_id}.json.

Run this from your Mac (Clubspot allows your home IP; it 403s GitHub Actions
runners). Commit the cache/ directory. The nightly Action then scores from
cache and never calls Clubspot.

You only re-run this when you ADD an event -- roughly 19 times a year.

Usage:
    python3 warm_cache.py                    # fetch only events missing from cache
    python3 warm_cache.py --refresh          # re-fetch everything, overwrite
    python3 warm_cache.py --only nationals,midwest
"""

from __future__ import annotations
import argparse
import datetime as dt

import yaml

from scraper import fetch_event, read_cache, cache_path
from run import in_window


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="events.yaml")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--only", default="", help="comma-separated event ids")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    anchor = cfg["run_config"]["window_anchor"]
    today = dt.date.today()

    fetched = cached = skipped = failed = 0

    for ev in cfg["events"]:
        eid = ev["id"]
        if only and eid not in only:
            continue
        if not ev.get("active", False):
            print(f"SKIP    {eid}: inactive")
            skipped += 1
            continue
        if not in_window(ev.get("date", ""), anchor, today):
            print(f"SKIP    {eid}: outside window (date={ev.get('date') or 'none'})")
            skipped += 1
            continue
        if ev.get("platform") == "manual":
            print(f"SKIP    {eid}: manual CSV, not cached")
            skipped += 1
            continue

        if not args.refresh and read_cache(eid) is not None:
            rows = len(read_cache(eid)["scoresByRegistration"])
            print(f"CACHED  {eid}: {rows} registrations already on disk")
            cached += 1
            continue

        try:
            payload = fetch_event(ev, refresh=True)
        except Exception as e:
            print(f"FAILED  {eid}: {e}")
            failed += 1
            continue

        rows = len(payload.get("scoresByRegistration", []))
        print(f"FETCHED {eid}: {rows} registrations -> {cache_path(eid)}")
        fetched += 1

    print(f"\n--- fetched {fetched}, already cached {cached}, "
          f"skipped {skipped}, failed {failed} ---")
    print("GUT-CHECK the registration counts above against fleet sizes you know.")
    print("A wrong class_id does NOT error -- it silently returns the wrong fleet.")
    print("Then: upload the cache/ folder to the repo.")


if __name__ == "__main__":
    main()
