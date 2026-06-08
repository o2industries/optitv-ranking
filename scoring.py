"""
OPTI TV national ranking — v1 scoring engine (alpha).

Pipeline stage this covers: takes parsed Clubspot result JSON for an event,
computes per-sailor result_score, applies the event multiplier, then aggregates
each sailor's best-of-5 results into a mean across all events.

It deliberately does NOT scrape (no network here) or resolve identities beyond a
normalize-name+club key. Those are separate stages. This is the math layer,
built against the real JSON shape from the 2026 Sunshine State endpoint.

Locked v1 spec encoded here:
  result_score = ((fleet_size - finish_position) / fleet_size)^1.5 * 100 * sqrt(fleet_size / REFERENCE_FLEET)
                 then * event_multiplier
  REFERENCE_FLEET   = 100
  PLACING_POWER     = 1.5  (front-of-fleet emphasis, no cutoff)
  fleet_size        = sailors who sailed the series (>=1 non-DNC race); excludes all-DNC no-shows
  finish_position   = rank on `net` ascending; ties broken by last-race finish
  aggregation       = mean of best 5 result_scores; qualification floor = 4 events
  multipliers       = Team Trials 1.25, Nationals 1.15, all else 1.0
"""

from __future__ import annotations
import math
import re
import unicodedata
from dataclasses import dataclass, field

REFERENCE_FLEET = 100
PLACING_POWER = 1.5   # front-of-fleet emphasis; no hard cutoff
BEST_N = 5
QUALIFY_MIN_EVENTS = 2
USA_ONLY = True       # exclude non-USA country codes from the ranking
ALLOWED_COUNTRY = "usa"

# Manual merges: sailors YOU have confirmed are the same person but who split
# across DIFFERENT REAL CLUBS (so the automatic merge correctly refuses to
# guess). Each entry is a list of identity keys to fuse into one sailor.
# Keys are in "normalized name|normalized club" form (the form shown in the
# flags.log duplicate detector). The FIRST key in each list is the canonical
# one whose name/club the merged sailor displays under.
#
# How to fill this: when the ranking shows the same real sailor twice, find
# their two keys (name|club) and add a line here. Only do this when you are
# CONFIDENT it is one person — a wrong merge fuses two real kids.
#
# Example:
#   ["marshall rodriguez|lakewood yacht club",
#    "marshall rodriguez|lauderdale yacht club",
#    "marshall rodriguez|pensacola yacht club"],
MANUAL_MERGES: list[list[str]] = [
    # add confirmed same-sailor key groups here, one list per sailor
]

# Event multipliers. Keyed by a canonical event id you assign in your config.
# Anything not listed defaults to 1.0.
EVENT_MULTIPLIERS = {
    "team_trials": 1.25,
    "nationals": 1.15,
}

# Letter scores that mean "did not produce a finishing position in that race."
# A sailor with one of these in a race still counts as a series participant
# as long as they have a real net result (handled via series participation, not per-race).
DNC_LIKE = {"DNC", "DNS", "DNF", "RET", "NSC"}  # treated as "didn't sail that race"
# Note: BFD / UFD / OCS / DSQ are penalties for sailors who DID compete -> still a finisher.


# ---------------------------------------------------------------------------
# Identity normalization
# ---------------------------------------------------------------------------

