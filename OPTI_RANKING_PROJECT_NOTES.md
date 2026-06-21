# OPTI TV — National Optimist Ranking — Project Notes

> Paste this whole file as your first message in a new chat to bring the
> assistant fully up to speed. Append a new dated entry at the end of each
> working session.
>
> RULE: this file must match the live code and config. If you change `BEST_N`,
> a multiplier, the event pool, or the identity logic, update this file in the
> SAME session. Stale notes caused real confusion before (see History).

---

## WHAT THIS IS
A self-updating national ranking for **Championship Fleet Optimist racing**, built as a
marketing / engagement tool for OPTI TV (not an official USODA selection ranking).
v1 is points-based; an Elo-style v2 is planned later over the same data pipeline.

Pipeline: scrape/load event results → score each event → resolve sailor identity
across events → aggregate **best-of-3 mean** → output `ranking.json` for display.

---

## CURRENT STATUS (as of 2026-06-21)
- Pipeline complete, verified on live data, **deployed and live.**
- Identity layer rebuilt this session (frequency-merge, see below) — duplicate
  double-counting fixed; post-merge detector reports zero unresolved splits.
- `ranking.json` (top 50, with club field) pushed to GitHub and fetched by a live
  Squarespace leaderboard at `/how-to-fix-team-trials`.
- Methodology copy written and config-verified (Gold-only, best-of-3, multipliers).
- **NOT yet verified:** the daily GitHub Action actually running green and
  committing a fresh `ranking.json`. Until confirmed, the board is STATIC, not
  self-updating. This is the #1 open item.

---

## THE FILES (working folder: `/Users/albertoolivo/Desktop/Python scripts/files/`)
> NOTE: this working folder is NOT a git repo. The GitHub repo
> `o2industries/optitv-ranking` is updated by manual website upload, not by
> `git push` from this folder. Local and repo can drift — always confirm the
> raw URL matches local after any change. (This bit you once: a stale 450-sailor
> best-of-3 file was live while local was different.)

- `scoring.py`  — scoring formula, identity logic (frequency merge + passes),
                   club aliases, travel-team set, post-merge duplicate detector
- `scraper.py`  — fetches Clubspot JSON and manual CSV; normalized to one shape
- `run.py`      — orchestrates: load config → fetch each active event → enforce floor →
                   score → aggregate → write `ranking.json` + `flags.log`.
                   Holds `SAILOR_DISPLAY_CLUB` override map (travel-team-only sailors).
- `events.yaml` — THE ONLY FILE EDITED to manage the event pool. Also holds the
                   STQ rotation flag and multipliers (single source of truth).
- `manual_results/orange_bowl.csv` — hand-entered Orange Bowl results (226 sailors)
- `whereis.py` / `find_splits.py` / `keydump.py` — diagnostic tools
- `requirements.txt`, `.gitignore`, `.github/workflows/update-ranking.yml`

Run locally: `python3 run.py`  (needs `pip3 install requests pyyaml beautifulsoup4`)

---

## SCORING FORMULA (v1)
```
result_score = ((fleet_size - finish_position) / fleet_size) ** 1.5
               * 100
               * sqrt(fleet_size / 100)
               * event_multiplier
```
- **PLACING_POWER = 1.5** — front-of-fleet emphasis, no hard cutoff (a hard cutoff
  creates an indefensible 10th-vs-11th cliff).
- **REFERENCE_FLEET = 100** — pivot for the sqrt fleet-size weight.
- **sqrt fleet weight** — bigger fleet = harder = higher score. CHECKED this session:
  the top sailors rank high on PERCENTILE, not fleet-size scaling — sqrt was NOT
  over-weighting them. Left unchanged. (If revisited, sweep the exponent against
  judgment across the whole top 20, not two sailors.)
- **Multipliers live in `events.yaml`, NOT hardcoded.** Current: Team Trials 1.25,
  Nationals 1.15, rotating Spring Teams Qualifier (STQ, currently Midwest) 1.25,
  Orange Bowl 1.1, all others 1.0. `run.py` overrides the multiplier for STQ events.
