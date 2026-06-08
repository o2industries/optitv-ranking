"""
Scraper layer for the OPTI TV ranking.

Two source platforms:
  - clubspot:        results.theclubspot.com/clubspot-results-v4/{regatta_id}?boatClassIDs={class_id}
                     returns clean JSON with scoresByRegistration[]  (the easy case)
  - regatta_network: regattanetwork.com/clubmgmt/applet_regatta_results.php?regatta_id={id}
                     returns server-rendered HTML tables (parsed with BeautifulSoup)

Output of fetch_event() is ALWAYS the same shape the scoring engine expects:
a dict with key "scoresByRegistration": [ {registrationObject:{...}, net, scoring_data:[...]} ]
so scoring.py doesn't care which platform the data came from.

NOTE: This needs network access to run. It will not execute in an offline
sandbox — run it in your GitHub Actions environment (or any machine online).
"""

from __future__ import annotations
import sys
import time
import requests

CLUBSPOT_URL = "https://results.theclubspot.com/clubspot-results-v4/{regatta_id}"
RN_URL = "https://www.regattanetwork.com/clubmgmt/applet_regatta_results.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (OPTITV-ranking-bot; contact: optitv)",
    "Accept": "application/json, text/html",
}

REQUEST_TIMEOUT = 20
RETRIES = 3
RETRY_BACKOFF = 2.0


def _get(url: str, params: dict | None = None) -> requests.Response:
    last_err = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_err = e
            time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(f"failed after {RETRIES} attempts: {url} :: {last_err}")


# ---------------------------------------------------------------------------
# Clubspot
# ---------------------------------------------------------------------------

def fetch_clubspot(regatta_id: str, class_id: str) -> dict:
    if not regatta_id or not class_id:
        raise ValueError("clubspot event missing regatta_id or class_id")
    url = CLUBSPOT_URL.format(regatta_id=regatta_id)
    r = _get(url, params={"boatClassIDs": class_id})
    data = r.json()
    # The endpoint returns the structure we already validated. Pass through the
    # scoresByRegistration array; if Clubspot nests it, dig for it defensively.
    if "scoresByRegistration" in data:
        return {"scoresByRegistration": data["scoresByRegistration"]}
    # some Clubspot payloads wrap results; search one level down
    for v in data.values():
        if isinstance(v, dict) and "scoresByRegistration" in v:
            return {"scoresByRegistration": v["scoresByRegistration"]}
    raise RuntimeError(f"clubspot payload had no scoresByRegistration (regatta {regatta_id})")


# ---------------------------------------------------------------------------
# Regatta Network
# ---------------------------------------------------------------------------