# Club alias table: maps known variant spellings of the SAME club to one
# canonical normalized form, so a sailor isn't split across events.
# Keys and values must be in the post-normalization form (lowercase, no
# punctuation, single spaces). Add a line whenever a duplicate surfaces.
# The duplicate-detector in run.py will flag candidates for you.
CLUB_ALIASES = {
    # ---- Coral Reef Yacht Club (incl. typos) ----
    "cryc": "coral reef yacht club",
    "coral reef yc": "coral reef yacht club",
    "coral reef": "coral reef yacht club",
    "coral reef yatch club": "coral reef yacht club",
    "coral reif yacht club": "coral reef yacht club",
    "coral reef yacht club": "coral reef yacht club",

    # ---- Annapolis Yacht Club ----
    "ayc": "annapolis yacht club",
    "annapolis yc": "annapolis yacht club",
    "annapolis yacht club": "annapolis yacht club",

    # ---- Lauderdale Yacht Club ----
    "lyc": "lauderdale yacht club",
    "lauderdale yc": "lauderdale yacht club",
    "lauderdale yacht club": "lauderdale yacht club",

    # ---- Carolina Yacht Club (SC) ----
    "carolina yc sc": "carolina yacht club sc",
    "carolina yacht club sc": "carolina yacht club sc",

    # ---- St. Petersburg Yacht Club ----
    "spyc": "st petersburg yacht club",
    "st petersburg yc": "st petersburg yacht club",
    "st petersburg yacht club": "st petersburg yacht club",

    # ---- Southern Yacht Club ----
    "southern yc": "southern yacht club",
    "southern yacht club": "southern yacht club",

    # ---- Coconut Grove Sailing Club ----
    "cgsc": "coconut grove sailing club",
    "coconut grove sc": "coconut grove sailing club",
    "coconut grove sailing club": "coconut grove sailing club",

    # ---- Lakewood Yacht Club ----
    "lakewood": "lakewood yacht club",
    "lakewood yc": "lakewood yacht club",
    "lakewood yacht club": "lakewood yacht club",

    # ---- Bellport Bay Yacht Club ----
    "bellport bay": "bellport bay yacht club",
    "bellport bay y": "bellport bay yacht club",
    "bellport bay yc": "bellport bay yacht club",
    "bellport bay yacht club": "bellport bay yacht club",

    # ---- Brant Beach Yacht Club ----
    "bbyc": "brant beach yacht club",
    "brant beach yc": "brant beach yacht club",
    "brant beach yacht club": "brant beach yacht club",

    # ---- Pensacola Yacht Club ----
    "pensacola yc": "pensacola yacht club",
    "pensacola yacht club": "pensacola yacht club",

    # ---- Rush Creek Yacht Club ----
    "rcyc": "rush creek yacht club",
    "rush creek yacht club": "rush creek yacht club",

    # ---- Lake Geneva Yacht Club ----
    "lgyc": "lake geneva yacht club",
    "lake geneva": "lake geneva yacht club",
    "lake geneva yc": "lake geneva yacht club",
    "lake geneva yacht club": "lake geneva yacht club",

    # ---- Noroton Yacht Club ----
    "noroton": "noroton yacht club",
    "noroton yc": "noroton yacht club",
    "noroton yacht club": "noroton yacht club",

    # ---- Encinal Yacht Club ----
    "encinal yc": "encinal yacht club",
    "encinal yacht club": "encinal yacht club",

    # ---- Hyannis Yacht Club ----
    "hyannis yc": "hyannis yacht club",
    "hyannis yacht club": "hyannis yacht club",

    # ---- San Francisco Yacht Club ----
    "sfyc": "san francisco yacht club",
    "san francisco yc": "san francisco yacht club",
    "san francisco yacht club": "san francisco yacht club",

    # ---- Saunderstown Yacht Club ----
    "saunderstown yc": "saunderstown yacht club",
    "saunderstown yacht club": "saunderstown yacht club",

    # ---- Shelter Island Yacht Club ----
    "siyc": "shelter island yacht club",
    "shelter island yc": "shelter island yacht club",
    "shelter island yacht club": "shelter island yacht club",

    # ---- Hampton Yacht Club ----
    "hampton": "hampton yacht club",
    "hampton yc": "hampton yacht club",
    "hampton yacht club": "hampton yacht club",

    # ---- Chicago Yacht Club (confirm: 'cyc' is unambiguous in your pool) ----
    "cyc": "chicago yacht club",
    "chicago yacht club": "chicago yacht club",

    # ---- Severn Sailing Association ----
    "ssa": "severn sailing association",
    "severn sailing association": "severn sailing association",

    # ---- Key Biscayne Yacht Club ----
    "kbyc": "key biscayne yacht club",
    "key biscayne yacht club": "key biscayne yacht club",

    # ---- Miami Yacht Club ----
    "myc": "miami yacht club",
    "miami yacht club": "miami yacht club",

    # ---- Palm Beach Sailing Club ----
    "pbsc": "palm beach sailing club",
    "palm beach sc": "palm beach sailing club",
    "palm beach sailing club": "palm beach sailing club",

    # ---- Norfolk Yacht & Country Club ----
    "nycc": "norfolk yacht and country club",
    "norfolk yacht": "norfolk yacht and country club",
    "norfolk yacht cc": "norfolk yacht and country club",
    "norfolk yacht and cc": "norfolk yacht and country club",
    "norfolk yacht country club": "norfolk yacht and country club",
    "norfolk yacht and country club": "norfolk yacht and country club",

    # ---- California Yacht Club ----
    "cal yc": "california yacht club",
    "california": "california yacht club",
    "california yc": "california yacht club",
    "california yacht club": "california yacht club",

    # ---- Toms River Yacht Club ----
    "toms river yc": "toms river yacht club",
    "tryc": "toms river yacht club",
    "toms river yacht club": "toms river yacht club",

    # ---- Riverside Yacht Club ----
    "riverside yc": "riverside yacht club",
    "riverside yacht club": "riverside yacht club",

    # ---- Shrewsbury Sailing & Yacht Club ----
    "ssyc": "shrewsbury sailing and yacht club",
    "shrewsbury sailing yacht club": "shrewsbury sailing and yacht club",
    "shrewsbury sailing and yacht club": "shrewsbury sailing and yacht club",

    # ---- Falmouth Yacht Club ----
    "falmouth": "falmouth yacht club",
    "falmouth yacht club": "falmouth yacht club",

    # ---- Metedeconk River Yacht Club (typo) ----
    "metedeconk rive yacht club": "metedeconk river yacht club",
    "metedeconk river yacht club": "metedeconk river yacht club",

    # ---- Fishing Bay Yacht Club ----
    "fbyc": "fishing bay yacht club",
    "fishing bay yacht club": "fishing bay yacht club",

    # ---- Clearwater Community Sailing Center ----
    "ccsc": "clearwater community sailing center",
    "clearwater csc": "clearwater community sailing center",
    "clearwater community sailing center": "clearwater community sailing center",

    # ---- batch added from flags.log review (real clubs, verified) ----
    "fyc": "falmouth yacht club",
    "falmouth": "falmouth yacht club",
    "falmouth yacht club": "falmouth yacht club",

    "larchmont": "larchmont yacht club",
    "larchmont yc": "larchmont yacht club",
    "larchmont yacht club": "larchmont yacht club",

    "mantoloking": "mantoloking yacht club",
    "mantoloking yc": "mantoloking yacht club",
    "mantoloking yacht club": "mantoloking yacht club",

    "centeport yacht club": "centerport yacht club",   # typo seen in data
    "centerport yc": "centerport yacht club",
    "centerport yacht club": "centerport yacht club",

    "oyc": "orient yacht club",
    "orient yc": "orient yacht club",
    "orient yacht club": "orient yacht club",

    "saint augustine yacht club": "st augustine yacht club",
    "st augustine yacht club": "st augustine yacht club",

    "tred avon ycht club": "tred avon yacht club",      # typo seen in data
    "tred avon yacht club": "tred avon yacht club",

    "portland yc": "portland yacht club",
    "portland yacht club": "portland yacht club",

    "lehyc": "little egg harbor yc",
    "little egg harbor yc": "little egg harbor yc",

    "lavallette": "lavallette yacht club",
    "lavallette yacht club": "lavallette yacht club",

    "hyc": "hampton yacht club",
    "hampton yacht club": "hampton yacht club",

    "tom s river yacht club": "toms river yacht club",  # apostrophe-stripped variant
    "toms river yacht club": "toms river yacht club",

    "sachem s head yacht club": "sachem s head yacht club",
    "shyc": "sachem s head yacht club",
}

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )

# Private travel teams / coaching programs — NOT home clubs. When a sailor's
# club field is one of these (and nothing else resolves to a real club), we
# can't trust club for identity, so we fall back to NAME-ONLY matching.
# Stored in normalized form (lowercase, no punctuation). Add as new ones appear.
TRAVEL_TEAMS = {
    "performance sailing institute",
    "performance sailing",
    "performance sailing miami",
    "performance sailing club",
    "psi",
    "coach pulio sailing",
    "coach pulio sailling",   # seen misspelled in data
    "coach pulio",
    "jk sailing",
    "jk sailing sccyc",
    "jk sailling sccyc",      # misspelling seen in data
    "jk",
    "lisot",
    "cert",
    "loot",
    "team happy",
    "team manos",
    "gold winds team",
    "gold winds",
    "goldwin",
    "bcs",
    "bcs best coast sailing",
    "best coast sailing",
    "narrow race team",
    "narrows race team",
    "team lbi",
    "us one-design",
    "ur sailing",
    "diminich sailing",
}

def normalize_name(first: str, last: str) -> str:
    full = f"{(first or '').strip()} {(last or '').strip()}"
    full = _strip_accents(full).lower()
    full = re.sub(r"[^a-z0-9 ]", "", full)
    full = re.sub(r"\s+", " ", full).strip()
    return full