- **fleet_size** = sailors who SAILED the series (≥1 non-DNC race); excludes all-DNC
  no-shows. NOT registrations.
- **finish_position** = rank on series `net` ascending; ties broken by last-race finish.

## AGGREGATION
- **Best-of-3 mean.** (Changed from best-of-5 on 2026-06-21 — deliberate reversal of
  the earlier best-of-5 lock. Best-of-3 rewards peak performance over consistency;
  chosen for a more exciting public board. Mean, not sum — sum rewards travel volume.)
  The public methodology page hard-codes "best three results" — if this changes,
  UPDATE THE PAGE.
- **QUALIFY_MIN_EVENTS = 2.** Event count shown next to each public name.

## COUNTRY FILTER
- **USA_ONLY = True.** Non-USA codes EXCLUDED from output but STILL COUNTED in
  fleet_size and finish positions (a US sailor keeps the true position earned against
  the full fleet).

---

## IDENTITY MATCHING (rebuilt 2026-06-21 — read this carefully)
Key format: `normalized_name | normalized_club`.

The core fix this session: `resolve_club()` picks the FIRST real club in a multi-club
string (e.g. "Lakewood YC / SFYC / StFYC"), so a sailor whose affiliation is listed in
different orders across events split into multiple keys. This was the real cause of most
"duplicates" — not 54 hand-merges, one parsing rule.

**Pass -1 — frequency merge (NEW, the main mechanism).** In `build_ranking`, before
all other passes: any name with ≥2 distinct real-club keys is collapsed to ONE canonical
key — the club they appear under in the MOST events. A strictly larger count merges
freely. An exact TIE at the top is the collision-risk zone and merges ONLY if the two
keys share a sail number; otherwise it is refused and flagged (MERGE? in flags.log) for
manual review. This auto-resolved ~52 of the splits that used to need hand-merging.
  - Sail number is carried onto `EventScore` and accumulated per sailor for the tie guard.
  - Guard skips any key already covered by MANUAL_MERGES (Pass 0 handles those).

**Pass 0 — MANUAL_MERGES** (`list[list[str]]` in scoring.py). For genuine oddities the
frequency rule can't resolve. Currently holds ONE entry: Eloise Hild (1–1 split, blank
sail numbers, confirmed one sailor). Each inner list = full `name|club` keys, canonical
first.

**Pass 2 — travel-team name-only absorption.** When `resolve_club` returns travel-team-
only, the key falls back to NAME-ONLY and is absorbed into the unique real-club sailor of
the same name. SAFETY GUARD: if two real-club sailors share a name, refuses to merge.

**CLUB_ALIASES** — spelling/abbreviation variants of the SAME club → one canonical form.
Added this session: `indian harbor` and `lisot indian harbor yacht club` → indian harbor
yacht club (fixed Owen Santini, who was dropping below the floor).

**TRAVEL_TEAMS** — set of private travel teams / coaching programs (PSI, Coach Pulio,
LISOT, CERT, BCS, Team Happy, etc.). Stripped from club strings when slash-separated.

### Known limitations (still open)
- **Space-prefixed travel teams defeat stripping.** `resolve_club` only strips
  slash-separated segments, so "LISOT Indian Harbor Yacht Club" (no slash) is one blob.
  Patched Owen via aliases; the general case will recur. Aliases catch them one at a time.
- **The `lyc → lauderdale` alias is WRONG and still in the table.** LYC is ambiguous
  (Lavallette / Lauderdale / Larchmont / Leland). Frequency masks its ranking effect but
  it poisons `clubs_seen` (e.g. Bryce Anderson shows a phantom Lauderdale). KILL IT.
- **Leaderboard display reads the canonical KEY's club, not `clubs_seen`** — correct,
  because merged sailors have multi-club `clubs_seen` sets that would display ambiguously.
