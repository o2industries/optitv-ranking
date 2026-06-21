"""
keydump.py — list the exact current identity keys for sailors who appear under
more than one key (i.e. the split duplicates). Run this, paste the output back,
and use it to build the MANUAL_MERGES list in scoring.py.

Usage:  python3 keydump.py
(Run from the same folder as run.py / scoring.py / events.yaml.)
"""
import datetime as dt
import yaml
from scoring import score_event, identity_key
from scraper import fetch_event

# Names you've confirmed are split duplicates to merge. Add/remove as needed.
# (Tzhone siblings handled separately — Harmony and Hayden stay distinct.)
TARGET_NAMES = {
    "marshall rodriguez", "patrick rodriguez", "charles wesley hoffman",
    "christian freyre", "george wyatt tyson", "mia diab", "david fantarella",
    "bryce hryniewicz", "hayden spamer", "colton rapalje", "maxwell jones",
    "molly kern", "will kaiser", "grant munder", "natalie buchner",
    "george geye", "benjamin qualshie", "levi manchester", "justin callahan",
    "bryce anderson", "harmony tzhone", "hayden tzhone",
}

with open("events.yaml") as f:
    cfg = yaml.safe_load(f)
anchor = cfg["run_config"]["window_anchor"]
today = dt.date.today()

def in_window(d, a, t):
    if not d: return False
    try:
        d = dt.date.fromisoformat(d); a = dt.date.fromisoformat(a)
    except ValueError:
        return False
    return a <= d <= t

# name -> set of (key) seen
from collections import defaultdict
keys_by_name = defaultdict(set)

for ev in cfg["events"]:
    if not ev.get("active", False):
        continue
    if not in_window(ev.get("date",""), anchor, today):
        continue
    try:
        payload = fetch_event(ev)
    except Exception as e:
        print(f"# skip {ev['id']}: {e}")
        continue
    scores = score_event(payload, event_id=ev["id"], event_name=ev["name"])
    for es in scores:
        nm = es.key.split("|",1)[0]
        if nm in TARGET_NAMES:
            keys_by_name[nm].add(es.key)

print("\n# ---- exact keys per duplicate name (paste this back) ----")
for nm in sorted(keys_by_name):
    keys = sorted(keys_by_name[nm])
    print(f"\n# {nm}  ({len(keys)} keys)")
    for k in keys:
        print(f'#   {k!r}')
