"""
Shared extraction logic for t17_unified_app's per-region grid pipelines
(2026-07-24, full NL+Germany domain widening -- see Claude.md / the
approved plan). extract_grids.py (Netherlands) and extract_grids_de.py
(Germany) both import from here rather than duplicating this code --
everything in this module is genuinely region-agnostic: current/water-level
extraction from a TrilaWatt Hydrodynamik NetCDF file, the binary Int16 grid
writers, and the numeric (not S-57/BSH-source-specific) half of the depth
compositing (apply_depare_priority, the local-median SOUNDG smoothing).

What stays OUT of this module, in each region's own script: reading the
actual ENC/BSH source data. The Netherlands reads real S-57 cells via
fiona; Germany reads pre-scraped GeoJSON (quantenschaum/mapping's bsh-data
export) -- different formats, different libraries, genuinely not shared
code, even though both eventually produce the same shape of intermediate
data (a list of (geometry, drval1) pairs for DEPARE, (lon, lat, depth)
tuples for SOUNDG) that this module's generic compositing functions consume.
"""
import json
import os

import numpy as np
import xarray as xr

M2_HOURS = 12.4206
N_BINS = 72
RANGE_PERCENTILES = (20, 80)

DEPTH_FACTOR = 5  # fine depth cells per coarse (current/water-level) cell, per axis
ALWAYS_SHALLOW_THRESHOLD_M = 0.5
SHALLOW_THRESHOLD_M = 2.0
SHALLOW_NEIGHBOR_RADIUS_CELLS = 2  # ~2 fine cells (factor=5 -> ~200m) either side

GRID_NODATA_I16 = -32768
CURRENT_BIN_SCALE = 1000
WATER_LEVEL_BIN_SCALE = 1000
DEPTH_BIN_SCALE = 100


def open_dataset(local_path, remote_url):
    """Opens a TrilaWatt Hydrodynamik NetCDF file, local file preferred,
    remote (fsspec+h5netcdf) fallback -- same "bulk-download then process
    locally" pattern t01's spike found necessary (remote range-reads were
    slow/inconsistent for a box this size)."""
    if os.path.exists(local_path):
        print(f"opening local file {local_path}")
        return xr.open_dataset(local_path)
    print(f"local file not found, falling back to remote (slow for a box this size): {remote_url}")
    import fsspec
    fs = fsspec.filesystem("http")
    f = fs.open(remote_url, block_size=256 * 1024)
    return xr.open_dataset(f, engine="h5netcdf")


def find_reference_hw(ds, ref_lat, ref_lon, search_deg=0.02):
    """Finds the first local-maximum HW instant in sea_surface_height_2d
    near (ref_lat, ref_lon), searching a small box around the requested
    point for the nearest cell that actually has data -- harbour
    coordinates are exactly where a hydrodynamic model is likely to mask
    out a cell (already learned the hard way for Harlingen; the same
    lesson applies to any new reference station, NL or Germany)."""
    box = ds["sea_surface_height_2d"].sel(
        lat=slice(ref_lat - search_deg, ref_lat + search_deg),
        lon=slice(ref_lon - search_deg, ref_lon + search_deg))
    valid_counts = box.count(dim="time")
    if float(valid_counts.max()) == 0:
        raise RuntimeError(f"no wet cell found within {search_deg} deg of ({ref_lat}, {ref_lon})")
    best = valid_counts.argmax(dim=["lat", "lon"])
    point = box.isel(lat=best["lat"], lon=best["lon"])
    resolved_lat, resolved_lon = float(point.lat), float(point.lon)
    print(f"reference cell: lat={resolved_lat:.4f}, lon={resolved_lon:.4f}")
    vals = point.load().values
    times = ds["time"].values
    for i in range(2, len(vals) - 1):
        if np.isnan(vals[i]):
            continue
        if vals[i] >= vals[i - 1] and vals[i] >= vals[i + 1]:
            return times[i], vals[i], resolved_lat, resolved_lon
    raise RuntimeError("no local maximum found in the sampled window")


