"""
t17: fetch REAL measured water level from PEGELONLINE (WSV -- Wasserstrassen-
und Schifffahrtsverwaltung des Bundes) for Germany's named HARBOURS stations,
and write them as a static .js data file this app loads directly -- the
German equivalent of extract_rws_waterlevel.py.

Source found by the user (2026-07-24): https://pegelonline.wsv.de/webservice/dokuRestapi
Real base path confirmed directly (the doc page's own summary had a typo,
"webservice" singular -- the real API is under "webservices" plural, checked
by hitting both and comparing response codes):
  https://pegelonline.wsv.de/webservices/rest-api/v2/

Checked before trusting it (this project's own standing practice):
- CORS: DOES support it (Access-Control-Allow-Origin: * on every response) --
  a genuine difference from RWS's WaterWebservices API (confirmed CORS-less,
  see extract_rws_waterlevel.py's own docstring). A live client-side fetch()
  would technically work here. Kept the same static-snapshot-file pattern
  anyway, for consistency with every other water-level/tide-prediction
  source in this app (all pre-baked, refreshed by re-running the extractor)
  and to keep this app's "100% static, no live external dependency at page-
  load time" architecture stance intact (see Claude.md).
- Forecast/prediction timeseries ("WV", confirmed via /stations.json?
  hasTimeseries=WV): exists for 43 stations nationwide, but EVERY one is an
  inland river gauge (Elbe, Rhine, Danube, Oder, Saale) -- these are
  hydrological (rainfall/snowmelt/discharge) flood forecasts, not
  astronomical tidal predictions, and none of our coastal stations have one.
  So this script only ever writes a MEASURED series, never a predicted one
  -- same "measured-only, empty predicted array" shape this project already
  uses for NL's own Holwerd station (no equivalent forecast there either),
  which _rwsSeriesFor()-style code on the JS side already handles gracefully.
- Vertical datum: each station publishes its own real gauge-zero-to-NHN
  offset (?includeTimeseries=true -> timeseries[].gaugeZero.value, e.g.
  Wilhelmshaven: -5.04 m NHN) -- this script converts raw cm-above-gauge-
  zero to cm-above-NHN using that real offset, matching the convention
  TrilaWatt's own wlGrid/sea_surface_height_2d already uses (NHN), the same
  way RWS's own raw values are already NAP-referenced. Height-above-LAT-
  equivalent conversion (this app's existing napToLatM offset) still applies
  the same way at draw time -- German HARBOURS entries currently have
  napToLatM: 0 (an explicit "not yet researched" placeholder, see
  Claude.md), so the curve will show raw NHN-referenced height until a real
  NHN-to-SKN (German chart datum) offset is found -- a disclosed, not
  silently wrong, limitation, consistent with every other German-side gap
  already documented.

Station selection (found directly via /stations.json, not guessed): for each
HARBOURS entry, the real PEGELONLINE station closest to its existing
lat/lon, confirmed to actually have a "W" (WASSERSTAND ROHDATEN) timeseries.
Borkum has two real stations (Fischerbalje, Suedstrand) -- Suedstrand is
the closer match; same for Wilhelmshaven (Alter/Neuer Vorhafen -- Alter is
closer). List auf Sylt added as a 5th German harbour (not in HARBOURS
before this) per the user's own test of that station.

Usage: python extract_pegelonline_waterlevel.py
"""
import json
import urllib.request
from datetime import datetime, timezone

API = "https://pegelonline.wsv.de/webservices/rest-api/v2"

