"""
t17: extract current + water-level + depth grids for the German Wadden Sea
domain (2026-07-24, full NL+Germany domain widening -- see Claude.md / the
approved plan). Sibling to extract_grids.py (Netherlands) -- both import
their shared logic from grid_common.py; this script only contains what's
genuinely Germany-specific: the box/reference-station choice, and reading
BSH's own pre-scraped GeoJSON (quantenschaum/mapping's bsh-data export,
.cache/bsh_data/{DEPARE,SOUNDG,FAIRWY}.json) instead of Dutch S-57 cells.

Usage:
  python extract_grids_de.py [path-to-local-nc-file]
"""
import sys
import os
import json

import numpy as np

import grid_common as gc

LOCAL_PATH = sys.argv[1] if len(sys.argv) > 1 else "../tides_2022_ger.nc"
REMOTE_URL = "https://dl.datenrepository.baw.de/7000/B3955.02.04.70237/Hydrodynamik/2022/tides_2022_ger.nc"

# Full DE domain -- confirmed extent of tides_2022_ger.nc: lat
# 53.20647429455622-55.16264888646538 (442 cells), lon
# 6.066877227159898-9.362654374072601 (744 cells). Rounded slightly inward,
# same reasoning as the NL box (xr.sel(slice(...)) with an endpoint 1 ULP
# outside the actual data range risks silently dropping the last row/col).
LAT_RANGE = (53.21, 55.16)
LON_RANGE = (6.07, 9.36)

# Reference station: Wilhelmshaven (53.5297N, 8.1120E) -- a major, well-
# known German Wadden Sea port roughly central to this box's own coastline
# (Ems estuary near the NL border to the Schleswig-Holstein Wadden Sea
# near Sylt/List). This is a genuinely independent regional clock from
# NL's Harlingen reference, not an attempt to unify them -- see Claude.md's
# own documented limitation for Harlingen-at-distance phase lag (the
# Zuidoostrak/far-corner findings); reusing a single reference station
# hundreds of km away would only make that worse, not better. Each
# region's own refHwIso anchors its own app-side clock (Phase 4).
REF_LAT, REF_LON = 53.5297, 8.1120

# Smaller than NL's 300 (2026-07-24) -- this box is 328,848 cells vs NL's
# 168,504 (1.95x), and Germany's own tides_2022_ger.nc is a much larger
# (45 GB vs 15 GB) file overall. Kept conservative (rather than the
# arithmetically-proportional ~154) given this machine's ~7.4 GB total RAM
# and the bigger accumulator footprint (10 arrays x 72 bins x 328,848
# cells x 8 bytes ~= 1.9 GB, vs NL's ~1 GB) -- leaves more headroom.
TIME_CHUNK = 100


def extract():
    # ref_search_deg widened to 0.05 (2026-07-24) -- the default 0.02 (which
    # worked fine for Harlingen) found no wet cell at all within that radius
    # of Wilhelmshaven's harbour coordinate on the first real run (confirmed
    # directly: 0.02 finds nothing, 0.05 finds a real wet cell with a full
    # valid time series) -- the same "harbour coordinates often mask out a
    # cell" lesson this project has hit before, just needing a bigger
    # search box here specifically.
    return gc.extract_current_and_waterlevel(
        LOCAL_PATH, REMOTE_URL, LAT_RANGE, LON_RANGE, REF_LAT, REF_LON, TIME_CHUNK,
        ref_search_deg=0.05)


BSH_DATA_DIR = "../.cache/bsh_data"

# Real sanity bound for VALSOU (2026-07-24) -- checked directly against
# this project's own downloaded bsh_data before picking a number, not
# guessed: real values in this box's raw data range -4.3 to 68.0 m (see
# Claude.md's SOUNDG spot-check). 80 gives headroom above the largest
# observed real value (German Bight/Heligoland-area water can run
# genuinely deep) while still catching a wild parsing artifact; -4 matches
# NL's own bound for the same kind of small real drying-height range.
SOUNDG_MIN_M, SOUNDG_MAX_M = -4, 80

# Real sanity bound for DEPARE's DRVAL1 (2026-07-24) -- checked directly
# (see Claude.md): this box's raw DRVAL1 range is a plausible -5 to 50 m,
# with no NL-style sentinel spike (only 10/12,801 polygons hit exactly 50,
# a real 50-100m open-water band, not a repeated fake marker). Deliberately
# NOT reusing SOUNDG_MIN_M/MAX_M (-4/80) here -- that would incorrectly
# reject the real, common -5 m band (1,197 of 12,801 polygons, the single
# most frequent DRVAL1 value in the whole file). Wide enough to keep all
# real observed data, still catches a wild future parsing artifact.
DEPARE_MIN_M, DEPARE_MAX_M = -10, 100