def rolling_tidal_range(ssh, m2_hours=M2_HOURS, dt_minutes=20):
    win = int(round(m2_hours * 60 / dt_minutes / 2))
    n = len(ssh)
    out = np.full(n, np.nan)
    for i in range(n):
        lo, hi = max(0, i - win), min(n, i + win + 1)
        seg = ssh[lo:hi]
        if np.all(np.isnan(seg)):
            continue
        out[i] = np.nanmax(seg) - np.nanmin(seg)
    return out


def accumulate_current_chunk(u_chunk, v_chunk, bin_idx_chunk, mask_chunk, u_sum, v_sum, count, n_bins):
    for b in range(n_bins):
        sel = mask_chunk & (bin_idx_chunk == b)
        if not sel.any():
            continue
        u_sum[b] += np.nansum(u_chunk[sel], axis=0)
        v_sum[b] += np.nansum(v_chunk[sel], axis=0)
        count[b] += np.sum(~np.isnan(u_chunk[sel]), axis=0)


def accumulate_scalar_chunk(x_chunk, bin_idx_chunk, mask_chunk, x_sum, count, n_bins):
    for b in range(n_bins):
        sel = mask_chunk & (bin_idx_chunk == b)
        if not sel.any():
            continue
        x_sum[b] += np.nansum(x_chunk[sel], axis=0)
        count[b] += np.sum(~np.isnan(x_chunk[sel]), axis=0)


