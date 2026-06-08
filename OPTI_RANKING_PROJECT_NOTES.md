# OPTI TV — National Optimist Ranking — Project Notes

> Paste this whole file as your first message in a new chat to bring the
> assistant fully up to speed. Append a new dated entry at the end of each
> working session.

---

## WHAT THIS IS
A self-updating national ranking for **Championship Fleet Optimist racing**, built as a
marketing / engagement tool for OPTI TV (not an official USODA selection ranking).
v1 is points-based; an Elo-style v2 is planned later over the same data pipeline.

Pipeline: scrape/load event results → score each event → resolve sailor identity
across events → aggregate best-of-5 mean → output `ranking.json` for display.

---

## CURRENT STATUS (as of latest session)
- Pipeline complete and **verified on live data** across the active event pool.
- Top 15 confirmed accurate by Alberto's own knowledge of the fleet.
- Identity matching verified: max event count = 8 (plausible), no over-merges.
- **NOT yet built:** the delivery layer (GitHub Actions auto-run + Squarespace embed).
  This is the main remaining work. Everything upstream of publishing is done.

---

## THE FILES (all live in one folder on Alberto's Mac, e.g. `~/optitv/files`)
- `scoring.py`  — scoring formula, identity logic, club aliases, duplicate detector
- `scraper.py`  — fetches Clubspot JSON, Regatta Network HTML, and manual CSV; all
                   normalized to one common shape so scoring doesn't care about source
- `run.py`      — orchestrates: load config → fetch each active event → enforce floor →
                   score → aggregate → write `ranking.json` + `flags.log`
- `events.yaml` — THE ONLY FILE EDITED to manage the event pool (add/remove/activate events)
- `manual_results/orange_bowl.csv` — hand-entered Orange Bowl results (226 sailors)
- `requirements.txt`, `.gitignore`, `.github/workflows/update-ranking.yml` (built, not yet deployed)

Run locally with: `python3 run.py`  (needs `pip3 install requests pyyaml beautifulsoup4`)

---

## LOCKED SCORING FORMULA (v1)
```
result_score = ((fleet_size - finish_position) / fleet_size) ** 1.5
               * 100
               * sqrt(fleet_size / 100)
               * event_multiplier
```
- **PLACING_POWER = 1.5** — front-of-fleet emphasis, no hard cutoff (chosen over a
  top-10 bonus because a hard cutoff creates an indefensible 10th-vs-11th cliff).
- **REFERENCE_FLEET = 100** — pivot for the sqrt fleet-size weight (cosmetic scaling;
  does not reorder sailors).
- **sqrt fleet weight** — bigger fleet = harder = higher score (confirmed direction).
- **Event multipliers:** Team Trials ×1.25, Nationals ×1.15, all others ×1.0.
  (Started at 2x/1.5x; reduced because 2x made Trials swamp the best-of-5 mean.)
- **fleet_size** = sailors who SAILED the series (≥1 non-DNC race); excludes all-DNC
  no-shows. NOT registrations (registrations vary by no-show rate and break cross-event
  comparability).
- **finish_position** = rank on series `net` ascending; ties broken by last-race finish.

## AGGREGATION
- **Best-of-5 mean** (mean, not sum — sum rewards travel volume over performance).
- **QUALIFY_MIN_EVENTS = 2** (lowered from 4 to populate the alpha; show event count
  next to each sailor publicly so thin-sample entries are transparent).

## COUNTRY FILTER
- **USA_ONLY = True.** Non-USA `sailNumber_country` codes are EXCLUDED from the ranking
  but STILL COUNTED in fleet_size and finish positions (a US sailor keeps the true
  position they earned against the full fleet). This is the honest treatment.

---

## IDENTITY MATCHING (the hardest part — built in layers)
Key format: `normalized_name | normalized_club`.

**Pattern 1 — spelling/abbreviation variants of the SAME club** → `CLUB_ALIASES` dict
in scoring.py (~123 entries, e.g. CRYC/Coral Reef YC/Coral Reef Yacht Club all → one).
Add new ones as the duplicate detector surfaces them.

**Pattern 3 — private travel teams used instead of a home club** → `TRAVEL_TEAMS` set
in scoring.py. When a sailor's club field is ONLY a travel team (Performance Sailing /
PSI, Coach Pulio, JK Sailing, LISOT, CERT, LOOT, Team Happy, BCS, Team LBI, etc.), the
key falls back to NAME-ONLY. `build_ranking` then absorbs each name-only entry into the
UNIQUE matching real-club sailor of the same name.
  - SAFETY GUARD: if two different real-club sailors share a name, a name-only entry is
    NOT merged into either (refuses to guess → avoids fusing two real people).

**Pattern 2 — sailor legitimately lists two different REAL clubs** → left unmerged
(not an alias problem; would corrupt other sailors from those clubs if aliased).

### Known edge case (rare, periodic eyeball)
Two DIFFERENT same-named sailors who BOTH only ever appear under travel teams (never a
home club) would wrongly merge into one. Watch for an implausibly high event count on a
sailor who's always on travel teams.

### Duplicate detector (in flags.log)
Reports same-name/different-club candidates, ranked worst-first by # sailors affected,
with paste-ready alias lines. **KNOWN FLAW:** it reports on RAW keys (pre-merge), so it
lists duplicates that build_ranking has ALREADY fixed — the count is inflated/misleading.
TODO: make it report POST-merge so the count reflects reality.

---

