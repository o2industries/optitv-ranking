"""
OPTI TV ranking — runner.

Orchestrates the full pipeline:
  1. load events.yaml
  2. for each ACTIVE event in the scoring window:
       - fetch results (clubspot or regatta_network)
       - enforce 150 Championship-Fleet floor for non-USODA events (auto-drop + flag)
       - score the event (scoring.py)
  3. aggregate best-of-5 mean across events (4-event qualification floor)
  4. write ranking.json  (consumed by the Squarespace embed)
     and a flags log    (events dropped / skipped / problems) for your review

Run in an environment WITH network (GitHub Actions). Offline it will fail at
the fetch step — that's expected.

Usage:  python run.py
        python run.py --dry-run   (skip network; score nothing, just validate config)
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import sys

import yaml

from scoring import score_event, build_ranking, find_duplicate_candidates, REFERENCE_FLEET, PLACING_POWER, BEST_N, QUALIFY_MIN_EVENTS
from scraper import fetch_event

NON_USODA_FLOOR = 150
OUTPUT_JSON = "ranking.json"
FLAGS_LOG = "flags.log"


def in_window(event_date: str, anchor: str, today: dt.date) -> bool:
    if not event_date:
        return False
    try:
        d = dt.date.fromisoformat(event_date)
        a = dt.date.fromisoformat(anchor)
    except ValueError:
        return False
    return a <= d <= today


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="validate config, skip network")
    ap.add_argument("--config", default="events.yaml")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    anchor = cfg["run_config"]["window_anchor"]
    today = dt.date.today()
    flags: list[str] = []
    all_event_scores: dict[str, list] = {}

    for ev in cfg["events"]:
        eid = ev["id"]

        if not ev.get("active", False):
            flags.append(f"SKIP   {eid}: inactive")
            continue

        if not in_window(ev.get("date", ""), anchor, today):
            flags.append(f"SKIP   {eid}: outside window (date={ev.get('date') or 'none'}, anchor={anchor})")
            continue

        if args.dry_run:
            flags.append(f"DRYRUN {eid}: would fetch ({ev['platform']})")
            continue

        # ---- fetch ----
        try:
            payload = fetch_event(ev)
        except Exception as e:
            flags.append(f"ERROR  {eid}: fetch failed -> {e}")
            continue

        # ---- score ----
        scores = score_event(payload, event_id=eid, event_name=ev["name"])
        fleet_size = scores[0].fleet_size if scores else 0

        # ---- 150 floor for non-USODA ----
        if not ev.get("usoda", False) and fleet_size < NON_USODA_FLOOR:
            flags.append(f"DROP   {eid}: non-USODA fleet_size={fleet_size} < {NON_USODA_FLOOR} floor")
            continue

        if fleet_size == 0:
            flags.append(f"DROP   {eid}: no scored sailors (results posted yet?)")
            continue

        all_event_scores[eid] = scores
        flags.append(f"OK     {eid}: fleet_size={fleet_size}, mult={ev.get('multiplier',1.0)}")

    # ---- aggregate ----
    ranking = build_ranking(all_event_scores)

    # ---- duplicate detection (same name, different club -> likely split) ----
    dup_warnings = find_duplicate_candidates(all_event_scores)
    if dup_warnings:
        flags.append(f"--- {len(dup_warnings)} possible duplicate(s) to review ---")
        flags.extend(dup_warnings)

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_anchor": anchor,
        "window_end": today.isoformat(),
        "method": {
            "formula": "((fleet_size - finish_position)/fleet_size)^P * 100 * sqrt(fleet_size/REF) * multiplier",
            "placing_power": PLACING_POWER,
            "reference_fleet": REFERENCE_FLEET,
            "best_n": BEST_N,
            "qualify_min_events": QUALIFY_MIN_EVENTS,
        },
        "events_scored": list(all_event_scores.keys()),
        "rankings": [
            {
                "rank": i,
                "name": s.name,
                "ranking_score": s.ranking_score,
                "events_counted": s.n_events,
            }
            for i, s in enumerate(ranking, start=1)
        ],
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    with open(FLAGS_LOG, "w") as f:
        f.write("\n".join(flags) + "\n")

    print(f"wrote {OUTPUT_JSON}: {len(output['rankings'])} ranked sailors "
          f"from {len(all_event_scores)} events")
    print(f"wrote {FLAGS_LOG}:")
    print("\n".join("  " + line for line in flags))


if __name__ == "__main__":
    main()