def extract_current_and_waterlevel(nc_path, remote_url, lat_range, lon_range,
                                    ref_lat, ref_lon, time_chunk,
                                    n_bins=N_BINS, m2_hours=M2_HOURS,
                                    range_percentiles=RANGE_PERCENTILES,
                                    ref_search_deg=0.02):
    """Full current + water-level extraction for one region's box: finds
    the region's own reference HW instant, classifies neap/spring by a
    rolling tidal-range percentile split, then accumulates phase-binned
    u/v/water-level sums in time_chunk-sized chunks (keeps peak memory
    bounded regardless of how many years/timesteps of source data exist --
    same technique t15's wide-box extraction and t17's original box both
    already used). Returns a grid dict with the same shape
    write_current_bin()/write_water_level_bin() expect.
    """
    full_ds = open_dataset(nc_path, remote_url)
    ref_hw_time, ref_hw_height, res_lat, res_lon = find_reference_hw(full_ds, ref_lat, ref_lon, ref_search_deg)
    print(f"reference HW: {ref_hw_time} (height {ref_hw_height:.2f} m)")
    ssh_ref = full_ds["sea_surface_height_2d"].sel(lat=res_lat, lon=res_lon, method="nearest").load().values

    ds = full_ds.sel(lat=slice(*lat_range), lon=slice(*lon_range))
    times = ds["time"].values
    ntime = len(times)
    nlat, nlon = ds.sizes["lat"], ds.sizes["lon"]
    print(f"box: nlat={nlat}, nlon={nlon}, cells={nlat*nlon}, ntime={ntime}")

    phase_hours = ((times - ref_hw_time) / np.timedelta64(1, "h")) % m2_hours
    bin_idx = np.clip((phase_hours / m2_hours * n_bins).astype(int), 0, n_bins - 1)

    print("computing rolling tidal-range indicator (spring/neap signal)...")
    tidal_range = rolling_tidal_range(ssh_ref, m2_hours)
    valid = ~np.isnan(tidal_range)
    neap_ref, spring_ref = np.nanpercentile(tidal_range[valid], list(range_percentiles))
    neap_mask = valid & (tidal_range <= neap_ref)
    spring_mask = valid & (tidal_range >= spring_ref)
    print(f"neap reference range: {neap_ref:.2f} m ({neap_mask.sum()} samples), "
          f"spring reference range: {spring_ref:.2f} m ({spring_mask.sum()} samples)")

    u_sum_neap = np.zeros((n_bins, nlat, nlon)); v_sum_neap = np.zeros((n_bins, nlat, nlon)); count_cur_neap = np.zeros((n_bins, nlat, nlon))
    u_sum_spring = np.zeros((n_bins, nlat, nlon)); v_sum_spring = np.zeros((n_bins, nlat, nlon)); count_cur_spring = np.zeros((n_bins, nlat, nlon))
    wl_sum_neap = np.zeros((n_bins, nlat, nlon)); count_wl_neap = np.zeros((n_bins, nlat, nlon))
    wl_sum_spring = np.zeros((n_bins, nlat, nlon)); count_wl_spring = np.zeros((n_bins, nlat, nlon))

    n_chunks = (ntime + time_chunk - 1) // time_chunk
    for c in range(n_chunks):
        lo, hi = c * time_chunk, min(ntime, (c + 1) * time_chunk)
        print(f"chunk {c+1}/{n_chunks} (timesteps {lo}:{hi})...")
        u_chunk = ds["current_velocity_2d_x"].isel(time=slice(lo, hi)).load().values
        v_chunk = ds["current_velocity_2d_y"].isel(time=slice(lo, hi)).load().values
        wl_chunk = ds["sea_surface_height_2d"].isel(time=slice(lo, hi)).load().values
        bin_idx_chunk = bin_idx[lo:hi]
        accumulate_current_chunk(u_chunk, v_chunk, bin_idx_chunk, neap_mask[lo:hi], u_sum_neap, v_sum_neap, count_cur_neap, n_bins)
        accumulate_current_chunk(u_chunk, v_chunk, bin_idx_chunk, spring_mask[lo:hi], u_sum_spring, v_sum_spring, count_cur_spring, n_bins)
        accumulate_scalar_chunk(wl_chunk, bin_idx_chunk, neap_mask[lo:hi], wl_sum_neap, count_wl_neap, n_bins)
        accumulate_scalar_chunk(wl_chunk, bin_idx_chunk, spring_mask[lo:hi], wl_sum_spring, count_wl_spring, n_bins)
        del u_chunk, v_chunk, wl_chunk

    with np.errstate(invalid="ignore", divide="ignore"):
        u_neap = np.where(count_cur_neap > 0, u_sum_neap / count_cur_neap, np.nan)
        v_neap = np.where(count_cur_neap > 0, v_sum_neap / count_cur_neap, np.nan)
        u_spring = np.where(count_cur_spring > 0, u_sum_spring / count_cur_spring, np.nan)
        v_spring = np.where(count_cur_spring > 0, v_sum_spring / count_cur_spring, np.nan)
        wl_neap = np.where(count_wl_neap > 0, wl_sum_neap / count_wl_neap, np.nan)
        wl_spring = np.where(count_wl_spring > 0, wl_sum_spring / count_wl_spring, np.nan)

    speed_neap = np.sqrt(u_neap**2 + v_neap**2)
    speed_spring = np.sqrt(u_spring**2 + v_spring**2)
    print(f"current neap:   mean {np.nanmean(speed_neap):.3f} m/s, max {np.nanmax(speed_neap):.3f} m/s")
    print(f"current spring: mean {np.nanmean(speed_spring):.3f} m/s, max {np.nanmax(speed_spring):.3f} m/s")
    print(f"water level neap:   min {np.nanmin(wl_neap):.2f} max {np.nanmax(wl_neap):.2f} m")
    print(f"water level spring: min {np.nanmin(wl_spring):.2f} max {np.nanmax(wl_spring):.2f} m")

    grid_def = {
        "lat0": float(ds.lat.values[0]), "dlat": float(ds.lat.values[1] - ds.lat.values[0]), "nlat": nlat,
        "lon0": float(ds.lon.values[0]), "dlon": float(ds.lon.values[1] - ds.lon.values[0]), "nlon": nlon,
    }

    return {
        **grid_def,
        "refHwIso": str(np.datetime_as_string(ref_hw_time, unit="s")),
        "m2Hours": m2_hours, "nBins": n_bins,
        "neapRangeM": round(float(neap_ref), 3), "springRangeM": round(float(spring_ref), 3),
        "uNeap": u_neap, "vNeap": v_neap, "uSpring": u_spring, "vSpring": v_spring,
        "wlNeap": wl_neap, "wlSpring": wl_spring,
    }