def _bsh_bbox_filter(features, lat_range, lon_range):
    """Filters a BSH GeoJSON FeatureCollection's features down to those
    whose geometry falls within the box -- these files cover the whole
    German Bight (+ some Baltic coast), far beyond this project's DE
    domain (see Claude.md's earlier coverage-assessment entry)."""
    lat0, lat1 = lat_range
    lon0, lon1 = lon_range

    def in_box(coords, depth):
        if depth == 0:
            lon, lat = coords[0], coords[1]
            return lon0 <= lon <= lon1 and lat0 <= lat <= lat1
        return any(in_box(c, depth - 1) for c in coords)

    kind_depth = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2,
                  "Polygon": 2, "MultiPolygon": 3}
    out = []
    for f in features:
        geom = f.get("geometry")
        if geom is None:
            continue
        depth = kind_depth.get(geom["type"])
        if depth is None:
            continue
        try:
            if in_box(geom["coordinates"], depth):
                out.append(f)
        except Exception:
            continue
    return out


def read_bsh_depare(lat_range=LAT_RANGE, lon_range=LON_RANGE):
    """Reads real DEPARE polygons from BSH's own GeoJSON export -- same
    (geometry, drval1) pair shape NL's own extract_depare_bands() produces
    from fiona, so both feed gc.rasterize_depare() identically. Checked
    directly (2026-07-24) that this file carries no NL-style sentinel
    value (NL's DRVAL1==-50.0 bug, 4.4% of polygons) -- min/max are a
    plausible -5/50 m, no outlier spike. objl==42 filters to real DEPARE
    only (30 of 12,801 raw features are objl==46/DRGARE -- dredged areas,
    a related but different layer folded into the same file)."""
    path = os.path.join(BSH_DATA_DIR, "DEPARE.json")
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    features = [f for f in fc["features"] if f["properties"].get("objl") == 42]
    features = _bsh_bbox_filter(features, lat_range, lon_range)
    shapes = []
    for f in features:
        drval1 = f["properties"].get("DRVAL1")
        if drval1 is None:
            continue
        shapes.append((f["geometry"], float(drval1)))
    print(f"BSH DEPARE: {len(shapes)} polygons in box (of {len(fc['features'])} total in file)")
    return shapes


def read_bsh_soundg(lat_range=LAT_RANGE, lon_range=LON_RANGE):
    """Reads real SOUNDG points from BSH's own GeoJSON export -- same
    (lon, lat, bedElev-convention depth) tuple shape NL's own
    _read_soundg_points() produces from fiona.

    Sign convention verified before trusting it (2026-07-24, per this
    project's own standing practice of never guessing a safety-relevant
    sign): VALSOU is the official S-57 attribute name (distinct from
    GDAL's own driver-computed "DEPTH", which is what NL's own extraction
    reads, and which had a real, confirmed-backwards sign bug -- see
    Claude.md). VALSOU's own S-57 spec definition is positive-down-from-
    datum. Checked empirically too: this box's raw VALSOU distribution
    is mostly small-magnitude negative values (a handful, -4.3 min -- real
    small drying heights) alongside many large positive values (up to
    68.0 m -- real deep water, common in the German Bight) -- exactly the
    expected shape for a correct positive-=-deeper convention, not the
    inverted pattern (many small-negative "channel depths") a backwards
    sign would produce. VALSOU itself is positive-=-deeper; bedElevation
    (what this function returns) is negative-is-deep, so the value is
    negated below before returning -- same reasoning as NL's own
    extract_enc_soundings_t17.py fix.
    """
    path = os.path.join(BSH_DATA_DIR, "SOUNDG.json")
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    features = [f for f in fc["features"] if f["properties"].get("objl") == 129]
    features = _bsh_bbox_filter(features, lat_range, lon_range)
    points = []
    n_rejected = 0
    for f in features:
        valsou = f["properties"].get("VALSOU")
        if valsou is None:
            continue
        valsou = float(valsou)
        if valsou > SOUNDG_MAX_M or valsou < SOUNDG_MIN_M:
            n_rejected += 1
            continue
        lon, lat = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]
        # bedElevation convention is negative-is-deep (matches TrilaWatt);
        # VALSOU is positive-=-deeper, so negate here (mirrors NL's own
        # extract_enc_soundings_t17.py fix for the same reason).
        points.append((lon, lat, -valsou))
    print(f"BSH SOUNDG: {len(points)} soundings in box ({n_rejected} rejected) "
          f"(of {len(fc['features'])} total in file)")
    return points


