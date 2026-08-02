"""
OPTI TV ranking — runner.

Orchestrates the full pipeline:
  1. load events.yaml
  2. for each ACTIVE event in the scoring window:
       - fetch results (clubspot or regatta_network)
       - enforce 150 Championship-Fleet floor for non-USODA events (auto-drop + flag)
       - score the event (scoring.py)
  3. aggregate best-of-N mean across events (BEST_N and the qualification floor
     are defined in scoring.py and imported; do not hardcode them here)
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
STQ_MULTIPLIER = 1.25      # Spring Teams Qualifier weight (rotating; set via stq_status in events.yaml)
PUBLISH_TOP_N = 50        # full ranking is computed; only the top N are written to ranking.json
# Sanity floors. A run that falls below either of these is presumed broken
# (upstream fetch failure, bad config) rather than a real collapse in the
# data, so run.py refuses to overwrite a good ranking.json with it.
# Current healthy run: 19 events, ~468 ranked.
MIN_RANKED_FLOOR = 300
MIN_EVENTS_FLOOR = 15
OUTPUT_JSON = "ranking.json"
FLAGS_LOG = "flags.log"

# Display-only club override for sailors who never list a real home club
# (travel-team-only). Keys are normalized lowercase names. Display only —
# does NOT affect identity/merging. Revisit with general passthrough later.
SAILOR_DISPLAY_CLUB = {
    "jaiden strickon": "Performance Sailing Institute",
    "ryan lee": "Coach Pulio Sailing",
    "adam butz": "Performance Sailing Institute",
    "storm husky kim": "Best Coast Sailing",
    "aislyn flynn": "Performance Sailing Institute",
    "joshua wenokur": "JK Sailing",
}



def in_window(event_date: str, anchor: str, today: dt.date) -> bool:
    if not event_date:
        return False
    try:
        d = dt.date.fromisoformat(event_date)
        a = dt.date.fromisoformat(anchor)
    except ValueError:
        return False
    return a <= d <= today


def load_optout_keys(path: str = "optouts.yaml") -> set[str]:
    """Canonical identity keys to remove from PUBLIC output by request.
    Opted-out sailors were scored normally upstream, so they STILL count
    toward other sailors' fleet sizes and finish positions -- they are only
    dropped from ranking.json. Match is on the canonical key, lowercased.
    Missing file -> no opt-outs (safe default).
    NOTE: this filters run.py's final list ONLY. When profiles.json is built it
    MUST filter on this same key set, or an opted-out sailor reappears there."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return set()
    return {(e.get("key") or "").strip().lower()
            for e in (data.get("optouts") or []) if e.get("key")}


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

        # ---- effective multiplier: stq_status overrides the YAML multiplier ----
        # events.yaml is the single source of truth. If stq_status is true this
        # event is the Spring Teams Qualifier and gets STQ_MULTIPLIER regardless
        # of its multiplier field; otherwise the multiplier field is used.
        if ev.get("stq_status", False):
            eff_mult = STQ_MULTIPLIER
        else:
            eff_mult = ev.get("multiplier", 1.0)

        # ---- score ----
        scores = score_event(payload, event_id=eid, event_name=ev["name"],
                              multiplier=eff_mult)
        fleet_size = scores[0].fleet_size if scores else 0
        # Full championship entry. Equals fleet_size for flat events; for
        # qualifying/finals events it is the whole entry, not just Gold.
        strength_size = scores[0].strength_size if scores else 0

        # ---- 150 floor for non-USODA ----
        if not ev.get("usoda", False) and strength_size < NON_USODA_FLOOR:
            flags.append(f"DROP   {eid}: non-USODA entry={strength_size} < {NON_USODA_FLOOR} floor")
            continue

        if fleet_size == 0:
            flags.append(f"DROP   {eid}: no scored sailors (results posted yet?)")
            continue

        all_event_scores[eid] = scores
        stq_note = " [STQ]" if ev.get("stq_status", False) else ""
        gold_note = f", entry={strength_size} [GOLD SPLIT]" if strength_size != fleet_size else ""
        flags.append(f"OK     {eid}: fleet_size={fleet_size}{gold_note}, "
                     f"mult={eff_mult}{stq_note}")

    # ---- aggregate (full field computed; we publish only the top N) ----
    ranking, merge_flags, dup_warnings = build_ranking(all_event_scores)
    if merge_flags:
        flags.append(f"--- {len(merge_flags)} even-split merge(s) refused, review ---")
        flags.extend(merge_flags)
    # ---- opt-out removal (privacy): drop requested sailors from OUTPUT only ----
    # They remain in others' fleet sizes / positions (scored upstream). Removing
    # a mid-list sailor backfills the top N and re-numbers ranks contiguously, so
    # there is no visible gap where they were. A key matching NOBODY is logged
    # LOUD: a stale key means a removed sailor is silently still public.
    optout_keys = load_optout_keys()
    if optout_keys:
        matched = {s.key for s in ranking if s.key in optout_keys}
        ranking = [s for s in ranking if s.key not in optout_keys]
        for k in sorted(matched):
            flags.append(f"OPTOUT removed: '{k}'")
        for k in sorted(optout_keys - matched):
            flags.append(f"OPTOUT NO MATCH: '{k}' matched zero sailors -- stale key? "
                         f"run whereis.py; sailor may STILL be public")
    total_ranked = len(ranking)
    published = ranking[:PUBLISH_TOP_N]

    # ---- duplicate detection (same name, different club -> likely split) ----
    if dup_warnings:
        flags.append(f"--- {len(dup_warnings)} possible duplicate(s) to review ---")
        flags.extend(dup_warnings)

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_anchor": anchor,
        "window_end": today.isoformat(),
        "method": {
            "formula": "((fleet_size - finish_position)/fleet_size)^P * 100 * sqrt(strength_size/REF) * multiplier",
            "fleet_size": "the fleet actually raced (Gold only for qualifying/finals events)",
            "strength_size": "full championship entry across all finals fleets",
            "placing_power": PLACING_POWER,
            "reference_fleet": REFERENCE_FLEET,
            "best_n": BEST_N,
            "qualify_min_events": QUALIFY_MIN_EVENTS,
        },
        "events_scored": list(all_event_scores.keys()),
        "total_ranked": total_ranked,
        "publish_top_n": PUBLISH_TOP_N,
        "rankings": [
            {
                "rank": i,
                "name": s.name,
                "club": (s.key.split("|", 1)[1] if "|" in s.key and s.key.split("|", 1)[1]
                         else SAILOR_DISPLAY_CLUB.get(s.name.strip().lower(), "")),
                "ranking_score": s.ranking_score,
                "events_counted": s.n_events,
            }
            for i, s in enumerate(published, start=1)
        ],
    }

    # ---- sanity guard: refuse to publish a degenerate run ----
    if len(all_event_scores) < MIN_EVENTS_FLOOR or total_ranked < MIN_RANKED_FLOOR:
        flags.append(f"ABORT  refusing to write {OUTPUT_JSON}: "
                     f"events={len(all_event_scores)} (floor {MIN_EVENTS_FLOOR}), "
                     f"ranked={total_ranked} (floor {MIN_RANKED_FLOOR}). "
                     f"Existing ranking.json left untouched.")
        with open(FLAGS_LOG, "w") as f:
            f.write("\n".join(flags) + "\n")
        print("\n".join(flags))
        sys.exit(1)

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    with open(FLAGS_LOG, "w") as f:
        f.write("\n".join(flags) + "\n")

    print(f"wrote {OUTPUT_JSON}: published top {len(output['rankings'])} "
          f"of {total_ranked} ranked sailors from {len(all_event_scores)} events")
    print(f"wrote {FLAGS_LOG}:")
    print("\n".join("  " + line for line in flags))


if __name__ == "__main__":
    main()
