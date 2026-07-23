"""
t17: extract current + water-level + depth grids for the extended canonical
working area -- Vlieland through Schiermonnikoog, the Wadden Sea, and a
buffer of open water north of the barrier islands. This REPLACES the old
Terschelling-Vlieland test box as the area the real app (t17) works over;
the older milestones (t03-t16) keep using their own small box and are left
untouched, same as every other completed milestone in this project.

Same method as t05's extract_current_grid.py / t16's
extract_water_level_grid.py (Harlingen HW reference, spring/neap split via
rolling tidal-range percentile classification), but chunked like t15's
extract_current_grid_wide.py -- this box has ~49,348 cells (2.5x the t15
wide box), so a full-year (time, lat, lon) float64 load would need
~20+ GB, well past this machine's ~7.4 GB total RAM. Both current AND
water level are accumulated in chunks here (t16's original water-level
script loaded the whole array at once -- fine for its small box, not for
this one).

Usage:
  python extract_grids.py [path-to-local-nc-file]
"""
import sys
import os
import json
import io

import numpy as np
import xarray as xr

import grid_common as gc

LOCAL_PATH = sys.argv[1] if len(sys.argv) > 1 else "../.cache/tides_2015_nl.nc"
REMOTE_URL = "https://dl.datenrepository.baw.de/7000/B3955.02.04.70237/Hydrodynamik/2015/tides_2015_nl.nc"

# Full NL domain (2026-07-24, see Claude.md / the approved full-domain-
# extension plan) -- widened from the original Vlieland-Schiermonnikoog
# test box to tides_2022_nl.nc's own full confirmed extent (lat
# 52.865477992507266-53.938934344529976, lon 4.179138375068806-
# 7.337406650441411, 243x713 cells). Rounded slightly inward (to whole
# 0.01 deg) rather than exactly on the file's own floating-point edges,
# since xr.sel(slice(...)) with an endpoint 1 ULP outside the actual data
# range risks silently dropping the last row/column.
LAT_RANGE = (52.87, 53.93)
LON_RANGE = (4.19, 7.33)

REF_LAT, REF_LON = 53.1746, 5.4222  # Harlingen, same as every prior extraction

# Reduced from 800 (2026-07-24, full-domain widening) -- the full NL box
# is 173,259 cells vs the old test box's 49,348 (3.51x), so 800 would now
# load ~3.3 GB per chunk (u+v+wl, float64) on top of the ~1 GB the ten
# (N_BINS, nlat, nlon) accumulator arrays already hold constantly -- too
# much for this machine's ~7.4 GB total RAM (see Claude.md). 300 keeps a
# chunk's own u+v+wl load at ~1.25 GB, leaving headroom alongside the
# accumulators; n_chunks rises from 33 to ~88, a modest, not multiplicative,
# increase in per-chunk Python-loop overhead (accumulate_*_chunk still
# loops N_BINS times per chunk regardless of chunk size).
TIME_CHUNK = 300

# M2_HOURS/N_BINS/RANGE_PERCENTILES/DEPTH_FACTOR and all the actual
# extraction/compositing logic (find_reference_hw, rolling_tidal_range,
# the accumulate_*_chunk helpers, the binary grid writers, apply_depare_
# priority, ...) moved to grid_common.py 2026-07-24 (full-domain widening)
# -- both this script and extract_grids_de.py (Germany) now import them
# from one shared place rather than each keeping its own copy. Only what's
# genuinely region-specific (this NL box's own bbox/reference station, its
# S-57/fiona ENC readers) stays here.


def extract():
    return gc.extract_current_and_waterlevel(
        LOCAL_PATH, REMOTE_URL, LAT_RANGE, LON_RANGE, REF_LAT, REF_LON, TIME_CHUNK)


# build_fine_grid_def (no-network-call fine-grid geometry builder) moved
# to grid_common.py -- called as gc.build_fine_grid_def() below. Note why
# fetch_depth()'s own WCS fetch below is no longer on the real pipeline's
# hot path: depth is ENC-only since 2026-07-21's vertical-datum fix, so
# nothing reads fetch_depth()'s actual TrilaWatt raster VALUES anymore,
# only geometry -- kept here (unused by the main flow, still NL-specific
# since it hardcodes TrilaWatt's "..._nl" coverage ID) as a real, working
# WCS query for manual TrilaWatt comparisons, not deleted outright.