def extract_enc_geojson_features_de(lat_range=LAT_RANGE, lon_range=LON_RANGE):
    """Real vector GeoJSON features (DEPARE polygons + SOUNDG points) from
    BSH's own GeoJSON export, matching NL's extract_enc_geojson_features()
    output shape exactly (a "layer" + normalized depth_lat property,
    positive-=-deeper, same as Nautinect's own convention) -- so index.
    html's existing vector-query/render code (chartedDepthAt's bucket
    search, the "Depth chart" Leaflet.VectorGrid layer) works for Germany
    with no further app-side logic changes, just a second pair of data
    files to load (2026-07-24, user request after the raster-only overlay
    shipped in the initial full-domain-widening pass).

    Unlike NL's own extraction, no GDAL/fiona/S-57 reading is involved --
    this is already-scraped GeoJSON, so DRVAL1/VALSOU are read directly, no
    OGR_S57_OPTIONS or ADD_SOUNDG_DEPTH quirk to work around.
    """
    depare_path = os.path.join(BSH_DATA_DIR, "DEPARE.json")
    with open(depare_path, encoding="utf-8") as f:
        depare_fc = json.load(f)
    depare_raw = [f for f in depare_fc["features"] if f["properties"].get("objl") == 42]
    depare_raw = _bsh_bbox_filter(depare_raw, lat_range, lon_range)

    features = []
    n_depare, n_depare_rejected = 0, 0
    for f in depare_raw:
        drval1 = f["properties"].get("DRVAL1")
        if drval1 is None:
            continue
        drval1 = float(drval1)
        if drval1 < DEPARE_MIN_M or drval1 > DEPARE_MAX_M:
            n_depare_rejected += 1
            continue
        features.append({
            "type": "Feature",
            "geometry": gc.round_geom(f["geometry"]),
            "properties": {
                "layer": "DEPARE",
                "depth_lat": round(drval1, 2),
                "DRVAL2": f["properties"].get("DRVAL2"),
            },
        })
        n_depare += 1

    soundg_path = os.path.join(BSH_DATA_DIR, "SOUNDG.json")
    with open(soundg_path, encoding="utf-8") as f:
        soundg_fc = json.load(f)
    soundg_raw = [f for f in soundg_fc["features"] if f["properties"].get("objl") == 129]
    soundg_raw = _bsh_bbox_filter(soundg_raw, lat_range, lon_range)

    n_soundg, n_soundg_rejected = 0, 0
    for f in soundg_raw:
        valsou = f["properties"].get("VALSOU")
        if valsou is None:
            continue
        valsou = float(valsou)
        if valsou > SOUNDG_MAX_M or valsou < SOUNDG_MIN_M:
            n_soundg_rejected += 1
            continue
        features.append({
            "type": "Feature",
            "geometry": gc.round_geom(f["geometry"]),
            "properties": {
                "layer": "SOUNDG",
                "depth_lat": round(valsou, 2),
            },
        })
        n_soundg += 1

    print(f"BSH ENC GeoJSON export: {n_depare} DEPARE polygons ({n_depare_rejected} rejected), "
          f"{n_soundg} SOUNDG points ({n_soundg_rejected} rejected)")
    return features


if __name__ == "__main__":
    depth_only = "--depth-only" in sys.argv
    geojson_only = "--geojson-only" in sys.argv
    simplify_depare = "--simplify-depare" in sys.argv

    if simplify_depare:
        # Same payload-size fix as NL's own (2026-07-23) -- reuses the
        # already-written enc_features_de.js so this can be re-run alone.
        features = gc.load_enc_features("enc_features_de.js")
        depare = [f for f in features if f["properties"]["layer"] == "DEPARE"]
        simplified = gc.simplify_depare_features(depare)
        gc.write_enc_geojson(simplified, "enc_features_de_simplified.js", var_name="ENC_FEATURES_DE_SIMPLIFIED")
        sys.exit(0)
    elif geojson_only:
        features = extract_enc_geojson_features_de()
        depare = [f for f in features if f["properties"]["layer"] == "DEPARE"]
        soundg = [f for f in features if f["properties"]["layer"] == "SOUNDG"]
        gc.write_enc_geojson(depare, "enc_features_de.js", var_name="ENC_FEATURES_DE")
        gc.write_enc_geojson(soundg, "enc_soundg_native_de.js", var_name="ENC_SOUNDG_DE")
        sys.exit(0)

    grid = None
    if depth_only:
        grid_def = gc.load_coarse_grid_def("current_grid_de.meta.json")
    else:
        grid = extract()
        grid_def = {k: grid[k] for k in ("lat0", "dlat", "nlat", "lon0", "dlon", "nlon")}

    fine_def = gc.build_fine_grid_def(grid_def)
    soundg_points = read_bsh_soundg()
    soundg_grid = gc.bin_points_onto_grid(fine_def, soundg_points)
    depare_shapes = read_bsh_depare()
    depare_bands = gc.rasterize_depare(fine_def, depare_shapes)

    depth_enc_only = np.full((fine_def["nlat"], fine_def["nlon"]), np.nan)
    depth_enc_only = gc.composite_enc_depth(fine_def, depth_enc_only, soundg_grid)
    depth_enc_only = gc.apply_depare_priority(depth_enc_only, depare_bands, soundg_grid)
    n_valid = np.sum(~np.isnan(depth_enc_only))
    print(f"DE depth: {n_valid}/{depth_enc_only.size} cells covered "
          f"({100*n_valid/depth_enc_only.size:.1f}%)")

    if grid is not None:
        gc.cull_always_shallow_currents(grid, fine_def, depth_enc_only)
        gc.write_current_bin(grid, "current_grid_de.bin", "current_grid_de.meta.json")
        gc.write_water_level_bin(grid, "water_level_grid_de.bin", "water_level_grid_de.meta.json")

    gc.write_depth_bin(fine_def, depth_enc_only, "depth_grid_de.bin", "depth_grid_de.meta.json")