def _normalize_segment(seg: str) -> str:
    """Normalize one club/team segment to the canonical comparison form."""
    c = _strip_accents(seg).lower()
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    c = re.sub(r"\s+", " ", c).strip()
    c = re.sub(r"\bclub club\b", "club", c)
    return CLUB_ALIASES.get(c, c)

def resolve_club(club: str) -> tuple[str, bool]:
    """
    Resolve a raw club string to (canonical_club, is_travel_team_only).

    Splits the string on '/' and looks for the first segment that is a REAL
    club (not a travel team). If a real club is found, returns it. If EVERY
    segment is a travel team (or empty), returns ('', True) signalling that
    club can't be trusted for identity and name-only matching should be used.
    """
    if not club:
        return "", False
    segments = [_normalize_segment(s) for s in club.split("/")]
    segments = [s for s in segments if s]
    if not segments:
        return "", False
    # find first segment that is a real club (not a travel team)
    for s in segments:
        if s not in TRAVEL_TEAMS:
            return s, False
    # every segment was a travel team -> club is untrustworthy for identity
    return "", True

def normalize_club(club: str) -> str:
    """Back-compat: return the resolved club string (empty if travel-team-only)."""
    canonical, _ = resolve_club(club)
    return canonical

def identity_key(first: str, last: str, club: str) -> str:
    """
    Identity key. Normally name+club. But if the club field is ONLY a travel
    team (no real club anywhere in it), fall back to NAME-ONLY, since the team
    label tells us nothing about which club-keyed identity this is — and the
    same sailor will appear under their real club at other events.
    """
    name = normalize_name(first, last)
    canonical, team_only = resolve_club(club)
    if team_only or not canonical:
        # name-only key — note the trailing '|' is dropped so it can MATCH a
        # name+club key for the same sailor at another event... but that only
        # works if we collapse on name. See build_ranking note.
        return f"{name}|"
    return f"{name}|{canonical}"


# ---------------------------------------------------------------------------
# Parsing one event's Clubspot JSON
# ---------------------------------------------------------------------------

@dataclass
class SailorResult:
    key: str
    first: str
    last: str
    club: str
    sail_number: str
    country: str
    net: float
    sailed_series: bool
    last_race_finish: float  # for tie-breaking; lower is better

@dataclass
class EventScore:
    key: str
    name: str
    finish_position: int
    fleet_size: int
    result_score: float