def build_fine_grid_def(grid_def, factor=DEPTH_FACTOR):
    """Fine-grid geometry (fine cells per coarse cell, same origin), no
    network call -- depth is ENC/BSH-only in this project (see Claude.md,
    "Vertical-datum mismatch found"), so nothing reads an actual TrilaWatt
    Topographie raster's values anymore, only the coarse grid's own
    dimensions determine the fine grid's shape."""
    fine_def = {
        "lat0": grid_def["lat0"], "dlat": grid_def["dlat"] / factor, "nlat": grid_def["nlat"] * factor,
        "lon0": grid_def["lon0"], "dlon": grid_def["dlon"] / factor, "nlon": grid_def["nlon"] * factor,
        "factor": factor,
    }
    print(f"fine depth grid: {fine_def['nlat']}x{fine_def['nlon']} = "
          f"{fine_def['nlat']*fine_def['nlon']} cells (factor {factor})")
    return fine_def


def cull_always_shallow_currents(grid, fine_def, depth_fine, threshold=ALWAYS_SHALLOW_THRESHOLD_M):
    """Nulls out uNeap/vNeap/uSpring/vSpring (all bins) at any coarse
    current-grid cell that can NEVER hold more than `threshold` metres of
    water, even at the highest sampled spring water level -- these cells
    are permanently too shallow to sail regardless of tide (2026-07-23,
    real perf fix -- a full-grid diagnostic redraw was taking 5-10s/step
    partly because of these). Mirrors chartedDepthAt()'s own "actual depth
    = charted depth + water level" formula, using each coarse cell's
    DEEPEST fine sub-cell and its own highest sampled spring water level --
    erring toward keeping data rather than over-pruning. A coarse cell
    with no valid fine-depth data at all (outside ENC/BSH coverage) is left
    alone -- "unknown" is not the same as "known always shallow."
    """
    factor = fine_def["factor"]
    nlat, nlon = grid["nlat"], grid["nlon"]
    with np.errstate(invalid="ignore"):
        charted_depth_fine = -depth_fine  # bedElevation is negative-below-LAT; depth is positive-below-LAT
    coarse_max_depth = np.full((nlat, nlon), np.nan)
    for iLat in range(nlat):
        for iLon in range(nlon):
            block = charted_depth_fine[iLat * factor:(iLat + 1) * factor, iLon * factor:(iLon + 1) * factor]
            if np.any(~np.isnan(block)):
                coarse_max_depth[iLat, iLon] = np.nanmax(block)

    with np.errstate(invalid="ignore"):
        wl_max = np.nanmax(grid["wlSpring"], axis=0)  # highest sampled spring water level per cell, any bin

    known = ~np.isnan(coarse_max_depth) & ~np.isnan(wl_max)
    max_actual_depth = np.where(known, coarse_max_depth + wl_max, np.nan)
    always_shallow = known & (max_actual_depth < threshold)

    n_before = int(np.sum(~np.isnan(grid["uNeap"][0])))
    for arr_name in ("uNeap", "vNeap", "uSpring", "vSpring"):
        grid[arr_name][:, always_shallow] = np.nan
    n_after = int(np.sum(~np.isnan(grid["uNeap"][0])))
    print(f"culled {int(np.sum(always_shallow))}/{nlat*nlon} coarse cells as always-shallow "
          f"(<{threshold} m even at spring HW) -- valid cells at bin 0: {n_before} -> {n_after}")
    return grid


def _pack_i16(flat_values, scale):
    """flat_values: 1-D float array, NaN = no-data. Returns an Int16
    (little-endian) numpy array, same length, with NaN -> GRID_NODATA_I16."""
    nanmask = np.isnan(flat_values)
    scaled = np.where(nanmask, 0.0, flat_values * scale)
    clipped = np.clip(np.round(scaled), -32767, 32767).astype("<i2")
    clipped[nanmask] = GRID_NODATA_I16
    return clipped


def _write_binary_arrays(arrays_in_order, scale, bin_path):
    total_bytes = 0
    with open(bin_path, "wb") as f:
        for arr in arrays_in_order:
            packed = _pack_i16(np.asarray(arr, dtype=float).ravel(), scale)
            f.write(packed.tobytes())
            total_bytes += packed.nbytes
    print(f"wrote {bin_path} ({total_bytes} bytes, {len(arrays_in_order)} array(s))")