def fetch_depth(grid_def, factor=gc.DEPTH_FACTOR):
    """Samples TrilaWatt Topographie (native ~10 m) at FACTOR-times finer
    resolution than the current/water-level grid, not 1:1 with it.

    t09's original approach (and this function's own first version) point-
    sampled Topographie only at the coarse ~500 m current-grid points --
    fine for open water, but narrow real, actively-dredged fairways (like
    Vliesloot near Vlieland -- confirmed 2026-07-20 by comparing this
    grid's output against the real ENC soundings/buoys there, which show
    the channel is fine) fall *between* sample points that far apart and
    get misrepresented as shallower than reality. t16's
    extract_depth_grid_fine.py already solved this for its small test box
    (FACTOR=10, same coarse-grid-relative fine/coarse mapping) -- this is
    the same fix, applied here, at a smaller factor (5, not 10) purely
    because this box has ~19x t16's test box's coarse cell count, so
    FACTOR=10 here would mean ~100x more WCS point-samples and a much
    larger output file than FACTOR=10 meant for that small box.

    Still one WCS GetCoverage call (server-side subset, no bulk download)
    -- only the number of points sampled FROM that one response changes.
    """
    import rasterio
    import requests
    from pyproj import Transformer

    WCS_URL = "https://mdi-dienste.baw.de/geoserver/TrilaWatt_Topographie/wcs"
    COVERAGE_ID = "TrilaWatt_Topographie__topography_2015_nl"

    fine_def = {
        "lat0": grid_def["lat0"], "dlat": grid_def["dlat"] / factor, "nlat": grid_def["nlat"] * factor,
        "lon0": grid_def["lon0"], "dlon": grid_def["dlon"] / factor, "nlon": grid_def["nlon"] * factor,
        "factor": factor,
    }
    lat_max = fine_def["lat0"] + fine_def["dlat"] * (fine_def["nlat"] - 1)
    lon_max = fine_def["lon0"] + fine_def["dlon"] * (fine_def["nlon"] - 1)
    print(f"fine depth grid: {fine_def['nlat']}x{fine_def['nlon']} = "
          f"{fine_def['nlat']*fine_def['nlon']} cells (factor {factor})")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    pad_deg = 0.01
    e_min, n_min = transformer.transform(fine_def["lon0"] - pad_deg, fine_def["lat0"] - pad_deg)
    e_max, n_max = transformer.transform(lon_max + pad_deg, lat_max + pad_deg)
    params = {
        "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
        "COVERAGEID": COVERAGE_ID, "FORMAT": "image/geotiff",
        "SUBSET": [f"E({e_min},{e_max})", f"N({n_min},{n_max})"],
    }
    print("fetching depth via WCS (one call, server-side subset)...")
    # Timeout raised from 120s (2026-07-24, full-domain widening) -- the WCS
    # response scales with area (~1 GB expected for the full NL box vs. the
    # test box's 308 MB), and this is a single unpaginated GetCoverage call.
    r = requests.get(WCS_URL, params=params, timeout=600)
    r.raise_for_status()
    print(f"WCS GetCoverage: {len(r.content)} bytes")

    with rasterio.open(io.BytesIO(r.content)) as src:
        arr = src.read(1)
        print(f"native raster shape={src.shape} res={src.res}")
        lats = fine_def["lat0"] + fine_def["dlat"] * np.arange(fine_def["nlat"])
        lons = fine_def["lon0"] + fine_def["dlon"] * np.arange(fine_def["nlon"])
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        e_grid, n_grid = transformer.transform(lon_grid.ravel(), lat_grid.ravel())
        coords = list(zip(e_grid, n_grid))
        samples = np.array([v[0] for v in src.sample(coords)], dtype=float)
        samples[samples == src.nodata] = np.nan
        depth = samples.reshape(fine_def["nlat"], fine_def["nlon"])

    n_valid = np.sum(~np.isnan(depth))
    n_underwater = np.sum(depth < 0)
    print(f"{n_valid}/{depth.size} cells valid, {n_underwater} underwater "
          f"(mean depth-where-wet: {np.nanmean(np.where(depth < 0, -depth, np.nan)):.2f} m)")
    return fine_def, depth