def parse_clubspot_event(payload: dict) -> list[SailorResult]:
    """Turn the raw `scoresByRegistration` JSON into SailorResult rows."""
    rows: list[SailorResult] = []
    for entry in payload.get("scoresByRegistration", []):
        reg = entry.get("registrationObject", {}) or {}
        first = reg.get("firstName", "") or ""
        last = reg.get("lastName", "") or ""
        club = reg.get("clubName", "") or ""
        sail = str(reg.get("sailNumber", "") or "")
        country = (reg.get("sailNumber_country", "") or "").lower()
        net = entry.get("net")
        scoring = entry.get("scoring_data", []) or []

        # Did they actually sail the series? At least one race with a real
        # finishing position (numeric points and not a DNC-like letter score).
        sailed = False
        last_race_no = -1
        last_race_finish = math.inf
        for s in scoring:
            ls = (s.get("letterScore") or "").upper()
            if ls in DNC_LIKE:
                continue
            # a real sailed race
            sailed = True
            rno = s.get("race_number", -1)
            if rno is not None and rno > last_race_no:
                last_race_no = rno
                last_race_finish = s.get("points", math.inf)

        if net is None:
            # no series result at all -> a pure no-show; skip entirely
            continue

        rows.append(SailorResult(
            key=identity_key(first, last, club),
            first=first, last=last, club=club, sail_number=sail,
            country=country,
            net=float(net), sailed_series=sailed,
            last_race_finish=float(last_race_finish) if last_race_finish != math.inf else 1e9,
        ))
    return rows


def score_event(payload: dict, event_id: str, event_name: str) -> list[EventScore]:
    rows = parse_clubspot_event(payload)

    # fleet_size = sailors who sailed the series
    sailed = [r for r in rows if r.sailed_series]
    fleet_size = len(sailed)
    if fleet_size == 0:
        return []

    # finish_position = rank on net ascending; tie-break by last-race finish
    sailed.sort(key=lambda r: (r.net, r.last_race_finish))

    mult = EVENT_MULTIPLIERS.get(event_id, 1.0)
    dampen = math.sqrt(fleet_size / REFERENCE_FLEET)

    out: list[EventScore] = []
    pos = 0
    prev_key = None
    for i, r in enumerate(sailed, start=1):
        # finish_position is computed against the FULL sailed fleet (a non-US
        # sailor still beat/was beaten by these sailors on the water), but
        # non-US sailors are excluded from the published ranking output.
        finish_position = i
        if USA_ONLY and r.country and r.country != ALLOWED_COUNTRY:
            continue
        pct = (fleet_size - finish_position) / fleet_size
        result_score = (pct ** PLACING_POWER) * 100 * dampen * mult
        out.append(EventScore(
            key=r.key, name=f"{r.first} {r.last}".strip(),
            finish_position=finish_position, fleet_size=fleet_size,
            result_score=round(result_score, 2),
        ))
    return out


# ---------------------------------------------------------------------------
# Cross-event aggregation: best-of-5 mean, qualification floor
# ---------------------------------------------------------------------------

@dataclass
class RankedSailor:
    key: str
    name: str
    clubs_seen: set = field(default_factory=set)
    scores: list = field(default_factory=list)  # (event_id, result_score)

    @property
    def n_events(self) -> int:
        return len(self.scores)

    @property
    def ranking_score(self) -> float:
        best = sorted((s for _, s in self.scores), reverse=True)[:BEST_N]
        return round(sum(best) / len(best), 2) if best else 0.0