- **`SAILOR_DISPLAY_CLUB` override map** in run.py holds travel-team-only sailors who have
  no real home club to display (Strickon→PSI, Lee→Coach Pulio, Butz→PSI, Kim→BCS). This
  is a hand-maintained list — new travel-team-only sailors entering the top 50 need adding.
  Long-term: a general affiliation-passthrough instead.

### Duplicate detector — FIXED this session
`find_split_survivors(sailors)` now reports POST-merge (operates on the final sailors
dict), so it lists only genuine unresolved same-name splits, not ones already fixed.
Currently reports ZERO. The old pre-merge `find_duplicate_candidates` still exists in
scoring.py, unused — delete next session.

---

## EVENT POOL (current, settled)
**In scope:** USODA Championship Fleet events + Team Trials + Orange Bowl. **GOLD FLEET
ONLY** — Silver results are NOT included. (This reverses the earlier "score Gold/Silver
separately as two events" approach. Silver is now excluded entirely; cleaner, and kills
the indefensible "Silver winner scores like an event winner" problem.)

**Inclusion rule for NON-USODA events:** must have 150+ in the relevant fleet.
Applied WITHOUT exception. USODA championship events are floor-EXEMPT (canonical pool by
definition) — this asymmetry is disclosed on the methodology page.

**Active events (19, all verified):**
sunshine_state, nationals (1.15), team_trials (1.25), midwinters, atlantic_coast_gold,
great_lakes, gulf_coast, mid_america, midwest (STQ 1.25), new_england, new_jersey_gold,
northwest, pacific_coast, southeast, west_coast, texas_youth, chesapeake, valentines,
orange_bowl (manual, 1.1, 226 boats).
  - Only `_gold` splits are active; no `_silver` events in events.yaml (verified 2026-06-21).

**Small USODA fleets (Northwest ~22, Great Lakes ~30, etc.):** count fully under the
floor-exemption policy. Not yet decided whether to handle these differently — OPEN.

**Window:** rolling 12 months, anchored to previous Team Trials date
(events.yaml `run_config.window_anchor`).

---

## DATA SOURCES & HOW TO ADD EVENTS
**Clubspot (most USODA events):**
`results.theclubspot.com/clubspot-results-v4/{regatta_id}?boatClassIDs={class_id}`
returns clean JSON. Get both IDs via Firefox: results page → F12 → Network → reload →
XHR filter → find the clubspot-results-v4 call. Put `regatta_id` + `class_id` (must be
the CHAMPIONSHIP/GOLD fleet) into events.yaml.
  - GUT-CHECK every new event's fleet_size in flags.log. A wrong class_id doesn't error —
    it silently returns the wrong fleet (nationals first came in at 27 = a sub-fleet;
    correct was 258).

**Manual CSV (Orange Bowl — on SAILTI, robots-blocked):** `platform: manual`,
`results_file: manual_results/{name}.csv`. Columns:
`finish_position, first_name, last_name, club, sail_number, country`. Every row = a
finisher; fleet_size = row count. Floor + country filter still apply.
  - Pursuing permission for direct SAILTI/Orange Bowl access; would replace manual entry.

**Regatta Network:** parser exists but NO RN events currently in the pool.

---

## DEPLOYMENT
- **Repo:** `o2industries/optitv-ranking` (public). Updated by manual website upload
  (working folder is not a git clone).
- **What Squarespace fetches:** `ranking.json` via raw GitHub URL
  `raw.githubusercontent.com/o2industries/optitv-ranking/main/ranking.json`.
  Squarespace only DISPLAYS output — it does not run the pipeline.