# cull_always_shallow_currents(), the binary grid writers (write_current_
# bin/write_water_level_bin/write_depth_bin, _pack_i16, _write_binary_
# arrays), and the now-fully-dead JSON writers (write_current_js/
# write_water_level_js/write_depth_js -- nothing has loaded these since
# index.html switched to the binary format) all moved to grid_common.py
# 2026-07-24 -- called as gc.cull_always_shallow_currents(), gc.write_
# current_bin(), etc. below. Only NL-specific ENC/S-57 reading stays here.


def _read_soundg_points(fine_def):
    """Direct fiona SOUNDG-layer scan across ENC_ROOTS (2026-07-24, full-
    domain widening) -- replaces the old _load_soundg_bins(), which read a
    stale, pre-generated enc_soundings_t17.js from t16_depth_viewer (a
    frozen milestone whose own ENC_ROOT only ever covered the original
    Waddenzee-product cells, never the "Nederland (excl Zeeland,
    Waddenzee)" cells added for the full-domain southwest extension). This
    project's own convention is "t01-t16 stay frozen" -- rather than edit
    t16's script to add the new cells (or leave t17 silently missing
    SOUNDG coverage the DEPARE/current/depth pipelines already have), t17
    now reads SOUNDG directly, the same way extract_depare_bands() already
    reads DEPARE directly, both via the shared _enc_cells_overlapping_box().

    Sign convention + sanity bound: same fix as extract_enc_geojson_
    features()'s own SOUNDG loop (see Claude.md, "Real sign-inversion bug
    found") -- GDAL's ADD_SOUNDG_DEPTH is positive-below-datum for a normal
    wet sounding, negative for a drying height. This function negates it
    before returning, since (unlike depth_lat's positive-=-deeper vector-
    feature convention) bedElevation elsewhere in this file is negative-
    is-deep, matching TrilaWatt.
    """
    import fiona

    # Real bug, found by a 0-soundings result on the first full-domain run
    # (2026-07-24): without this option set, GDAL's S-57 driver never
    # populates SOUNDG's DEPTH property at all (every feature's `.get
    # ("DEPTH")` comes back None, silently skipped by the `if depth is
    # None: continue` below) and doesn't split multipoint soundings into
    # individual features either. extract_enc_geojson_features() already
    # sets this before its own SOUNDG scan; this function needs its own
    # copy since it can run standalone in the main extraction flow, before
    # (or without) that function ever executing in the same process.
    os.environ["OGR_S57_OPTIONS"] = "SPLIT_MULTIPOINT=ON,ADD_SOUNDG_DEPTH=ON"

    cells = _enc_cells_overlapping_box(fine_def)
    points = []
    n_rejected = 0
    for cell in cells:
        try:
            with fiona.open(cell, layer="SOUNDG") as src:
                for f in src:
                    geom = f["geometry"]
                    if geom is None:
                        continue
                    depth = f["properties"].get("DEPTH")
                    if depth is None:
                        continue
                    depth = float(depth)
                    if depth > 45 or depth < -4:
                        n_rejected += 1
                        continue
                    lon, lat = geom["coordinates"][0], geom["coordinates"][1]
                    points.append((lon, lat, -depth))
        except Exception:
            pass
    print(f"SOUNDG: {len(points)} real soundings ({n_rejected} rejected) across {len(cells)} cells")
    return points


# _bin_soundg_points() and composite_enc_depth() moved to grid_common.py
# 2026-07-24 (as bin_points_onto_grid()/composite_enc_depth()) -- called as
# gc.bin_points_onto_grid()/gc.composite_enc_depth() below.


