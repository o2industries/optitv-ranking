"""
whereis.py — locate a sailor across the event pool, RAW and MERGED.

Answers two questions about one name:
  1. RAW   — every event the name appears in, with the club string AS RECORDED
             and the finishing position, BEFORE any identity merging. This is
             where you spot a coach masquerading as a sailor, a phantom entry,
             or a name that splits across clubs.
  2. MERGED — what build_ranking() collapsed that name into: the final identity
             key(s), how many events were counted, the clubs_seen set, and the
             ranking_score. This tells you whether the merge did the right thing.

It reuses run.py / scoring.py exactly — it does NOT re-implement scoring, so it
can't drift from the real pipeline. Runs the same way run.py does: online, from
the folder that holds events.yaml + scoring.py + scraper.py.

Usage:
    python whereis.py "justin callahan"
    python whereis.py "callahan"          # substring match on the normalized name
    python whereis.py "justin callahan" --config events.yaml

Offline it will fail at the fetch step, same as run.py — that's expected.
"""

from __future__ import annotations
import argparse
import datetime as dt
import sys

import yaml

from scoring import (
    parse_clubspot_event,
    score_event,
    build_ranking,
    normalize_name,
)
from scraper import fetch_event
from run import in_window  # reuse the exact window logic run.py uses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help='sailor name to locate, e.g. "justin callahan" (quote it)')
    ap.add_argument("--config", default="events.yaml")
    args = ap.parse_args()

    needle = normalize_name(*(args.name.split(" ", 1) if " " in args.name
                              else (args.name, "")))
    if not needle:
        print("empty name after normalization; nothing to search")
        sys.exit(1)

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    anchor = cfg["run_config"]["window_anchor"]
    today = dt.date.today()

    # ---------------------------------------------------------------
    # Fetch + parse every active, in-window event the same way run.py does,
    # keeping BOTH the raw SailorResult rows and the scored EventScores.
    # ---------------------------------------------------------------
    raw_hits = []                       # (event_id, club, country, net, sailed, key)
    all_event_scores: dict[str, list] = {}
    skipped = []

    for ev in cfg["events"]:
        eid = ev["id"]
        if not ev.get("active", False):
            continue
        if not in_window(ev.get("date", ""), anchor, today):
            continue
        try:
            payload = fetch_event(ev)
        except Exception as e:
            skipped.append(f"{eid}: fetch failed -> {e}")
            continue

        # RAW rows (pre-merge): match the normalized name as a substring so
        # "callahan" finds "justin callahan" and "joseph callahan".
        for r in parse_clubspot_event(payload):
            rn = normalize_name(r.first, r.last)
            if needle in rn:
                raw_hits.append({
                    "event": eid, "name": f"{r.first} {r.last}".strip(),
                    "club": r.club or "(blank)", "country": r.country or "usa",
                    "net": r.net, "sailed": r.sailed_series, "key": r.key,
                })

        # scored rows for the merged view
        if ev.get("stq_status", False):
            eff_mult = 1.25
        else:
            eff_mult = ev.get("multiplier", 1.0)
        scores = score_event(payload, event_id=eid, event_name=ev["name"],
                             multiplier=eff_mult)
        if scores:
            all_event_scores[eid] = scores

    # ---------------------------------------------------------------
    # RAW report
    # ---------------------------------------------------------------
    print(f'\n=== RAW (pre-merge) matches for "{args.name}" -> normalized contains "{needle}" ===')
    if not raw_hits:
        print("  no raw matches in any active in-window event.")
    else:
        for h in sorted(raw_hits, key=lambda x: (x["name"], x["event"])):
            sailed = "sailed" if h["sailed"] else "DID-NOT-SAIL"
            print(f'  {h["name"]:28} | {h["event"]:20} | club="{h["club"]}" '
                  f'| {h["country"]} | net={h["net"]} | {sailed}')
            print(f'        identity_key -> {h["key"]}')
        # distinct-name warning: more than one underlying name = possible two people
        names = {h["name"] for h in raw_hits}
        if len(names) > 1:
            print(f'\n  NOTE: {len(names)} distinct name spellings matched: {sorted(names)}')
            print('        If these are different people, a substring search is expected to')
            print('        show all of them. If they should be ONE person, check spelling.')

    # ---------------------------------------------------------------
    # MERGED report
    # ---------------------------------------------------------------
    ranking, _, _ = build_ranking(all_event_scores)
    print(f'\n=== MERGED (post-build_ranking) identities containing "{needle}" ===')
    merged = [s for s in ranking if needle in normalize_name(*(s.name.split(" ", 1)
                                       if " " in s.name else (s.name, "")))]
    if not merged:
        print("  name not present in the final ranking (excluded? non-US? below qualify floor?).")
    else:
        # rank position is index in the sorted full field
        pos_of = {id(s): i for i, s in enumerate(ranking, start=1)}
        for s in merged:
            clubs = ", ".join(sorted(c for c in s.clubs_seen)) if s.clubs_seen else "(none recorded)"
            evs = ", ".join(eid for eid, _ in s.scores)
            print(f'  {s.name:28} | full-field rank #{pos_of[id(s)]} '
                  f'| events_counted={s.n_events} | score={s.ranking_score}')
            print(f'        key        -> {s.key}')
            print(f'        clubs_seen -> {clubs}')
            print(f'        events     -> {evs}')

    # ---------------------------------------------------------------
    # Cross-check: did raw and merged event counts line up?
    # ---------------------------------------------------------------
    print('\n=== READ THIS ===')
    raw_events_by_name = {}
    for h in raw_hits:
        raw_events_by_name.setdefault(h["name"], set()).add(h["event"])
    for nm, evset in raw_events_by_name.items():
        print(f'  RAW: "{nm}" appears (sailed or not) in {len(evset)} event(s): {sorted(evset)}')
    print('  Compare the RAW event list to MERGED events_counted above.')
    print('  - If one person shows an IMPLAUSIBLY high count, you may have a false merge.')
    print('  - If a name appears RAW but is MISSING from MERGED, they were filtered')
    print('    (non-US, DID-NOT-SAIL, or below the qualification floor) — verify which.')
    print('  - For a suspected COACH: a real sailor has finishing positions across events;')
    print('    a coach often appears under a coaching/club-staff label or with no real net.')
    if skipped:
        print("\n  events skipped (fetch errors): " + "; ".join(skipped))


if __name__ == "__main__":
    main()