def write_current_bin(grid, bin_path, meta_path):
    arrays = ["uNeap", "vNeap", "uSpring", "vSpring"]
    flat = [grid[name].reshape(grid["nBins"], -1) for name in arrays]
    _write_binary_arrays(flat, CURRENT_BIN_SCALE, bin_path)
    meta = {
        "lat0": grid["lat0"], "dlat": grid["dlat"], "nlat": grid["nlat"],
        "lon0": grid["lon0"], "dlon": grid["dlon"], "nlon": grid["nlon"],
        "refHwIso": grid["refHwIso"], "m2Hours": grid["m2Hours"], "nBins": grid["nBins"],
        "neapRangeM": grid["neapRangeM"], "springRangeM": grid["springRangeM"],
        "scale": CURRENT_BIN_SCALE, "noData": GRID_NODATA_I16,
        "arrays": arrays, "bin": os.path.basename(bin_path),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    print(f"wrote {meta_path}")


def write_water_level_bin(grid, bin_path, meta_path):
    arrays = ["wlNeap", "wlSpring"]
    flat = [grid[name].reshape(grid["nBins"], -1) for name in arrays]
    _write_binary_arrays(flat, WATER_LEVEL_BIN_SCALE, bin_path)
    meta = {
        "lat0": grid["lat0"], "dlat": grid["dlat"], "nlat": grid["nlat"],
        "lon0": grid["lon0"], "dlon": grid["dlon"], "nlon": grid["nlon"],
        "refHwIso": grid["refHwIso"], "m2Hours": grid["m2Hours"], "nBins": grid["nBins"],
        "neapRangeM": grid["neapRangeM"], "springRangeM": grid["springRangeM"],
        "scale": WATER_LEVEL_BIN_SCALE, "noData": GRID_NODATA_I16,
        "arrays": arrays, "bin": os.path.basename(bin_path),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    print(f"wrote {meta_path}")


def write_depth_bin(fine_def, depth, bin_path, meta_path):
    _write_binary_arrays([depth], DEPTH_BIN_SCALE, bin_path)
    meta = dict(fine_def)
    meta["scale"] = DEPTH_BIN_SCALE
    meta["noData"] = GRID_NODATA_I16
    meta["arrays"] = ["bedElevation"]
    meta["bin"] = os.path.basename(bin_path)
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    print(f"wrote {meta_path}")


def bin_points_onto_grid(fine_def, points):
    """Bins a list of (lon, lat, bedElev-convention depth) points onto the
    fine grid, one exact-cell average per cell. Region-agnostic -- both NL
    (S-57 SOUNDG via fiona) and Germany (BSH GeoJSON SOUNDG) produce this
    same (lon, lat, depth) tuple shape from their own, genuinely different,
    source readers."""
    nlat, nlon = fine_def["nlat"], fine_def["nlon"]
    sums = {}
    counts = {}
    for lon, lat, enc_depth in points:
        iLat = round((lat - fine_def["lat0"]) / fine_def["dlat"])
        iLon = round((lon - fine_def["lon0"]) / fine_def["dlon"])
        if iLat < 0 or iLat >= nlat or iLon < 0 or iLon >= nlon:
            continue
        key = (iLat, iLon)
        sums[key] = sums.get(key, 0.0) + enc_depth
        counts[key] = counts.get(key, 0) + 1

    grid = np.full((nlat, nlon), np.nan)
    for (iLat, iLon), total in sums.items():
        grid[iLat, iLon] = total / counts[(iLat, iLon)]
    return grid


def rasterize_depare(fine_def, shapes):
    """Rasterizes a list of (geometry, drval1) pairs onto the fine grid --
    region-agnostic (rasterio doesn't care whether the geometries came from
    fiona/S-57 or a GeoJSON file), used by both NL's extract_depare_bands
    and Germany's own BSH-GeoJSON-based equivalent."""
    import rasterio.features

    nlat, nlon = fine_def["nlat"], fine_def["nlon"]
    lat0, dlat = fine_def["lat0"], fine_def["dlat"]
    lon0, dlon = fine_def["lon0"], fine_def["dlon"]
    # Pixel (col, row) -> (lon, lat), row = iLat (matches depth_grid's own
    # row-major iLat-then-iLon convention, no north-up flip needed here --
    # the flip only happens in the browser's canvas draw).
    transform = rasterio.Affine(dlon, 0, lon0 - dlon / 2, 0, dlat, lat0 - dlat / 2)
    bands = rasterio.features.rasterize(
        shapes, out_shape=(nlat, nlon), transform=transform,
        fill=np.nan, dtype="float64")

    n_valid = np.sum(~np.isnan(bands))
    print(f"DEPARE bands rasterized: {n_valid}/{nlat*nlon} cells covered "
          f"({100*n_valid/(nlat*nlon):.1f}%)")
    return bands


def composite_enc_depth(fine_def, depth, soundg_grid):
    """Priority-composite: override the (all-NaN, since depth is ENC/BSH-
    only) base depth with real official soundings wherever they exist,
    cell by cell. soundg_grid is the caller's already-binned grid
    (bin_points_onto_grid) -- callers already need it separately for
    apply_depare_priority's own shallow-water override, so binning it twice
    would be pure waste."""
    print("compositing real soundings onto the fine depth grid...")
    mask = ~np.isnan(soundg_grid)
    depth = np.where(mask, soundg_grid, depth)
    n_overridden = int(mask.sum())
    nlat, nlon = fine_def["nlat"], fine_def["nlon"]
    print(f"{n_overridden} of {nlat*nlon} fine cells overridden with real soundings "
          f"({100*n_overridden/(nlat*nlon):.1f}%)")
    return depth


def _soundg_local_median(soundg_grid, want_mask, radius=SHALLOW_NEIGHBOR_RADIUS_CELLS):
    """For every True cell in want_mask, returns the median of soundg_grid
    over a (2*radius+1)^2 neighborhood centered on it (nan-omitted), or nan
    if no sounding falls in that neighborhood at all. Blunts the risk of a
    single noisy/outlier sounding reaching the safety grid directly."""
    nlat, nlon = soundg_grid.shape
    out = np.full((nlat, nlon), np.nan)
    iLats, iLons = np.nonzero(want_mask)
    for iLat, iLon in zip(iLats, iLons):
        lat0, lat1 = max(0, iLat - radius), min(nlat, iLat + radius + 1)
        lon0, lon1 = max(0, iLon - radius), min(nlon, iLon + radius + 1)
        window = soundg_grid[lat0:lat1, lon0:lon1]
        if np.any(~np.isnan(window)):
            out[iLat, iLon] = np.nanmedian(window)
    return out


def apply_depare_priority(depth, depare_bands, soundg_grid=None, shallow_threshold_m=SHALLOW_THRESHOLD_M):
    """Final compositing step. DEPARE bands win over a lone sounding in
    deep water (an officially-compiled, smoothly-bounded area is more
    representative of a whole cell than one point); a real nearby sounding
    wins in shallow water (< shallow_threshold_m), since a coarse band's
    conservative shallow bound throws away exactly the precision that
    matters most for a tight go/no-go call there.

    Sign-convention note: depare_bands holds its own positive-depth-below-
    datum convention (DRVAL1-style); depth/soundg_grid are in
    bedElevation's negative-below-datum convention (matching TrilaWatt and
    already-negated soundings). Must negate before merging.
    """
    depare_bedElev = -depare_bands
    has_depare = ~np.isnan(depare_bedElev)
    result = np.where(has_depare, depare_bedElev, depth)

    if soundg_grid is None:
        return result

    is_shallow = has_depare & (depare_bedElev > -shallow_threshold_m)
    n_shallow = int(is_shallow.sum())
    soundg_shallow = _soundg_local_median(soundg_grid, is_shallow)
    use_soundg = is_shallow & ~np.isnan(soundg_shallow)
    result = np.where(use_soundg, soundg_shallow, result)

    n_flipped = int(use_soundg.sum())
    print(f"shallow-water sounding priority: {n_flipped}/{n_shallow} shallow "
          f"DEPARE cells (< {shallow_threshold_m} m) had a nearby sounding and now "
          f"use it instead of the DEPARE band; {n_shallow - n_flipped} had none "
          f"nearby and kept the DEPARE band value")
    return result


def round_geom(geom, ndigits=6):
    """Rounds every coordinate to 6 decimal places (~11 cm at this latitude)
    before JSON-encoding -- a real payload-size trim (source data often
    carries 7+ digit precision, pure bloat once encoded as text), moved
    here 2026-07-24 (full-domain widening) so Germany's own vector-feature
    export can reuse it instead of duplicating this walk."""
    def r(pt):
        return [round(pt[0], ndigits), round(pt[1], ndigits)] + list(pt[2:])

    def walk(coords, depth):
        if depth == 0:
            return r(coords)
        return [walk(c, depth - 1) for c in coords]

    depth = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2,
             "Polygon": 2, "MultiPolygon": 3}.get(geom["type"], 2)
    return {"type": geom["type"], "coordinates": walk(geom["coordinates"], depth)}


def write_enc_geojson(features, out_path, var_name="ENC_FEATURES_T17"):
    # .js-wrapped, not raw .geojson -- matches every other generated data
    # file this app loads via a plain <script src> tag, so the browser
    # never needs fetch()/CORS handling for local files.
    fc = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w") as f:
        f.write("// Auto-generated -- do not hand-edit.\n")
        f.write("// Real ENC/BSH vector features, kept as actual geometry (not\n")
        f.write("// rasterized) -- see Claude.md for the vector-tile depth rewrite and\n")
        f.write("// (if this is the _simplified file) the DEPARE-simplification\n")
        f.write("// comparison. Every feature has a normalized depth_lat property\n")
        f.write("// (metres below LAT, positive = deeper), matching Nautinect's own\n")
        f.write("// convention.\n")
        f.write(f"var {var_name} = ")
        json.dump(fc, f, separators=(",", ":"))
        f.write(";\n")
    print(f"wrote {out_path} ({len(features)} features)")


def load_enc_features(path):
    """Reads the real vector features back out of an already-written
    enc_features_*.js -- lets simplify_depare_features() (and any future
    re-run) work from the already-extracted data without redoing the
    original cell/GeoJSON read."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    payload = text[text.index("{"):text.rindex("}") + 1]
    return json.loads(payload)["features"]


DEPARE_SIMPLIFY_TOLERANCE_DEG = 0.0001  # ~11 m at this latitude


def simplify_depare_features(features, tolerance=DEPARE_SIMPLIFY_TOLERANCE_DEG):
    """Douglas-Peucker-simplifies every DEPARE polygon's boundary (shapely,
    preserve_topology=True) -- real payload-size fix (NL measured ~15s page
    load before this at the original test-box scale; see Claude.md).
    Falls back to the polygon's own original (unsimplified) geometry
    whenever simplification comes back invalid or empty, rather than
    shipping a broken one (NL found ~0.2% of its own polygons do this,
    confirmed to be the largest/most-complex ones, and confirmed the
    fallback fixes a real render bug -- see Claude.md)."""
    from shapely.geometry import shape, mapping

    out = []
    n_fallback_native = 0
    for f in features:
        geom = shape(f["geometry"])
        simplified = geom.simplify(tolerance, preserve_topology=True)
        if simplified.is_empty or not simplified.is_valid:
            simplified = geom
            n_fallback_native += 1
        if simplified.is_empty:
            continue
        gj = mapping(simplified)
        out.append({
            "type": "Feature",
            "geometry": {"type": gj["type"], "coordinates": gj["coordinates"]},
            "properties": f["properties"],
        })
    print(f"simplified {len(out)}/{len(features)} DEPARE polygons at tolerance={tolerance} "
          f"({n_fallback_native} fell back to native geometry)")
    return out


def load_fine_grid_def(depth_grid_meta_path):
    with open(depth_grid_meta_path) as f:
        d = json.load(f)
    return {k: d[k] for k in ("lat0", "dlat", "nlat", "lon0", "dlon", "nlon", "factor")}


def load_coarse_grid_def(current_grid_meta_path):
    with open(current_grid_meta_path) as f:
        d = json.load(f)
    return {k: d[k] for k in ("lat0", "dlat", "nlat", "lon0", "dlon", "nlon")}