# gauge_zero_nhn_m: real value from /stations/{uuid}.json?includeTimeseries=true,
# timeseries[].gaugeZero.value (unit "m. ue. NHN"), checked directly for
# each station below, not guessed.
STATIONS = [
    {"name": "Borkum", "var_suffix": "BORKUM", "uuid": "478f21e9-906b-4c6f-a009-b5eabb052746",
     "station_name": "BORKUM SUEDSTRAND", "gauge_zero_nhn_m": -5.02},
    {"name": "Norderney", "var_suffix": "NORDERNEY", "uuid": "c0244c0e-6ae6-40cb-a967-4039b2a0ce7c",
     "station_name": "NORDERNEY RIFFGAT", "gauge_zero_nhn_m": -5.00},
    {"name": "Wilhelmshaven", "var_suffix": "WILHELMSHAVEN", "uuid": "f85bd17b-06c7-49bd-8bfc-ee2bf3ffea99",
     "station_name": "WHV ALTER VORHAFEN", "gauge_zero_nhn_m": -5.04},
    {"name": "Cuxhaven", "var_suffix": "CUXHAVEN", "uuid": "aad49293-242a-43ad-a8b1-e91d7792c4b2",
     "station_name": "CUXHAVEN STEUBENHOEFT", "gauge_zero_nhn_m": -5.033},
    {"name": "List auf Sylt", "var_suffix": "LIST_AUF_SYLT", "uuid": "5e92d73f-e4ea-42c1-9f98-91536c17cdff",
     "station_name": "LIST AUF SYLT", "gauge_zero_nhn_m": -4.994},
    # 12 of the 17 new harbours added 2026-07-25 (see extract_bsh_gezeiten.py)
    # have a real nearby PEGELONLINE gauge with a "W" (measured water level)
    # timeseries -- found via /stations.json?includeTimeseries=true, matched
    # by coordinate the same way as the original 5. The other 5 (Juist,
    # Baltrum, Norddeich, Bensersiel, Wyk auf Föhr) have NO PEGELONLINE
    # station at all (WSV's own federal-waterway network is narrower than
    # BSH's 172-gauge one) -- checked directly, not assumed; those get real
    # BSH-predicted-only tide curves instead (see index.html's _rwsSeriesFor).
    {"name": "Emden", "var_suffix": "EMDEN", "uuid": "edfdf747-be92-462f-87ed-53d228a33172",
     "station_name": "EMDEN NEUE SEESCHLEUSE", "gauge_zero_nhn_m": -5.01},
    {"name": "Langeoog", "var_suffix": "LANGEOOG", "uuid": "a0c1dcb6-7812-48e6-8c01-f7edad7a2caf",
     "station_name": "LANGEOOG", "gauge_zero_nhn_m": -5.04},
    {"name": "Spiekeroog", "var_suffix": "SPIEKEROOG", "uuid": "662c4b5e-0241-456d-ac7d-9f62fd95c0d1",
     "station_name": "SPIEKEROOG", "gauge_zero_nhn_m": -5.08},
    {"name": "Wangerooge", "var_suffix": "WANGEROOGE", "uuid": "70039212-c8a8-43fc-82a5-150d95831772",
     "station_name": "WANGEROOGE WEST", "gauge_zero_nhn_m": -5.05},
    # Hooksiel/Juist/Norddeich/Baltrum/Bensersiel REMOVED 2026-07-23 (user
    # request) -- see extract_bsh_gezeiten.py's own comment; each was missing
    # its real BSH predicted curve, its real PEGELONLINE measured curve, or
    # both, making their tidal-curve display awkward.
    {"name": "Bremerhaven", "var_suffix": "BREMERHAVEN", "uuid": "d3f822a0-e201-4a61-8913-589c74818ae0",
     "station_name": "BHV ALTER LEUCHTTURM", "gauge_zero_nhn_m": -5.00},
    {"name": "Helgoland", "var_suffix": "HELGOLAND", "uuid": "c0ec139b-13b4-4f86-bee3-06665ad81a40",
     "station_name": "HELGOLAND BINNENHAFEN", "gauge_zero_nhn_m": -5.015},
    {"name": "Büsum", "var_suffix": "BUESUM", "uuid": "5287a3e1-c540-4ab1-b52e-880d124cbc43",
     "station_name": "BÜSUM", "gauge_zero_nhn_m": -5.019},
    {"name": "Husum", "var_suffix": "HUSUM", "uuid": "e114aeec-c8d9-4d20-8fe1-8822058cb38b",
     "station_name": "HUSUM", "gauge_zero_nhn_m": -5.004},
    {"name": "Wittdün (Amrum)", "var_suffix": "WITTDUEN", "uuid": "9c4c11f2-0548-4555-beac-ecfd36f9bd74",
     "station_name": "WITTDÜN", "gauge_zero_nhn_m": -5.025},
    {"name": "Dagebüll", "var_suffix": "DAGEBUELL", "uuid": "6233e901-2600-4b54-ae06-7b987934e99e",
     "station_name": "DAGEBÜLL", "gauge_zero_nhn_m": -5.005},
    {"name": "Hörnum (Sylt)", "var_suffix": "HOERNUM", "uuid": "733755fd-628f-4130-a694-aaba340531ba",
     "station_name": "HÖRNUM", "gauge_zero_nhn_m": -4.997},
]