def build_ranking(all_event_scores: dict[str, list[EventScore]]) -> list[RankedSailor]:
    """
    all_event_scores: {event_id: [EventScore, ...]}

    Two passes:
      1. Group every result by its exact identity key.
      2. Absorb travel-team name-only keys ("name|") into a real name+club
         sailor of the same name — but ONLY if exactly one real club-keyed
         sailor has that name. If zero or more-than-one match, the name-only
         entry stays separate (we will not guess which of two same-name
         sailors a bare name belongs to — that would risk a false merge).
    """
    sailors: dict[str, RankedSailor] = {}
    for event_id, scores in all_event_scores.items():
        for es in scores:
            rs = sailors.setdefault(es.key, RankedSailor(key=es.key, name=es.name))
            rs.scores.append((event_id, es.result_score))
            rs.name = es.name

    # Pass 0: apply MANUAL_MERGES — fuse keys you've confirmed are one sailor.
    # Builds a map from any listed key -> its canonical (first) key, then folds
    # every non-canonical sailor's scores into the canonical one.
    canonical_of: dict[str, str] = {}
    for group in MANUAL_MERGES:
        if not group:
            continue
        canon = group[0]
        for k in group:
            canonical_of[k] = canon
    for key in list(sailors.keys()):
        canon = canonical_of.get(key)
        if canon and canon != key and key in sailors:
            target = sailors.setdefault(canon, RankedSailor(key=canon, name=sailors[key].name))
            target.scores.extend(sailors[key].scores)
            # keep the canonical key's display name if it already had one
            if canon in sailors and sailors[canon].name:
                pass
            del sailors[key]

    # Pass 2: absorb name-only ("name|") keys into a unique name+club sailor.
    # Build: name -> list of real (name+club) keys with that name.
    def _name_of(key: str) -> str:
        return key.split("|", 1)[0]

    real_keys_by_name: dict[str, list] = {}
    for key in sailors:
        nm = _name_of(key)
        club_part = key.split("|", 1)[1] if "|" in key else ""
        if club_part:  # has a real club
            real_keys_by_name.setdefault(nm, []).append(key)

    merged_away = []
    for key, rs in list(sailors.items()):
        club_part = key.split("|", 1)[1] if "|" in key else ""
        if club_part == "":  # this is a name-only (travel-team) entry
            nm = _name_of(key)
            candidates = real_keys_by_name.get(nm, [])
            if len(candidates) == 1:
                # safe: exactly one real-club sailor with this name -> absorb
                target = sailors[candidates[0]]
                target.scores.extend(rs.scores)
                merged_away.append(key)
            # if 0 candidates: sailor only ever appeared under a travel team;
            #   keep the name-only entry as their identity (still valid).
            # if >1 candidates: ambiguous (two same-name sailors at real clubs)
            #   -> do NOT merge; leave name-only entry separate to avoid a
            #   false merge. It will surface in the duplicate detector.
    for k in merged_away:
        del sailors[k]

    qualified = [s for s in sailors.values() if s.n_events >= QUALIFY_MIN_EVENTS]
    qualified.sort(key=lambda s: s.ranking_score, reverse=True)
    return qualified


def find_duplicate_candidates(all_event_scores: dict[str, list[EventScore]]) -> list[str]:
    """
    Flag likely-split sailors: same normalized NAME but different identity KEY
    (the club differs). Instead of an unsorted list, GROUP by the club-pair
    involved and RANK by how many sailors each pair splits — so fixing the top
    few CLUB_ALIASES entries clears the most duplicates.

    Output is sorted worst-first: the club combinations splitting the most
    sailors appear at the top, with the affected sailor names listed.
    """
    from itertools import combinations

    # name -> {club -> display_name}
    by_name: dict[str, dict] = {}
    for scores in all_event_scores.values():
        for es in scores:
            name_part, _, club_part = es.key.partition("|")
            by_name.setdefault(name_part, {})[club_part] = es.name

    # For each sailor split across multiple clubs, attribute them to every
    # PAIR of clubs they appear under. Tally how many sailors each pair splits.
    pair_to_sailors: dict[tuple, list] = {}
    for name_part, club_map in by_name.items():
        if len(club_map) > 1:
            clubs = sorted(club_map.keys())
            display = next(iter(club_map.values()))
            for a, b in combinations(clubs, 2):
                pair_to_sailors.setdefault((a, b), []).append(display)

    if not pair_to_sailors:
        return []

    # rank club-pairs by number of sailors affected, worst first
    ranked = sorted(pair_to_sailors.items(), key=lambda kv: len(kv[1]), reverse=True)

    lines = []
    total_sailors = sum(1 for n, cm in by_name.items() if len(cm) > 1)
    lines.append(
        f"=== {total_sailors} sailors split across clubs; "
        f"{len(ranked)} club-pairs to review (worst first) ==="
    )
    for (a, b), sailors in ranked:
        uniq = sorted(set(sailors))
        sample = ", ".join(uniq[:6]) + (" ..." if len(uniq) > 6 else "")
        lines.append(
            f"[{len(uniq):>3} sailor(s)] '{a}'  <->  '{b}'   e.g. {sample}"
        )
        lines.append(
            f"            if same club, add to CLUB_ALIASES:  \"{a}\": \"{b}\","
        )
    return lines
