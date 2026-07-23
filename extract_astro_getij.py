"""
t17: fetch REAL astronomical HW/LW tide predictions for all HARBOURS
stations from RWS's public waterinfo.rws.nl chart API, and write them as
static [ms, "HW"|"LW", heightCm] .js files -- same format and NAP-
referenced convention as the original tide_predictions_2026_harlingen.js,
which this replaces (see Claude.md for why: that file was built from an
undocumented/now-404ing PDF endpoint via a script never saved to this
repo; this one is reproducible and already confirmed to generalize).

Endpoint found by reading Nautinect's own saved client code
(reference_nautinect/app.js) directly, 2026-07-23: their `fetchWaterinfo()`
calls `mapType=astronomische-getij` with `values=-48,336` and
`getijReference=NAP` through their own backend proxy. Reverse-engineered
the real underlying RWS endpoint from that (their proxy forwards to it):

  GET https://waterinfo.rws.nl/api/chart/get
      ?mapType=astronomische-getij&locationCodes=<code>
      &values=-48,336&getijReference=NAP

This is a DIFFERENT, currently-working API from the one an earlier session
tried and got HTTP 500 from every time (waterinfo.rws.nl/api/chart/get
with different params) -- confirmed this one returns real 200s.

Real constraint, checked directly rather than assumed unlimited: `values`
is a fixed preset whitelist, not a free-form range -- `-48,336` (2 days
back, 14 days forward, 10-min resolution) and `-48,48` both work; every
other combination tried (`-24,336`, `-48,168`, `0,4320`, `-336,336`, ...)
returned 404. So this gives a rolling ~16-day window, not a full year --
re-run this script periodically (weekly, matching the ENC soundings'
own established refresh cadence) to keep it current, rather than treating
it as a one-time full-year snapshot the way the old PDF pipeline did.

Because it's a pure astronomical CALCULATION (no real-world sensor gaps),
local-extrema detection on it is far cleaner than the old PDF-parsed
dataset's real, documented 18.3% same-type-pair gap rate -- verified
directly on the actual output below, not assumed.

Station codes confirmed via METADATASERVICES/OphalenCatalogus (see
extract_rws_waterlevel.py) and independently cross-confirmed by
Nautinect's own STATIONS table using the identical codes -- including
their own comment that Holwerd has no forecast/astronomical data at all,
exactly matching what this project found independently. Holwerd is
excluded here for that reason (real measured water level only, already
covered by extract_rws_waterlevel.py).

Usage: python extract_astro_getij.py
"""
import json
import urllib.request
import urllib.parse

API = "https://waterinfo.rws.nl/api/chart/get"

STATIONS = [
    {"name": "Oost-Vlieland", "var_suffix": "OOST_VLIELAND", "code": "vlieland.haven"},
    {"name": "West-Terschelling", "var_suffix": "WEST_TERSCHELLING", "code": "terschelling.west"},
    {"name": "Harlingen", "var_suffix": "HARLINGEN", "code": "harlingen.waddenzee"},
    {"name": "Kornwerderzand", "var_suffix": "KORNWERDERZAND", "code": "kornwerderzand.waddenzee.buitenhaven"},
    # Hollum has no dedicated station -- reuses Nes's real data, same
    # precedent already established in HARBOURS/extract_rws_waterlevel.py.
    {"name": "Hollum", "var_suffix": "HOLLUM", "code": "ameland.nes"},
    {"name": "Nes", "var_suffix": "NES", "code": "ameland.nes"},
    # Holwerd deliberately excluded -- confirmed (both independently and via
    # Nautinect's own code comment) to have no astronomical series at all.
    {"name": "Lauwersoog", "var_suffix": "LAUWERSOOG", "code": "lauwersoog.waddenzee"},
    {"name": "Schiermonnikoog", "var_suffix": "SCHIERMONNIKOOG", "code": "schiermonnikoog.waddenzee"},
    {"name": "Den Oever", "var_suffix": "DEN_OEVER", "code": "denoever.waddenzee.voorhaven"},
]