## EVENT POOL (current, settled)
**In scope:** USODA Championship Fleet events + Team Trials + Orange Bowl. Championship
Fleet only.

**Inclusion rule for NON-USODA events:** must have 150+ in the relevant fleet, most
recent running. Enforced automatically by the scraper at fetch time (auto-drops + flags
if under). Applied WITHOUT exception — this is what keeps the ranking defensible.

**Active events (all verified, sensible fleet sizes):**
sunshine_state, nationals (×1.15), team_trials (×1.25), midwinters, atlantic_coast
gold/silver, great_lakes, gulf_coast, mid_america, midwest, new_england, new_jersey
gold/silver, northwest, pacific_coast, southeast, west_coast, texas_youth, chesapeake,
orange_bowl (manual, ×1.1, 226 boats).

**Dropped:** Halloween Howler (Optimist RWB only ~52 Gold + ~51 Silver = 103 combined,
fails 150 floor — the 376 the scraper first showed included all classes). Other small
non-USODA club events (Buccaneer Blast, etc.) also dropped/likely fail the floor.

**Window:** rolling 12 months, anchored to previous Team Trials date (set in events.yaml
`run_config.window_anchor`).

**Split-fleet events** (Gold/Silver scored separately, e.g. atlantic_coast,
new_jersey): treated as two separate events. CONSEQUENCE ACCEPTED: a Silver-division
winner scores like an event winner though they finished mid-fleet overall. Must be
disclosed on the public methodology page.
  - Orange Bowl was the EXCEPTION: its data was one combined 226-boat overall ranking
    (Red/Blue/White are start groups, not divisions), so it's ONE event, not split.

---

## DATA SOURCES & HOW TO ADD EVENTS
**Clubspot (most USODA events):** endpoint
`results.theclubspot.com/clubspot-results-v4/{regatta_id}?boatClassIDs={class_id}`
returns clean JSON. Get both IDs via Firefox: open results page → F12 → Network →
reload → XHR filter → find the clubspot-results-v4 call. Put `regatta_id` + `class_id`
(must be the CHAMPIONSHIP/GOLD fleet, not Green/RWB/sub-fleet) into events.yaml.
  - GUT-CHECK every new event's fleet_size in flags.log against what you know the event
    drew. A wrong class_id doesn't error — it silently returns the wrong fleet
    (e.g. nationals first came in at 27 = a sub-fleet; correct full fleet was 258).

**Regatta Network:** `platform: regatta_network`, `regatta_id` from the event URL
(`regattanetwork.com/event/{id}`), plus `fleet_filter` = the exact fleet heading text
from the standings page (so it isolates the right fleet from all classes). No class_id.
  - PROVEN WORKING on live data. Note: RN parser may need a column tweak — it captured
    names/positions but produced blank clubs for some sailors; verify club column.

**Manual CSV (for un-scrapable events like Orange Bowl, which is on SAILTI and
robots-blocked):** `platform: manual`, `results_file: manual_results/{name}.csv`.
CSV columns: `finish_position, first_name, last_name, club, sail_number, country`
(country blank for USA, code like "phi"/"arg" for non-US). Every row = a finisher;
fleet_size = row count. Floor + country filter still apply.
  - Alberto is pursuing PERMISSION to access Orange Bowl/SAILTI data directly; if granted,
    could replace manual entry. SAILTI = the platform behind orangebowlregatta.org.

---

## KEY DECISIONS / PRINCIPLES (so they don't get relitigated)
- Identity matching is the core risk and the most important thing to get right — not the formula.
- The 150 floor is applied without exception; waiving it for a wanted event destroys the rule.
- fleet_size = finishers, not registrations (cross-event comparability).
- Best-of-5 MEAN, not sum.
- Conservative on merges: a false merge (two real kids → one) is worse than an unmerged duplicate.
- Anything edited in chat must be mirrored into the real file on the Mac — the assistant
  can't reach the disk. Watch for the "which copy of the file" trap (run.py reads the
  events.yaml in its own folder).

---

## OPEN ITEMS / NEXT SESSION (priority order)
1. **GitHub upload + Actions workflow** — the publish engine; biggest unbuilt piece.
   Workflow file already written (.github/workflows/update-ranking.yml). Needs: create
   GitHub account + public repo, upload all files (incl. the nested workflow path),
   confirm a green manual Actions run that commits ranking.json.
2. **Squarespace embed** — code block on the OPTI TV site that fetches ranking.json and
   renders the leaderboard. Squarespace can't run the pipeline; it only displays output.
3. **Fix duplicate detector to report POST-merge** (small change; stops the misleading
   inflated count).
4. **Decide: 150 floor per-division or on combined fleet** — settle one consistent rule
   for all split events; note on methodology page.
5. **Optional refinements:** work remaining real-club aliases; revisit whether Orange
   Bowl / big-fleet attendance is over-weighted in the top 15; RN parser club-column tweak.

---

## SESSION LOG
### (this session)
- Verified Pattern-3 (travel-team) identity fix on live data: 20 events, max event
  count 8, no over-merge, top 15 confirmed accurate by Alberto.
- Added Orange Bowl as a single 226-boat manual event (data was one combined ranking).
- Proved the Regatta Network scraper works on live data.
- Dropped Halloween Howler + other sub-150 non-USODA events (floor enforcement working).
- Batched ~13 verified real-club aliases into CLUB_ALIASES (now ~123 entries).
- Settled event pool: USODA + Team Trials + Orange Bowl only.