- **Leaderboard:** HTML/JS Code Block on `/how-to-fix-team-trials`, brand-styled
  (black/white/hornet-yellow #FFCB05, Helvetica Bold). Fetches the raw URL, renders top
  50 with rank/name/club/event-count/score, "last updated" from generated_at, fetch-fail
  fallback. Club display uses a prettify map + title-case fallback.
- **CORS confirmed working** between Squarespace and raw.githubusercontent.
- **To update the live board:** edit code → `python3 run.py` → upload the NEW
  `ranking.json` (and any changed .py / events.yaml) to GitHub → confirm raw URL matches.

---

## KEY DECISIONS / PRINCIPLES
- Identity matching is the core risk, not the formula.
- 150 floor applied without exception for non-USODA; USODA events floor-exempt.
- fleet_size = finishers, not registrations.
- **Best-of-3 mean** (as of 2026-06-21).
- **Gold Fleet only** (as of 2026-06-21).
- Conservative on merges: a false merge (two kids → one) is worse than an unmerged dup.
- `events.yaml` is the single source of truth for multipliers — never hardcode in scoring.py.
- Always verify on-disk / live state before trusting notes. The "which copy" trap is real
  at both the file level (staged scoring.py once reverted a fix) and the deploy level
  (stale ranking.json was live while local differed).

---

## OPEN ITEMS / NEXT SESSION (priority order)
1. **VERIFY THE DAILY GITHUB ACTION.** Trigger the workflow manually, confirm a green run
   that commits a fresh `ranking.json`. Until this works, "self-updating" is not true and
   the board is static. Check the Node-20 deprecation — bump Actions versions if needed.
2. **Kill the `lyc → lauderdale` alias** (wrong, ambiguous, poisons clubs_seen).
3. **Fix the space-prefixed travel-team case** in resolve_club (general, not per-sailor).
4. **Delete the dead `find_duplicate_candidates`** function in scoring.py (unused).
5. **Decide small-USODA-fleet handling** (Northwest ~22, Great Lakes ~30 score as full
   qualifying events under floor-exemption).
6. **Replace `SAILOR_DISPLAY_CLUB` override map** with a general affiliation-passthrough.
7. **Pursue SAILTI/Orange Bowl direct access** (replace manual CSV).
8. **Long-term:** Elo-style v2 over the same pipeline.

---

## KEY LEARNINGS
- The "21 confirmed merges" from a prior session were stale; the live detector found 54
  HIGH candidates, most of which were ONE parsing bug (first-token club selection in
  multi-club strings), not 54 real merges. Lesson: regenerate from current data, don't
  trust frozen lists.
- Sail number is NOT stable enough to be the identity key (new boats, charters, reuse
  across years), but within a single year it IS a reliable TIEBREAK / guard for even splits.
- Changing build_ranking's return signature breaks every caller (whereis.py, run.py) —
  update them in the same pass.
- web_fetch can't see post-JavaScript page state — verify a live JS-rendered page in an
  incognito browser, not via fetch.

---

## SESSION LOG
### 2026-06-21
- Discovered MANUAL_MERGES held only 1 entry (Justin Callahan), not the "21 confirmed"
  the prior notes claimed — live ranking was double-counting split sailors.
- Root-caused the splits to `resolve_club` picking the first real club in multi-club
  strings (order-dependent). Replaced hand-merging with a **frequency-merge Pass -1**:
  canonical club = most-frequent, even-split ties guarded by sail-number agreement.
- Threaded sail_number onto EventScore + RankedSailor for the tie guard.
- Verified: Will Kaiser (9 events, was split 6+3), Bryce Anderson (5, was split by a bad
  LYC alias), the four Irions stayed distinct, Owen Santini (alias fix), Eloise Hild
  (manual merge). Post-merge detector reports zero unresolved splits.
- Fixed the duplicate detector to report POST-merge (`find_split_survivors`).
- **Switched best-of-5 → best-of-3** (deliberate; rewards peak over consistency).
- **Switched to Gold-Fleet-only** (Silver excluded; reverses split-fleet-as-two-events).
- Added `club` field to ranking.json (canonical key source) + `SAILOR_DISPLAY_CLUB`
  override map for 4 travel-team-only top-50 sailors.
- Pushed clean pipeline + ranking.json to GitHub; confirmed live raw URL.
- Built and deployed the brand-styled Squarespace leaderboard (CORS confirmed).
- Wrote methodology copy (Gold-only, best-of-3, multipliers, 150 floor, ranking-vs-
  selection distinction), config-verified against events.yaml.
- Confirmed sqrt fleet weight is NOT over-weighting top sailors (they rank on percentile).