# ENC_ROOTS (2026-07-24, full-domain widening): the "Waddenzee met Diepte"
# product's own cells (WAD01-16/EMS01-04) stop at lon 4.667 -- confirmed by
# direct bounds inspection, not covering the new southwestern extension
# (Den Helder/Texel/Marsdiep down to the full box's 4.19 edge). The RWS
# download-listing API's "Nederland (excl Zeeland, Waddenzee)" package
# (fetched manually, unzipped to nl_rest_enc/) adds 10 more cells overlapping
# the full box -- mostly IJsselmeer/inland-waterway cells (YM/MK/EK/SK
# prefixes), not the open-sea Marsdiep approach itself (checked directly:
# its own westernmost cell only reaches lon 5.029). The core Den Helder
# harbour/Marsdiep channel area turns out to already be inside WAD01's own
# bounds (lon 4.667-4.833, lat 52.883-53.083 covers Den Helder's
# 52.964N/4.760E) -- the remaining gap is a strip of open North Sea west of
# ~4.667, the same kind of "no ENC product charts this" gap already
# documented and accepted for the buffer north of the barrier islands.
ENC_ROOTS = [
    "../.cache/waddenzee_enc/20260717_U7Inland_Waddenzee_week 29_NL/ENC_ROOT/1R/7",
    "../.cache/nl_rest_enc/ENC_ROOT",
]


# extract_fairway_mask()/write_fairway_mask_js() removed 2026-07-24 --
# confirmed fully dead: their only consumer was the auto-router
# (buildSafetyGrid/buildGateBiasGrid in index.html), which was dropped
# entirely 2026-07-21 (see Claude.md, "Auto-route (t11) dropped entirely").
# FAIRWY polygons are still real, valid, cached ENC data if a future
# feature wants fairway-awareness again -- just nothing generates a mask
# from them right now.


def extract_depare_bands(fine_def):
    """Rasterizes the official RWS IENC DEPARE (depth-area) polygons onto
    the same fine grid as depth_grid.js -- each cell gets DRVAL1 (the
    band's shallow/conservative bound, positive depth-below-datum, same
    sign convention as bedElevation's negation -- see Claude.md's sign-
    convention note) of whichever charted depth band it falls in.

    Why: depth_grid.js's ENC-composited raster is grainy in places (see
    Claude.md, 2026-07-20 "swiss cheese" finding -- 47.4% of composited
    cells jump >1.5m from their neighbor) because it's built from sparse
    independent point soundings. DEPARE bands are the *opposite* kind of
    ENC data: a small number of large, officially-drawn polygons with
    smooth boundaries -- rasterizing them (rather than point-sampling)
    should render as clean charted-area boundaries for direct visual
    comparison against the grainy composited raster, as a diagnostic only
    (the actual depth-safety check stays on depth_grid.js's real
    soundings, not this band-level approximation).
    """
    import fiona
    import rasterio.features

    cells = _enc_cells_overlapping_box(fine_def)

    shapes = []
    for cell in cells:
        try:
            with fiona.open(cell, layer="DEPARE") as src:
                for f in src:
                    if f["geometry"] is None:
                        continue
                    drval1 = f["properties"].get("DRVAL1")
                    if drval1 is None:
                        continue
                    shapes.append((f["geometry"], float(drval1)))
        except Exception:
            pass
    print(f"DEPARE: {len(shapes)} polygons across {len(cells)} cells")

    nlat, nlon = fine_def["nlat"], fine_def["nlon"]
    lat0, dlat = fine_def["lat0"], fine_def["dlat"]
    lon0, dlon = fine_def["lon0"], fine_def["dlon"]
    # Pixel (col, row) -> (lon, lat), row = iLat (matches depth_grid.js's
    # row-major iLat-then-iLon convention, no north-up flip needed here --
    # the flip only happens in the browser's canvas draw, same as t16/t17's
    # existing diagDepthRGB rendering).
    transform = rasterio.Affine(dlon, 0, lon0 - dlon / 2, 0, dlat, lat0 - dlat / 2)
    bands = rasterio.features.rasterize(
        shapes, out_shape=(nlat, nlon), transform=transform,
        fill=np.nan, dtype="float64")

    n_valid = np.sum(~np.isnan(bands))
    print(f"DEPARE bands rasterized: {n_valid}/{nlat*nlon} cells covered "
          f"({100*n_valid/(nlat*nlon):.1f}%)")
    return bands


