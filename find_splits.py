"""
find_splits.py — find sailors split into MULTIPLE ranked identities.

The problem this catches: one real sailor who listed different real clubs at
different events (e.g. "Annapolis YC / Tred Avon YC" at one event, "Tred Avon
YC" alone at another) gets TWO identity keys and appears TWICE in the ranking,
each with a diluted best-of-N score. The automatic merge correctly refuses to
fuse different real clubs (Pattern 2), so these must go in MANUAL_MERGES — but
only after YOU confirm they're one person.

This tool does NOT merge anything. It lists every name that resolves to 2+
identity keys with different real clubs, ranked by how likely they are to be
ONE person, and prints paste-ready MANUAL_MERGES lines for the ones you confirm.

Likelihood signal (heuristic, not proof):
  - HIGH  : the clubs share a word, OR one event's raw club string contained
            BOTH clubs (e.g. "A / B") — almost certainly one sailor splitting.
  - REVIEW: clubs look unrelated — could be one sailor who changed clubs, OR
            two different people with the same name. YOU decide.

Runs like run.py: online, from the folder with events.yaml + scoring.py +
scraper.py.  Usage:  python find_splits.py
"""

from __future__ import annotations
import argparse
import datetime as dt
from collections import defaultdict

import yaml

from scoring import parse_clubspot_event, normalize_name, resolve_club
from scraper import fetch_event
from run import in_window


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="events.yaml")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    anchor = cfg["run_config"]["window_anchor"]
    today = dt.date.today()

    # name -> { club_key -> set(raw_club_strings_seen) }
    # and    name -> set(raw club strings across all events) for the "A / B" test
    by_name: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    raw_strings_by_name: dict[str, set] = defaultdict(set)
    events_by_namekey: dict[tuple, set] = defaultdict(set)
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
            skipped.append(f"{eid}: {e}")
            continue

        for r in parse_clubspot_event(payload):
            if not r.sailed_series:
                continue
            nm = normalize_name(r.first, r.last)
            if not nm:
                continue
            canonical, team_only = resolve_club(r.club)
            # we only care about REAL-club keys here; name-only (travel-team)
            # entries are already absorbed by build_ranking's Pattern-3 pass.
            if team_only or not canonical:
                continue
            by_name[nm][canonical].add(r.club or "")
            raw_strings_by_name[nm].add((r.club or "").lower())
            events_by_namekey[(nm, canonical)].add(eid)

    # candidates: names with 2+ DIFFERENT real-club keys
    candidates = {nm: clubs for nm, clubs in by_name.items() if len(clubs) >= 2}

    def shares_word(a: str, b: str) -> bool:
        wa = {w for w in a.split() if len(w) > 2}
        wb = {w for w in b.split() if len(w) > 2}
        return bool(wa & wb)

    def both_in_one_raw(nm: str, clubs: list[str]) -> bool:
        # did any single raw club string mention 2+ of these canonical clubs?
        for raw in raw_strings_by_name[nm]:
            hits = sum(1 for c in clubs if c.split()[0] in raw)  # cheap contains
            if hits >= 2:
                return True
        return False

    high, review = [], []
    for nm, clubs in candidates.items():
        club_list = sorted(clubs.keys())
        pairwise_word = any(shares_word(a, b)
                            for i, a in enumerate(club_list)
                            for b in club_list[i + 1:])
        combined_raw = both_in_one_raw(nm, club_list)
        rec = (nm, club_list)
        if pairwise_word or combined_raw:
            high.append(rec)
        else:
            review.append(rec)

    def fmt_block(nm, club_list):
        # canonical = key with the most events (keep richest history)
        ranked = sorted(club_list,
                        key=lambda c: len(events_by_namekey[(nm, c)]),
                        reverse=True)
        lines = [f'    # {nm} — split across: ' +
                 ", ".join(f'{c} ({len(events_by_namekey[(nm,c)])} ev)' for c in ranked)]
        lines.append('    [' + ('\n     '.join(f'"{nm}|{c}",' for c in ranked)).rstrip(',') + '],')
        return "\n".join(lines)

    print(f"\n=== HIGH confidence one-sailor splits ({len(high)}) — clubs overlap ===")
    print("Paste into MANUAL_MERGES after confirming each is truly one person:\n")
    for nm, cl in sorted(high):
        print(fmt_block(nm, cl))
    if not high:
        print("  (none)")

    print(f"\n=== REVIEW — could be one sailor OR two same-named people ({len(review)}) ===")
    print("Do NOT paste blindly. Use whereis.py on each to decide:\n")
    for nm, cl in sorted(review):
        ev_counts = ", ".join(f'{c} ({len(events_by_namekey[(nm,c)])} ev)' for c in sorted(cl))
        print(f'  {nm}: {ev_counts}')
    if not review:
        print("  (none)")

    print("\n=== READ THIS ===")
    print(f"  {len(high)} high-confidence + {len(review)} review = {len(candidates)} names with 2+ real-club keys.")
    print("  HIGH = clubs share a word or appeared together in one 'A / B' string -> almost certainly one sailor.")
    print("  REVIEW = unrelated clubs -> could be a club change (one person) or a name collision (two people).")
    print("  Run  python whereis.py \"<name>\"  on any REVIEW entry before merging.")
    print("  Nothing here is merged automatically. You add confirmed lines to MANUAL_MERGES.")
    if skipped:
        print("\n  events skipped (fetch errors): " + "; ".join(skipped))


if __name__ == "__main__":
    main()
