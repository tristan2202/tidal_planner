"""
t17: fetch REAL astronomical HW/LW tide predictions AND the real NHN-to-SKN
(German chart datum) vertical offset for Germany's named HARBOURS stations,
from BSH's own gezeiten.bsh.de data API -- the German equivalent of
extract_astro_getij.py, found by the user (2026-07-24) after asking whether
gezeiten.bsh.de could help (a source this project's own research had
previously flagged as "the most promising candidate, not yet found a clean
API behind it").

API found by reading the site's own JS bundles directly (not guessed):
  https://gezeiten.bsh.de/gezeiten/pegel.js
  https://gezeiten.bsh.de/gezeiten/common.js
common.js's own station-model class builds its data URL as
`${this.baseUrl}/DE_${this.bshnr.padStart(5,"_")}_tides.json` with
baseUrl="/data" -- i.e. https://gezeiten.bsh.de/data/DE_<bshnr, left-padded
to 5 chars with "_">_tides.json. The station list itself (bshnr, seo_id,
coordinates, pegelonline_uuid) lives at
https://gezeiten.bsh.de/data/tides_overview.json (also found the same way,
via a "/data/tides_overview.json" string literal in common.js).

Verified directly before trusting any of this (this project's own standing
practice):
- Real, complete, TWO full years of data per station (2026 AND 2027) --
  confirmed via len(data['years']), each with ~1,410 real HW/LW events
  (matches a semidiurnal tide's expected ~706 cycles/year x 2 events).
- pegelonline_uuid field cross-references the EXACT SAME station UUIDs this
  project already uses for extract_pegelonline_waterlevel.py -- confirms
  these are the same real physical gauges, not a coincidental name match.
- PNP (unter NHN) cross-validated against PEGELONLINE's own gaugeZero for
  the same station (e.g. Wilhelmshaven: both give -5.04 m) -- independent
  agreement between two different official sources, not just one claim.
- SKN (unter NHN) is the real, government-published NHN-to-SKN (Seekartennull,
  Germany's chart datum) offset this project has been carrying as an
  explicit "napToLatM: 0, not yet researched" placeholder on every German
  HARBOURS entry (see index.html/Claude.md) -- this finally replaces it with
  real data, same role NAP-to-LAT's own napToLatM plays for NL (LAT = NAP +
  napToLatM; here, SKN = NHN + this offset). Printed to stdout for manual
  entry into HARBOURS (five static numbers -- not worth a runtime data file
  for this).

Height convention: hwnw_prediction heights are PNP-referenced (per each
year's own "level": "PNP" field) -- converted to NHN-referenced cm here
(height_nhn = height_pnp + PNP_unter_NHN_cm), matching wlGrid's own NHN
convention and this project's existing PEGELONLINE-measured-data convention
(same reasoning as extract_pegelonline_waterlevel.py's own conversion).

Usage: python extract_bsh_gezeiten.py
"""
import json
import urllib.request

API = "https://gezeiten.bsh.de/data"

# bshnr found directly via https://gezeiten.bsh.de/data/tides_overview.json
# (172 real gauges listed) -- matched to this project's existing German
# HARBOURS entries by coordinate, same as every other station-matching
# decision in this project. Station names/coords cross-checked and confirmed
# to match PEGELONLINE's own station list exactly (see
# extract_pegelonline_waterlevel.py's STATIONS).
STATIONS = [
    {"name": "Borkum", "var_suffix": "BORKUM", "bshnr": "798P"},
    {"name": "Norderney", "var_suffix": "NORDERNEY", "bshnr": "111P"},
    {"name": "Wilhelmshaven", "var_suffix": "WILHELMSHAVEN", "bshnr": "512P"},
    {"name": "Cuxhaven", "var_suffix": "CUXHAVEN", "bshnr": "506P"},
    # "List, Sylt, Hafen" (55.01667, 8.44056) -- matches this project's own
    # List-auf-Sylt HARBOURS coordinate (55.0165, 8.4404) almost exactly;
    # "List, Sylt, West" (bshnr 616P, 55.05417, 8.4) is a different, real
    # but less-well-matched station at the same island.
    {"name": "List auf Sylt", "var_suffix": "LIST_AUF_SYLT", "bshnr": "617P"},
    # 17 more German Wadden Sea harbours added 2026-07-25 (extending the
    # harbour list beyond the original 5 -- see Claude.md), all real,
    # well-known cruising destinations picked from tides_overview.json's
    # full 172-gauge list, not every gauge in it (most of the other ~150
    # are lighthouses/river gauges/sluice-approach markers, not places a
    # boat would actually call at). East Frisian islands + mainland
    # harbours (west to east):
    # Juist/Norddeich/Baltrum/Bensersiel/Hooksiel REMOVED 2026-07-23 (user
    # request) -- each was missing its real BSH predicted curve, its real
    # PEGELONLINE measured curve (see extract_pegelonline_waterlevel.py), or
    # both, making their tidal-curve display awkward. Only harbours with
    # both real curves present are kept in HARBOURS/this list now.
    {"name": "Emden", "var_suffix": "EMDEN", "bshnr": "507P"},
    {"name": "Langeoog", "var_suffix": "LANGEOOG", "bshnr": "781P"},
    {"name": "Spiekeroog", "var_suffix": "SPIEKEROOG", "bshnr": "779P"},
    {"name": "Wangerooge", "var_suffix": "WANGEROOGE", "bshnr": "777P"},
    {"name": "Bremerhaven", "var_suffix": "BREMERHAVEN", "bshnr": "103P"},
    {"name": "Helgoland", "var_suffix": "HELGOLAND", "bshnr": "509A"},
    # North Frisian islands + mainland harbours (west to east/north):
    {"name": "Büsum", "var_suffix": "BUESUM", "bshnr": "505P"},
    {"name": "Husum", "var_suffix": "HUSUM", "bshnr": "510P"},
    {"name": "Wyk auf Föhr", "var_suffix": "WYK_AUF_FOEHR", "bshnr": "632P"},
    {"name": "Wittdün (Amrum)", "var_suffix": "WITTDUEN", "bshnr": "631P"},
    {"name": "Dagebüll", "var_suffix": "DAGEBUELL", "bshnr": "635P"},
    {"name": "Hörnum (Sylt)", "var_suffix": "HOERNUM", "bshnr": "624P"},
]