def write_depare_bands_js(fine_def, bands, out_path):
    def r(x):
        return None if np.isnan(x) else round(float(x), 2)

    rows = [[r(x) for x in row] for row in bands]
    payload = dict(fine_def)
    payload["drval1"] = rows
    with open(out_path, "w") as f:
        f.write("// Auto-generated by extract_grids.py (extract_depare_bands) -- do not\n")
        f.write("// hand-edit. Official RWS IENC DEPARE depth-area polygons rasterized onto\n")
        f.write("// the same fine grid as depth_grid.js -- drval1 = shallow-bound depth\n")
        f.write("// below datum (m, same sign convention as -bedElevation) of the charted\n")
        f.write("// band each cell falls in. Diagnostic-only comparison against the\n")
        f.write("// point-sounding-composited depth_grid.js, not a safety-check source.\n")
        f.write("var ENC_DEPARE_BANDS_T17 = ")
        json.dump(payload, f)
        f.write(";\n")
    print(f"wrote {out_path}")


def _enc_cells_overlapping_box(fine_def, probe_layer="DEPARE"):
    """Shared cell-listing helper -- factored out 2026-07-23 (the vector-
    feature-export work below needed the exact same "which S-57 cells
    overlap our box" logic extract_depare_bands() already had inline, so
    rather than a third near-copy (a second one already exists in
    t16_depth_viewer/extract_enc_soundings_t17.py), this is now the one
    place it lives; extract_depare_bands() below was updated to call it too."""
    import glob
    import os
    import fiona

    def cell_overlaps_box(cell_path):
        try:
            try:
                with fiona.open(cell_path, layer=probe_layer) as src:
                    b = src.bounds
            except Exception:
                layers = fiona.listlayers(cell_path)
                with fiona.open(cell_path, layer=layers[0]) as src:
                    b = src.bounds
        except Exception as e:
            # A handful of cells in the broader "Nederland (excl Zeeland,
            # Waddenzee)" package (2026-07-24, full-domain widening) may not
            # be plain S-57 vector cells fiona can open at all (e.g. a
            # raster-only or malformed entry) -- skip rather than abort the
            # whole extraction over one unreadable cell.
            print(f"  skipping unreadable cell {cell_path}: {e}")
            return False
        lon_min, lat_min, lon_max, lat_max = b
        lat0, lon0 = fine_def["lat0"], fine_def["lon0"]
        lat_max_grid = lat0 + fine_def["dlat"] * (fine_def["nlat"] - 1)
        lon_max_grid = lon0 + fine_def["dlon"] * (fine_def["nlon"] - 1)
        return not (lon_max < lon0 or lon_min > lon_max_grid
                    or lat_max < lat0 or lat_min > lat_max_grid)

    cells = []
    seen = set()
    for root in ENC_ROOTS:
        for c in sorted(glob.glob(os.path.join(root, "*/*.000"))):
            real = os.path.abspath(c)
            if real in seen:
                continue
            seen.add(real)
            cells.append(c)
    return [c for c in cells if cell_overlaps_box(c)]


# Real S-57 GeoJSON feature export (2026-07-23) -- Phase A of the vector-
# tile depth rewrite (see the approved plan). Unlike composite_enc_depth()/
# apply_depare_priority() above (which rasterize DEPARE/SOUNDG onto
# depth_grid.js's flat grid, then get smoothed client-side by
# chartedDepthAt() -- confirmed last session to bleed shallow water from a
# fairway's own banks into the fairway itself, ~17-30% of real fairway
# cells depending on smoothing radius), this keeps every DEPARE polygon and
# SOUNDG point as its own real feature with its own exact boundary/value --
# no rasterization, no cross-feature smoothing possible. Both layers get a
# NORMALIZED `depth_lat` property (metres below LAT, positive = deeper),
# matching Nautinect's own convention exactly (confirmed from their saved
# app.js -- "depth_lat = depth below LAT (positive = deeper)"). Both source
# layers actually use the SAME native sign convention (corrected 2026-07-23
# -- see Claude.md, "Real sign-inversion bug found"; the original comment
# here claimed SOUNDG's DEPTH was negative-below-datum, discovered wrong by
# direct verification against a known ~24-40m-deep test cluster): DEPARE's
# DRVAL1/DRVAL2 and SOUNDG's DEPTH (via GDAL's ADD_SOUNDG_DEPTH) are BOTH
# positive-below-datum already, so depth_lat = DEPTH directly for SOUNDG,
# no negation -- it's only bedElevation elsewhere in this file (negative-
# is-deep) that needs SOUNDG negated before merging.
# _round_geom() moved to grid_common.py as gc.round_geom() (2026-07-24) --
# called below.


