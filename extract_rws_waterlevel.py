"""
t17: fetch REAL measured + astronomical-prediction water level from RWS's
official WaterWebservices API (OphalenWaarnemingen) for a named reference
station, and write them as a static .js data file this app loads directly.

Why static, not a live browser fetch (2026-07-23): this app is otherwise
100% client-side/static (no backend, see Claude.md's Architecture
philosophy) -- but checked directly (curl, both a real POST and an OPTIONS
CORS preflight) and confirmed this RWS API sends NO
Access-Control-Allow-Origin header at all, on either the real response or
the preflight. A browser's fetch() from this app's own origin (a static
site, not waterwebservices.rijkswaterstaat.nl itself) would be blocked by
CORS regardless of anything client-side code does about it -- this isn't
a config bug on our end, it's a hard architectural constraint. Same
resolution already used for tide_predictions_2026_harlingen.js (which hit
the exact same problem with a different RWS endpoint): fetch server-side
here, bake into a static file, re-run this script to refresh. Measured
data is inherently a point-in-time snapshot (can't be "live" in a static
file no matter what); re-running this script is the refresh mechanism,
same as the ENC soundings' own documented weekly-refresh pattern.

API discovered 2026-07-23 (the old waterinfo.rws.nl endpoints referenced
elsewhere in this project's history are a DIFFERENT, now-403-redirecting
API -- this is the current one, found via the redirect target):
  POST https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen
Location codes aren't the SEATALK-style short codes used elsewhere in this
project (e.g. "HARLGN") -- confirmed by querying METADATASERVICES/
OphalenCatalogus directly: Harlingen's actual water-level gauge is
"harlingen.waddenzee" (53.175634, 5.409342 -- matches this project's own
independently-validated Harlingen reference point almost exactly), not
"harlingen.havenmond" (which only has air-pressure/wind instruments, no
water-level gauge -- checked directly, not assumed, before picking the
right code).

Usage: python extract_rws_waterlevel.py
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"

# Extended to all 10 HARBOURS entries (2026-07-23) -- every code below was
# found via METADATASERVICES/OphalenCatalogus's LocatieLijst (nearest real
# station to each HARBOURS lat/lon, same method as Harlingen originally)
# and INDIVIDUALLY CONFIRMED to actually return real WATHTE data with a
# live test query -- several plausible-looking nearby codes per station
# (e.g. "vlieland.jachthaven*", "lauwersoog.havenmond") came back empty
# (204/no data), same "harbour mouth often has no gauge" lesson Harlingen's
# own harlingen.havenmond-vs-waddenzee mixup already taught. Hollum has no
# dedicated water-level station of its own (checked several nearby
# candidates -- borndiep/amelanderzeegat/hollum.strand -- all empty),
# consistent with this project's own existing precedent of approximating
# Hollum with Nes's data for hwOffsetMin/napToLatM (see HARBOURS in
# index.html) -- reuses the same ameland.nes code, not a separate entry.
STATIONS = [
    {"name": "Oost-Vlieland", "var_suffix": "OOST_VLIELAND", "code": "vlieland.haven"},
    {"name": "West-Terschelling", "var_suffix": "WEST_TERSCHELLING", "code": "terschelling.west"},
    {"name": "Harlingen", "var_suffix": "HARLINGEN", "code": "harlingen.waddenzee"},
    {"name": "Kornwerderzand", "var_suffix": "KORNWERDERZAND", "code": "kornwerderzand.waddenzee.buitenhaven"},
    {"name": "Hollum", "var_suffix": "HOLLUM", "code": "ameland.nes"},
    {"name": "Nes", "var_suffix": "NES", "code": "ameland.nes"},
    # holwerd.veersteiger (the nearer-by-coordinate candidate) returns NO
    # data at all, measured or predicted, over the real 4-day window this
    # script actually uses (it had returned a handful of points under a
    # quick 6-hour test query, but that data doesn't persist/repeat over a
    # longer real window -- checked directly, not assumed still valid).
    # holwerd.vaargeul has real, consistent measured data but genuinely no
    # predicted series of its own -- picked anyway since measured-only
    # still beats nothing, same "something sourced beats nothing" standard
    # already applied elsewhere in this file.
    {"name": "Holwerd", "var_suffix": "HOLWERD", "code": "holwerd.vaargeul"},
    {"name": "Lauwersoog", "var_suffix": "LAUWERSOOG", "code": "lauwersoog.waddenzee"},
    {"name": "Schiermonnikoog", "var_suffix": "SCHIERMONNIKOOG", "code": "schiermonnikoog.waddenzee"},
    {"name": "Den Oever", "var_suffix": "DEN_OEVER", "code": "denoever.waddenzee.voorhaven"},
]

# Real (not "live" -- see module docstring) window: measured data only
# exists for the past, so this covers a real recent-past stretch;
# predicted covers the same past window (for direct overlay comparison,
# matching Nautinect's own screenshot) plus enough future for the app's
# Map Time widget to scrub forward a few days before this data goes stale.
PAST_DAYS = 4
FUTURE_DAYS = 10


def _fetch(location_code, proces_type, begin, end):
    body = {
        "Locatie": {"Code": location_code},
        "AquoPlusWaarnemingMetadata": {
            "AquoMetadata": {
                "Compartiment": {"Code": "OW"},
                "Grootheid": {"Code": "WATHTE"},
            }
        },
        "Periode": {
            "Begindatumtijd": begin.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
            "Einddatumtijd": end.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        },
    }
    if proces_type:
        body["AquoPlusWaarnemingMetadata"]["AquoMetadata"]["ProcesType"] = proces_type
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        raw = resp.read()
    if status == 204 or not raw:
        return []
    data = json.loads(raw)
    if not data.get("Succesvol") or not data.get("WaarnemingenLijst"):
        return []
    out = []
    for series in data["WaarnemingenLijst"]:
        for m in series.get("MetingenLijst", []):
            waarde = m.get("Meetwaarde", {}).get("Waarde_Numeriek")
            tijdstip = m.get("Tijdstip")
            if waarde is None or tijdstip is None:
                continue
            ms = int(datetime.fromisoformat(tijdstip).timestamp() * 1000)
            out.append([ms, round(float(waarde), 1)])
    out.sort(key=lambda p: p[0])
    return out


def extract_station(code):
    now = datetime.now(timezone.utc)
    begin = now - timedelta(days=PAST_DAYS)
    end = now + timedelta(days=FUTURE_DAYS)
    measured = _fetch(code, "meting", begin, now)
    predicted = _fetch(code, "verwachting", begin, end)
    print(f"  {code}: {len(measured)} measured points, {len(predicted)} predicted points")
    return measured, predicted


def write_js(out_path, var_prefix, measured, predicted, station_name):
    with open(out_path, "w") as f:
        f.write("// Auto-generated by extract_rws_waterlevel.py -- do not hand-edit.\n")
        f.write(f"// Real RWS WaterWebservices data for {station_name}, NAP-referenced cm --\n")
        f.write("// [ms, cm] pairs, measured (real observed) and predicted (astronomical\n")
        f.write("// forecast). Static snapshot, not live -- see this script's own module\n")
        f.write("// docstring for why (confirmed no CORS support on the source API), and\n")
        f.write("// re-run this script to refresh. Convert to LAT via this app's own\n")
        f.write("// napToLatM offset for the same station, same as every other water-\n")
        f.write("// level source in this app.\n")
        f.write(f"var {var_prefix}_MEASURED = ")
        json.dump(measured, f, separators=(",", ":"))
        f.write(";\n")
        f.write(f"var {var_prefix}_PREDICTED = ")
        json.dump(predicted, f, separators=(",", ":"))
        f.write(";\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    for station in STATIONS:
        print(f"fetching {station['name']} ({station['code']})...")
        measured, predicted = extract_station(station["code"])
        # Filename slug from var_suffix, not the raw station name (2026-07-23
        # fix) -- "Den Oever".lower() left a literal space in the filename
        # ("rws_waterlevel_den oever.js"), unsafe for a plain <script src>
        # tag; var_suffix is already a safe ASCII identifier.
        write_js(
            f"rws_waterlevel_{station['var_suffix'].lower()}.js",
            f"RWS_WATERLEVEL_{station['var_suffix']}",
            measured, predicted, station["name"],
        )