def fetch_station(bshnr):
    url = f"{API}/DE_{bshnr.rjust(5, '_')}_tides.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def extract_events(data):
    """Real HW/LW events across every year the API publishes (2026+2027 at
    the time this was written -- checked directly, not assumed to always be
    exactly these two), converted from PNP-referenced to NHN-referenced cm.
    Returns (events, skn_under_nhn_cm) -- the latter taken from whichever
    year entry has it (same value every year, it's a fixed station
    property, not something that changes annually)."""
    events = []
    skn_under_nhn_cm = None
    pnp_under_nhn_cm = None
    for year_entry in data["years"]:
        for year_str, year_data in year_entry.items():
            pnp = year_data["PNP (unter NHN)"]
            if skn_under_nhn_cm is None:
                skn_under_nhn_cm = year_data["SKN (unter NHN)"]
                pnp_under_nhn_cm = pnp
            for ev in year_data["hwnw_prediction"]["data"]:
                # Real gap in BSH's own published data at some stations
                # (found 2026-07-25, Juist): height is null for a handful of
                # events. Skip rather than crash -- the same "genuine event
                # dropped" case _cosineInterpEvents() (index.html) already
                # falls back to plain-linear across, just discovered at the
                # source-data layer instead of the parsing layer this time.
                if ev["height"] is None:
                    continue
                ms = int(_parse_ts(ev["timestamp"]))
                height_nhn = round(ev["height"] + pnp, 1)
                typ = "HW" if ev["type"] == "HW" else "LW"
                events.append([ms, typ, height_nhn])
    events.sort(key=lambda e: e[0])
    return events, skn_under_nhn_cm, pnp_under_nhn_cm


def _parse_ts(ts):
    from datetime import datetime
    # "2026-01-01 03:48:00+01:00" -- space instead of "T", otherwise ISO.
    return datetime.fromisoformat(ts.replace(" ", "T")).timestamp() * 1000


def write_predictions_js(out_path, var_name, events, station_name):
    # encoding="utf-8" explicit -- station names with umlauts (Büsum, Wyk
    # auf Föhr, ...) would otherwise be written in this machine's default
    # locale encoding (cp1252 on Windows), mismatching the utf-8 charset
    # index.html serves as (cosmetic mojibake in the comment only, doesn't
    # break JS execution, but still real -- found while writing
    # compute_hw_offsets.py's own reader, 2026-07-25).
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by extract_bsh_gezeiten.py -- do not hand-edit.\n")
        f.write(f"// Real BSH astronomical HW/LW predictions for {station_name},\n")
        f.write("// NHN-referenced cm (converted from the source's own PNP reference --\n")
        f.write("// see this script's own module docstring), [ms, \"HW\"|\"LW\", heightCm]\n")
        f.write("// tuples. Real 2026+2027 data (not a rolling window) -- re-run to refresh\n")
        f.write("// once BSH publishes a later year.\n")
        f.write(f"var {var_name} = ")
        json.dump(events, f, separators=(",", ":"))
        f.write(";\n")
    print(f"wrote {out_path} ({len(events)} events)")


if __name__ == "__main__":
    print("Real NHN-to-SKN offsets found (paste into HARBOURS' napToLatM field manually --")
    print("see this script's own module docstring for why this isn't a runtime data file):")
    for station in STATIONS:
        print(f"fetching {station['name']} (bshnr {station['bshnr']})...")
        data = fetch_station(station["bshnr"])
        events, skn_cm, pnp_cm = extract_events(data)
        print(f"  {len(events)} real HW/LW events across {len(data['years'])} year(s)")
        print(f"  PNP (unter NHN): {pnp_cm} cm, SKN (unter NHN): {skn_cm} cm "
              f"-> napToLatM = {skn_cm / 100:.2f}")
        write_predictions_js(
            f"tide_predictions_{station['var_suffix'].lower()}.js",
            f"TIDE_PREDICTIONS_{station['var_suffix']}",
            events, station["name"],
        )