def extract_enc_geojson_features(fine_def):
    import os
    import fiona

    os.environ["OGR_S57_OPTIONS"] = "SPLIT_MULTIPOINT=ON,ADD_SOUNDG_DEPTH=ON"
    cells = _enc_cells_overlapping_box(fine_def)

    features = []
    n_depare, n_depare_rejected, n_soundg, n_soundg_rejected = 0, 0, 0, 0
    for cell in cells:
        try:
            with fiona.open(cell, layer="DEPARE") as src:
                for f in src:
                    geom = f["geometry"]
                    if geom is None:
                        continue
                    drval1 = f["properties"].get("DRVAL1")
                    if drval1 is None:
                        continue
                    drval1 = float(drval1)
                    # Real sentinel value found 2026-07-23, checked directly
                    # rather than assumed: 549 of 12,463 polygons (4.4%) carry
                    # EXACTLY -50.0 (not a noisy spread of large negative
                    # values -- one repeated round number), almost certainly
                    # a "no data"/unknown-depth marker in the source S-57
                    # data, not a real 50 m-above-datum measurement. Same
                    # real-world sanity bound already validated for SOUNDG
                    # below (channels here go to ~-40 m, drying flats top out
                    # a few metres above datum), expressed in depth_lat's own
                    # positive-=-deeper convention.
                    if drval1 < -4 or drval1 > 45:
                        n_depare_rejected += 1
                        continue
                    drval2 = f["properties"].get("DRVAL2")
                    sordat = f["properties"].get("SORDAT")
                    ninfom = f["properties"].get("NINFOM") or f["properties"].get("INFORM")
                    # DRVAL1 itself dropped (2026-07-23, payload-size trim) --
                    # it's an exact duplicate of depth_lat for this layer (no
                    # information lost); DRVAL2 is kept since it's genuinely
                    # additional (the band's deeper bound, used for the real
                    # "X-Y m LAT" range display Nautinect's own popup shows).
                    features.append({
                        "type": "Feature",
                        "geometry": gc.round_geom(geom),
                        "properties": {
                            "layer": "DEPARE",
                            "depth_lat": round(drval1, 2),
                            "DRVAL2": drval2,
                            "SORDAT": sordat, "NINFOM": ninfom,
                        },
                    })
                    n_depare += 1
        except Exception as e:
            print(f"  {cell}: no DEPARE ({e})")

        try:
            with fiona.open(cell, layer="SOUNDG") as src:
                for f in src:
                    geom = f["geometry"]
                    if geom is None:
                        continue
                    depth = f["properties"].get("DEPTH")
                    if depth is None:
                        continue
                    depth = float(depth)
                    # Sign convention + sanity bound corrected 2026-07-23
                    # (real bug, was backward -- see Claude.md, "Real sign-
                    # inversion bug found"). GDAL's ADD_SOUNDG_DEPTH is
                    # POSITIVE for a normal wet sounding (deeper = more
                    # positive) and NEGATIVE for a drying height above
                    # datum -- confirmed directly against this project's own
                    # well-documented ~24-40m-deep test cluster (raw DEPTH
                    # came back +20 to +40 there, matching known depth
                    # exactly), the OPPOSITE of what this extraction
                    # originally assumed. depth_lat wants the same
                    # positive-=-deeper convention DEPARE's DRVAL1 already
                    # uses, so raw `depth` needs NO negation here (unlike
                    # extract_enc_soundings_t17.py's own fix, which negates
                    # because THAT file feeds bedElevation's opposite,
                    # negative-is-deep convention). Bound: real channels
                    # here go to ~40m equivalent, real drying-flat crests
                    # top out a few metres above datum -- anything past
                    # +45/-4 is almost certainly a parsing artifact (e.g.
                    # SPLIT_MULTIPOINT leaking a non-depth point into
                    # SOUNDG), not a real sounding.
                    if depth > 45 or depth < -4:
                        n_soundg_rejected += 1
                        continue
                    sordat = f["properties"].get("SORDAT")
                    features.append({
                        "type": "Feature",
                        "geometry": gc.round_geom(geom),
                        "properties": {
                            "layer": "SOUNDG",
                            "depth_lat": round(depth, 2),
                            "SORDAT": sordat,
                        },
                    })
                    n_soundg += 1
        except Exception as e:
            print(f"  {cell}: no SOUNDG ({e})")

    print(f"ENC GeoJSON export: {n_depare} DEPARE polygons ({n_depare_rejected} rejected), "
          f"{n_soundg} SOUNDG points ({n_soundg_rejected} rejected) across {len(cells)} cells")
    return features


