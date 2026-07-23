"""
t17: compute a real, data-derived hwOffsetMin (HW time difference vs.
Harlingen, minutes) for every German HARBOURS station, from the real HW
event data already extracted by extract_astro_getij.py (Harlingen) and
extract_bsh_gezeiten.py (Germany) -- instead of the "0, not yet researched"
placeholder every German entry has carried since the full-domain widening.

Same idea as the wadkanovaren.nl table already used for the Dutch stations
(see index.html/Claude.md), but self-computed directly from real predicted
HW times rather than needing an external reference table -- doable now that
real BSH HW/LW predictions exist per German station.

Method: for every real Harlingen HW event within the overlap window (only
Harlingen's own rolling ~16-day RWS window, since that's the shorter of the
two datasets), find the nearest HW event (by absolute time) in the target
station's own real dataset, take the signed difference in minutes
(station_time - harlingen_time), and report the median across all pairs
(median, not mean, to be robust to the rare bad pairing near a data gap).

IMPORTANT CAVEAT, stated here and repeated in the printed output: Harlingen
is 250-450 km from these German stations (vs. the <70 km spread of this
project's existing Dutch reference-station table). Over that distance the
real M2 tidal wave has propagated through a very different, non-adjacent
part of the North Sea/German Bight system -- so unlike the Dutch table
(where hwOffsetMin is a well-established "secondary port" correction to a
nearby primary port), this number for Germany does NOT mean "this station's
tide arrives N minutes after Harlingen's" in the traditional tide-table
sense. It's simply the real, measured average difference in HW clock time
between the two real datasets -- useful as validated reference documentation
(same role hwOffsetMin already plays for NL, per index.html's own comment:
kept as reference data, not applied as a runtime phase shift), not as a
claim that Harlingen is a physically meaningful "primary port" for the
German Bight.

Usage: python compute_hw_offsets.py
"""
import json
import re
import statistics

HARLINGEN_FILE = "tide_predictions_harlingen.js"

GERMAN_STATIONS = [
    ("Borkum", "tide_predictions_borkum.js"),
    ("Norderney", "tide_predictions_norderney.js"),
    ("Wilhelmshaven", "tide_predictions_wilhelmshaven.js"),
    ("Cuxhaven", "tide_predictions_cuxhaven.js"),
    ("List auf Sylt", "tide_predictions_list_auf_sylt.js"),
    ("Emden", "tide_predictions_emden.js"),
    ("Juist", "tide_predictions_juist.js"),
    ("Norddeich", "tide_predictions_norddeich.js"),
    ("Baltrum", "tide_predictions_baltrum.js"),
    ("Bensersiel", "tide_predictions_bensersiel.js"),
    ("Langeoog", "tide_predictions_langeoog.js"),
    ("Spiekeroog", "tide_predictions_spiekeroog.js"),
    ("Wangerooge", "tide_predictions_wangerooge.js"),
    ("Hooksiel", "tide_predictions_hooksiel.js"),
    ("Bremerhaven", "tide_predictions_bremerhaven.js"),
    ("Helgoland", "tide_predictions_helgoland.js"),
    ("Büsum", "tide_predictions_buesum.js"),
    ("Husum", "tide_predictions_husum.js"),
    ("Wyk auf Föhr", "tide_predictions_wyk_auf_foehr.js"),
    ("Wittdün (Amrum)", "tide_predictions_wittduen.js"),
    ("Dagebüll", "tide_predictions_dagebuell.js"),
    ("Hörnum (Sylt)", "tide_predictions_hoernum.js"),
]


def load_events(path):
    # extract_bsh_gezeiten.py's own comment header contains German umlauts
    # (station names like "Büsum") written with this machine's default
    # (cp1252) text encoding, not utf-8 -- try utf-8 first, fall back.
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding="cp1252") as f:
            text = f.read()
    m = re.search(r"=\s*(\[.*\]);\s*$", text, re.S)
    return json.loads(m.group(1))


def hw_times(events):
    return sorted(e[0] for e in events if e[1] == "HW")


def nearest(sorted_list, value):
    # simple linear scan is fine here -- a few thousand HW events at most
    best = min(sorted_list, key=lambda v: abs(v - value))
    return best


def compute_offset(harlingen_hw, station_hw):
    if not station_hw:
        return None
    diffs_min = []
    for t in harlingen_hw:
        n = nearest(station_hw, t)
        diffs_min.append((n - t) / 60000.0)
    return diffs_min


def main():
    harlingen_events = load_events(HARLINGEN_FILE)
    harlingen_hw = hw_times(harlingen_events)
    print(f"Harlingen: {len(harlingen_hw)} real HW events "
          f"(rolling ~16-day RWS window)\n")
    print(f"{'Station':<20} {'n':>5} {'median':>8} {'mean':>8} {'stdev':>7} {'min':>7} {'max':>7}   (minutes, +later/-earlier than Harlingen HW)")
    results = {}
    for name, path in GERMAN_STATIONS:
        try:
            events = load_events(path)
        except FileNotFoundError:
            print(f"{name:<20} -- file not found, skipped")
            continue
        station_hw = hw_times(events)
        diffs = compute_offset(harlingen_hw, station_hw)
        if not diffs:
            print(f"{name:<20} -- no real HW events (BSH data gap for this station)")
            continue
        med = statistics.median(diffs)
        mean = statistics.mean(diffs)
        sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
        print(f"{name:<20} {len(diffs):>5} {med:>8.1f} {mean:>8.1f} {sd:>7.1f} {min(diffs):>7.1f} {max(diffs):>7.1f}")
        results[name] = round(med)
    print("\nRounded hwOffsetMin values (nearest minute):")
    for name, val in results.items():
        print(f"  {name:<20} hwOffsetMin: {val}")


if __name__ == "__main__":
    main()