def fetch_regatta_network(regatta_id: str, fleet_filter: str | None = None) -> dict:
    """
    RN serves an HTML results table. We parse rows into the common shape.
    fleet_filter: if the event has multiple fleets, only rows under a heading/section
    matching this string (case-insensitive substring) are kept.

    RN tables vary by event; this parser targets the standard applet layout
    (Pos | Sail | Skipper | Yacht Club | ... | Total | Net). It is intentionally
    defensive and will raise if it can't find a recognizable results table, so a
    layout change surfaces loudly rather than silently producing garbage.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError("regatta_network parsing needs beautifulsoup4 (pip install beautifulsoup4)")

    r = _get(RN_URL, params={"regatta_id": regatta_id})
    soup = BeautifulSoup(r.text, "html.parser")

    rows_out = []
    current_fleet = None

    # RN typically lays results in <table> rows; fleet names appear as header rows.
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        joined = " ".join(cells).lower()

        # detect a fleet/division header row
        if len(cells) <= 2 and ("fleet" in joined or "division" in joined or "championship" in joined):
            current_fleet = joined
            continue

        if fleet_filter and current_fleet is not None:
            if fleet_filter.lower() not in current_fleet:
                continue

        # a data row: needs a leading integer position and a numeric-looking net
        if not cells[0].isdigit():
            continue
        try:
            pos = int(cells[0])
        except ValueError:
            continue

        # heuristic column grab — RN standard layout
        sail = cells[1] if len(cells) > 1 else ""
        skipper = cells[2] if len(cells) > 2 else ""
        club = cells[3] if len(cells) > 3 else ""
        net = None
        # net is typically the last numeric cell
        for c in reversed(cells):
            try:
                net = float(c.replace(",", ""))
                break
            except ValueError:
                continue
        if net is None:
            continue

        first, _, last = skipper.partition(" ")
        rows_out.append({
            "registrationObject": {
                "firstName": first, "lastName": last,
                "clubName": club, "sailNumber": sail,
            },
            "net": net,
            # RN gives final standings only -> we synthesize a single sailed-race
            # marker so the scorer counts them as a series participant.
            "scoring_data": [{"race_number": 1, "points": float(pos), "letterScore": None}],
        })

    if not rows_out:
        raise RuntimeError(
            f"regatta_network parse found no result rows (regatta {regatta_id}); "
            f"layout may have changed or fleet_filter '{fleet_filter}' matched nothing"
        )
    return {"scoresByRegistration": rows_out}


# ---------------------------------------------------------------------------
# Manual entry (CSV)  — for events that can't be scraped (e.g. robots-blocked)
# ---------------------------------------------------------------------------

def fetch_manual(csv_path: str) -> dict:
    """
    Read a hand-entered results CSV and convert to the common shape.

    Expected CSV columns (header row required, case-insensitive):
      finish_position, first_name, last_name, club, sail_number, country

    - finish_position: integer final standing (1 = winner). This becomes both
      the rank and a synthetic 'net' so the scorer orders sailors correctly.
    - country: optional; 'usa' (or blank) keeps them; anything else is filtered
      out by the USA-only rule downstream. Leave blank for US sailors.
    - sail_number: optional, used only as an identity tiebreaker.

    Every listed sailor is treated as a finisher (they have a real position),
    so fleet_size = number of rows. Only enter sailors who actually sailed.
    """
    import csv as _csv
    import os

    if not csv_path or not os.path.exists(csv_path):
        raise FileNotFoundError(f"manual results file not found: {csv_path}")

    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        # normalize headers to lowercase/underscore
        reader.fieldnames = [(_h or "").strip().lower().replace(" ", "_") for _h in (reader.fieldnames or [])]
        for r in reader:
            r = { (k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in r.items() }
            pos_raw = r.get("finish_position", "")
            if not pos_raw:
                continue
            try:
                pos = int(float(pos_raw))
            except ValueError:
                continue
            rows.append({
                "registrationObject": {
                    "firstName": r.get("first_name", ""),
                    "lastName": r.get("last_name", ""),
                    "clubName": r.get("club", ""),
                    "sailNumber": r.get("sail_number", ""),
                    "sailNumber_country": r.get("country", "") or "usa",
                },
                # synthetic net = finish position, so net-ascending ranking
                # reproduces the entered order exactly.
                "net": float(pos),
                "scoring_data": [{"race_number": 1, "points": float(pos), "letterScore": None}],
            })

    if not rows:
        raise RuntimeError(f"manual CSV {csv_path} produced no usable rows (check headers/columns)")
    return {"scoresByRegistration": rows}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def fetch_event(ev: dict) -> dict:
    platform = ev.get("platform")
    if platform == "clubspot":
        return fetch_clubspot(ev["regatta_id"], ev["class_id"])
    if platform == "regatta_network":
        return fetch_regatta_network(ev["regatta_id"], ev.get("fleet_filter"))
    if platform == "manual":
        return fetch_manual(ev.get("results_file", ""))
    raise ValueError(f"unknown platform '{platform}' for event {ev.get('id')}")


if __name__ == "__main__":
    # quick manual test: python scraper.py clubspot anqiZ8G2n7 qWhwytMjzH
    if len(sys.argv) >= 2 and sys.argv[1] == "clubspot":
        out = fetch_clubspot(sys.argv[2], sys.argv[3])
        print(f"fetched {len(out['scoresByRegistration'])} registrations")
    elif len(sys.argv) >= 2 and sys.argv[1] == "rn":
        out = fetch_regatta_network(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print(f"fetched {len(out['scoresByRegistration'])} rows")