# write_enc_geojson()/load_enc_features()/simplify_depare_features()/
# DEPARE_SIMPLIFY_TOLERANCE_DEG moved to grid_common.py 2026-07-24 (full-
# domain widening) -- called as gc.write_enc_geojson() etc. below, so
# Germany's own vector-feature export (extract_grids_de.py) reuses them
# instead of duplicating this code a second time.


def load_fine_grid_def(depth_grid_path="depth_grid.meta.json"):
    """Thin NL-convenience wrapper (default filename) over
    gc.load_fine_grid_def()."""
    return gc.load_fine_grid_def(depth_grid_path)


def load_coarse_grid_def(current_grid_path="current_grid.meta.json"):
    """Thin NL-convenience wrapper (default filename) over
    gc.load_coarse_grid_def()."""
    return gc.load_coarse_grid_def(current_grid_path)


if __name__ == "__main__":
    depth_only = "--depth-only" in sys.argv
    depare_only = "--depare-only" in sys.argv
    geojson_only = "--geojson-only" in sys.argv
    simplify_depare = "--simplify-depare" in sys.argv
    if simplify_depare:
        # Payload-size fix (2026-07-23, see Claude.md) -- reuses the
        # already-written enc_features_t17.js so this can be re-run alone
        # without redoing the S-57 cell read. Writes a SEPARATE file
        # (DEPARE only -- SOUNDG needs no simplification, it lives in its
        # own always-eager-loaded enc_soundg_native_t17.js regardless of
        # this toggle) so index.html can load both and switch between
        # them -- kept permanently, not removed once decided: simplified
        # is the default (fast page load), native is an opt-in "high
        # detail" mode, since both are verified to produce equivalent
        # query/render results (see Claude.md).
        features = gc.load_enc_features("enc_features_t17.js")
        depare = [f for f in features if f["properties"]["layer"] == "DEPARE"]
        simplified = gc.simplify_depare_features(depare)
        gc.write_enc_geojson(simplified, "enc_features_t17_simplified.js", var_name="ENC_FEATURES_T17_SIMPLIFIED")
    elif geojson_only:
        # Phase A of the vector-tile depth rewrite (2026-07-23, see the
        # approved plan) -- reuses the already-written depth_grid.js's own
        # geometry (same trick --depare-only already used) so this can be
        # re-run alone without redoing the WCS depth fetch.
        #
        # Written as two SEPARATE files, not one combined file (2026-07-23,
        # real page-load fix -- see Claude.md): enc_features_t17.js is now
        # DEPARE-only and lazy-loaded client-side (only fetched if the user
        # asks for "high-detail" native DEPARE), while SOUNDG -- needed
        # unconditionally by the shallow-water query regardless of which
        # DEPARE dataset is active -- goes in its own always-eager-loaded
        # file so it's never gated behind that same lazy load.
        fine_def = load_fine_grid_def()
        features = extract_enc_geojson_features(fine_def)
        depare = [f for f in features if f["properties"]["layer"] == "DEPARE"]
        soundg = [f for f in features if f["properties"]["layer"] == "SOUNDG"]
        gc.write_enc_geojson(depare, "enc_features_t17.js")
        gc.write_enc_geojson(soundg, "enc_soundg_native_t17.js", var_name="ENC_SOUNDG_T17")
    elif depare_only:
        fine_def = load_fine_grid_def()
        depare_bands = extract_depare_bands(fine_def)
        write_depare_bands_js(fine_def, depare_bands, "enc_depare_bands_t17.js")
    else:
        grid = None
        if depth_only:
            # Fast re-run path (depth alone, no 3+ min current re-extraction)
            # -- current_grid.js isn't held in memory here, so it can't be
            # re-culled by cull_always_shallow_currents() below. Not a
            # correctness problem for the common case (nothing about ENC
            # depth coverage changes often enough for this to go stale in
            # practice), just a known gap in this maintenance-only path.
            grid_def = load_coarse_grid_def()
        else:
            grid = extract()
            grid_def = {k: grid[k] for k in ("lat0", "dlat", "nlat", "lon0", "dlon", "nlon")}
        fine_def = gc.build_fine_grid_def(grid_def)
        soundg_points = _read_soundg_points(fine_def)
        soundg_grid = gc.bin_points_onto_grid(fine_def, soundg_points)
        depare_bands = extract_depare_bands(fine_def)
        # ENC-only depth, no TrilaWatt fallback (2026-07-21 user decision):
        # TrilaWatt's own data paper (Lepper et al. 2025, Scientific Data)
        # confirms its topography AND sea_surface_height_2d are both
        # NHN/NAP-referenced, while RWS's ENC SOUNDG/DEPARE are Chart-Datum
        # (LAT)-referenced -- compositing them silently mixed two different
        # vertical datums with no offset correction (see Claude.md,
        # "Vertical-datum mismatch found"). Starting from an all-NaN base
        # sidesteps the mismatch by construction: every cell that gets a
        # value is LAT-referenced, no exceptions -- at the cost of no depth
        # data at all outside ENC's own coverage (~28% of the grid, already
        # quantified in Claude.md, mostly the open-water buffer north of
        # the islands). TrilaWatt is NOT dropped from the project --
        # current_grid/water_level_grid above are untouched, this only
        # removes it as a DEPTH fallback. (The old TrilaWatt-composited
        # comparison array and its "with TrilaWatt" diagnostic output, and
        # the WCS fetch of TrilaWatt's actual raster values entirely, were
        # removed 2026-07-24 -- confirmed dead, see build_fine_grid_def().)
        depth_enc_only = np.full((fine_def["nlat"], fine_def["nlon"]), np.nan)
        depth_enc_only = gc.composite_enc_depth(fine_def, depth_enc_only, soundg_grid)
        depth_enc_only = gc.apply_depare_priority(depth_enc_only, depare_bands, soundg_grid)
        n_valid = np.sum(~np.isnan(depth_enc_only))
        print(f"ENC-only depth: {n_valid}/{depth_enc_only.size} cells covered "
              f"({100*n_valid/depth_enc_only.size:.1f}%)")

        if grid is not None:
            # Deferred until here (2026-07-23, real perf fix -- see
            # Claude.md) so the always-shallow cull below can use the real
            # ENC depth data computed just above, instead of writing
            # current_grid before that data exists.
            gc.cull_always_shallow_currents(grid, fine_def, depth_enc_only)
            gc.write_current_bin(grid, "current_grid.bin", "current_grid.meta.json")
            gc.write_water_level_bin(grid, "water_level_grid.bin", "water_level_grid.meta.json")

        gc.write_depth_bin(fine_def, depth_enc_only, "depth_grid.bin", "depth_grid.meta.json")
        # fairway_mask_t17.js/enc_depare_bands_t17.js generation removed
        # 2026-07-24 -- confirmed dead (extract_fairway_mask's only
        # consumer was the auto-router, dropped entirely 2026-07-21;
        # enc_depare_bands_t17.js was the DEPARE-diagnostic toggle removed
        # in the same top-bar cleanup as the TrilaWatt comparison above).
        # depare_bands itself is still computed above -- it's a real input
        # to apply_depare_priority, not just this dead output file.