# PEGELONLINE's own "last N days" window param (ISO 8601 duration) -- no
# future/forecast window needed (see module docstring: no real prediction
# series exists for any of these stations).
PAST_WINDOW = "P4D"


def fetch_measured(uuid, gauge_zero_nhn_m):
    url = f"{API}/stations/{uuid}/W/measurements.json?start={PAST_WINDOW}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    data = json.loads(raw)
    out = []
    for p in data:
        ms = int(datetime.fromisoformat(p["timestamp"]).timestamp() * 1000)
        # raw value is cm above this station's own gauge zero; convert to
        # cm above NHN (matching wlGrid's own reference) using the real
        # gaugeZero offset -- NOT left as raw gauge-relative cm, which
        # would not be comparable between stations or against TrilaWatt's
        # own model output at all.
        cm_above_nhn = round(float(p["value"]) + gauge_zero_nhn_m * 100, 1)
        out.append([ms, cm_above_nhn])
    out.sort(key=lambda pt: pt[0])
    return out


def write_js(out_path, var_prefix, measured, station_name):
    # encoding="utf-8" explicit -- see extract_bsh_gezeiten.py's own write
    # function for why (umlaut station names, Windows default locale).
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by extract_pegelonline_waterlevel.py -- do not hand-edit.\n")
        f.write(f"// Real PEGELONLINE (WSV) measured water level for {station_name},\n")
        f.write("// NHN-referenced cm -- [ms, cm] pairs (converted from the station's own\n")
        f.write("// raw gauge-zero-relative cm using its real, published gaugeZero offset --\n")
        f.write("// see this script's own module docstring). No predicted/forecast series --\n")
        f.write("// PEGELONLINE has none for coastal tide stations (checked directly, only\n")
        f.write("// inland river gauges publish one) -- _PREDICTED is always empty, same\n")
        f.write("// 'measured-only' shape this project's own Holwerd (NL) station already\n")
        f.write("// uses. Static snapshot, not live (this API DOES support CORS, unlike\n")
        f.write("// RWS's, but kept static for architectural consistency -- see docstring).\n")
        f.write("// Re-run this script to refresh. Convert to LAT-equivalent via this app's\n")
        f.write("// own napToLatM offset for the same station once a real NHN-to-SKN value\n")
        f.write("// is researched (currently 0 -- an explicit placeholder, see Claude.md).\n")
        f.write(f"var {var_prefix}_MEASURED = ")
        json.dump(measured, f, separators=(",", ":"))
        f.write(";\n")
        f.write(f"var {var_prefix}_PREDICTED = [];\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    for station in STATIONS:
        print(f"fetching {station['name']} ({station['uuid']})...")
        measured = fetch_measured(station["uuid"], station["gauge_zero_nhn_m"])
        print(f"  {len(measured)} measured points")
        write_js(
            f"pegelonline_waterlevel_{station['var_suffix'].lower()}.js",
            f"PEGELONLINE_WATERLEVEL_{station['var_suffix']}",
            measured, station["name"],
        )