VALUES_WINDOW = "-48,336"  # the confirmed-working preset -- see module docstring


def fetch_series(code):
    url = API + "?" + urllib.parse.urlencode({
        "mapType": "astronomische-getij",
        "locationCodes": code,
        "values": VALUES_WINDOW,
        "getijReference": "NAP",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    series = data["series"][0]["data"]
    # [ms, valueCm] pairs, sorted (API already returns them in order).
    return [[_iso_to_ms(p["dateTime"]), p["value"]] for p in series if p["value"] is not None]


def _iso_to_ms(iso):
    from datetime import datetime
    # RWS timestamps are "...Z" (UTC) -- Python's fromisoformat wants +00:00
    # on this version, not a bare "Z".
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def find_extrema(series):
    """Local max/min over the continuous [ms, cm] series -- same simple
    neighbor-comparison technique extract_grids.py's own find_reference_hw()
    already uses on TrilaWatt's NetCDF data. A pure astronomical calculation
    has no sensor noise, so this is far more reliable here than it would be
    on a real measured series (small plateaus at the exact crest still
    happen -- the >= />=  comparison below intentionally tolerates one flat
    step there rather than requiring a strict single-point peak, matching
    how the old PDF-derived dataset defined an event)."""
    events = []
    n = len(series)
    for i in range(1, n - 1):
        v, vp, vn = series[i][1], series[i - 1][1], series[i + 1][1]
        if v >= vp and v >= vn and (v > vp or v > vn):
            events.append([series[i][0], "HW", round(v, 1)])
        elif v <= vp and v <= vn and (v < vp or v < vn):
            events.append([series[i][0], "LW", round(v, 1)])
    return events


def dedupe_adjacent(events):
    """A flat crest can register several consecutive extrema of the same
    type at adjacent 10-min samples -- collapse each run to its single most
    extreme point, so the output is one real event per real extremum, not
    a cluster of near-duplicates a few minutes apart."""
    if not events:
        return events
    out = [events[0]]
    for e in events[1:]:
        last = out[-1]
        if e[1] == last[1] and (e[0] - last[0]) <= 30 * 60000:
            if (e[1] == "HW" and e[2] > last[2]) or (e[1] == "LW" and e[2] < last[2]):
                out[-1] = e
        else:
            out.append(e)
    return out


def write_js(out_path, var_name, events, station_name):
    with open(out_path, "w") as f:
        f.write("// Auto-generated by extract_astro_getij.py -- do not hand-edit.\n")
        f.write(f"// Real RWS astronomical HW/LW predictions for {station_name}, NAP-\n")
        f.write("// referenced cm, [ms, \"HW\"|\"LW\", heightCm] tuples -- rolling ~16-day\n")
        f.write("// window (waterinfo.rws.nl's own chart preset, not a full year -- see\n")
        f.write("// this script's own module docstring). Re-run to refresh.\n")
        f.write(f"var {var_name} = ")
        json.dump(events, f, separators=(",", ":"))
        f.write(";\n")
    print(f"wrote {out_path} ({len(events)} events)")


if __name__ == "__main__":
    for station in STATIONS:
        print(f"fetching {station['name']} ({station['code']})...")
        series = fetch_series(station["code"])
        events = dedupe_adjacent(find_extrema(series))
        n_same_type = sum(1 for i in range(1, len(events)) if events[i][1] == events[i - 1][1])
        print(f"  {len(series)} raw points -> {len(events)} events "
              f"({n_same_type} same-type adjacent pairs, {100 * n_same_type / max(1, len(events) - 1):.1f}%)")
        write_js(
            f"tide_predictions_{station['var_suffix'].lower()}.js",
            f"TIDE_PREDICTIONS_{station['var_suffix']}",
            events, station["name"],
        )
