"""
t17: gzip the seven data files that are fetched on every page load, so the
browser downloads ~98 MB instead of ~499 MB and decompresses them itself.

WHY THIS EXISTS -- and why it is NOT the thing that was tried and reverted
on 2026-07-25. That attempt gzipped the R2 objects and set
`Content-Encoding: gzip` from functions/_middleware.js, expecting the
browser to decompress transparently; verified against the real Cloudflare
edge (curl, Node fetch, and a real Chromium via Playwright) the client
received the still-compressed bytes unchanged, so it was reverted. See that
file's own header comment for the full account. This script's approach is
different in one decisive way: **nothing anywhere claims the response is
encoded**. The .gz files are served as ordinary opaque bytes, and
index.html's own loader decompresses them with the browser-native
DecompressionStream API. No CDN behaviour is depended on at all, and the
Worker spends zero extra CPU (which is what ruled out decompressing
server-side instead).

WHY NOT JUST LET CLOUDFLARE COMPRESS THEM: it already compresses what it
can -- enc_features_t17_simplified.json is 22.2 MB on disk and 4.8 MB on
the wire. But Cloudflare only auto-compresses text-ish content types, and
the six binary grids are served as application/octet-stream, so they go out
raw. Measured on the live site 2026-08-23: the six .bin grids plus
enc_soundg_t17.json are ~492 MB of the ~499 MB total, all of it
uncompressed.

WHY THE INT16 GRIDS COMPRESS SO WELL: they are mostly long runs of the
noData sentinel (land, and everything outside the model domain), and the
real values change slowly cell to cell. Measured ratios, gzip -6:
depth_grid_de.bin 60x, depth_grid.bin 22.6x, enc_soundg_t17.json 16x,
current_grid.bin 5.6x, water_level_grid.bin 5.3x, current_grid_de.bin 4.7x,
water_level_grid_de.bin 3.8x.

NOT COMPRESSED HERE, deliberately:
  - enc_features_t17.js / enc_features_de.js -- lazy-loaded via a plain
    <script src> tag (see index.html's _ensureNativeEnc), which cannot be
    routed through DecompressionStream. They are also off the startup path
    entirely, so they cost nothing on first load.
  - enc_features_t17_simplified.json, enc_soundg_de.json,
    enc_features_de_simplified.json -- Cloudflare already brotli-compresses
    these (confirmed via Content-Encoding: br on the live site). Adding a
    .gz would make them bigger than the br Cloudflare serves for free.

Usage:  python compress_data.py            # only recompress what changed
        python compress_data.py --force    # recompress everything
        python compress_data.py --check    # exit 1 if anything is missing/stale

Re-run this after ever re-running extract_grids.py / extract_grids_de.py,
before redeploy.ps1 (which refuses to deploy without it). See MAINTENANCE.md.
"""
import gzip
import os
import shutil
import sys

# The startup-path files, in rough descending size order. Each is fetched by
# index.html on every page load; every one of them has a `.gz` sibling that
# the loader prefers, falling back to the plain file if it is missing.
FILES = [
    "current_grid_de.bin",
    "current_grid.bin",
    "water_level_grid_de.bin",
    "water_level_grid.bin",
    "enc_soundg_t17.json",
    "depth_grid_de.bin",
    "depth_grid.bin",
]

# Level 9 rather than the 6 the ratios above were measured at: this runs
# once per data refresh (i.e. almost never -- the grids are a frozen 2022
# model), while every visitor pays the download, so trading build seconds
# for wire bytes is free here.
LEVEL = 9


def is_stale(src, gz):
    """True if `gz` is missing or older than its source."""
    if not os.path.exists(gz):
        return True
    return os.path.getmtime(gz) < os.path.getmtime(src)


def compress(src, gz):
    # mtime=0 so re-running on unchanged input produces byte-identical
    # output -- keeps `wrangler r2 object put` from re-uploading 38 MB
    # because a header timestamp moved.
    tmp = gz + ".tmp"
    with open(src, "rb") as fin, gzip.GzipFile(tmp, "wb", compresslevel=LEVEL, mtime=0) as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)
    os.replace(tmp, gz)


def main():
    force = "--force" in sys.argv
    check_only = "--check" in sys.argv
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    missing_src, stale, total_raw, total_gz = [], [], 0, 0
    for name in FILES:
        gz = name + ".gz"
        if not os.path.exists(name):
            # Not an error: the 6 oversized files are gitignored, so a fresh
            # clone legitimately has none of them until the extraction
            # scripts have been run (see DEPLOY.md).
            missing_src.append(name)
            continue
        if force or is_stale(name, gz):
            stale.append(name)
            if not check_only:
                print(f"  compressing {name} ...", flush=True)
                compress(name, gz)
        if os.path.exists(gz):
            raw, small = os.path.getsize(name), os.path.getsize(gz)
            total_raw += raw
            total_gz += small
            print(f"  {name:28} {raw / 1048576:8.2f} MB -> {small / 1048576:7.2f} MB  ({raw / small:5.1f}x)")

    if missing_src:
        print("\n  not present locally (extraction not run yet?): " + ", ".join(missing_src))

    if check_only:
        if stale or missing_src:
            print("\nSTALE OR MISSING -- run: python compress_data.py")
            return 1
        print("\nAll .gz files present and current.")
        return 0

    if total_gz:
        print(f"\n  {'TOTAL':28} {total_raw / 1048576:8.2f} MB -> {total_gz / 1048576:7.2f} MB  "
              f"({total_raw / total_gz:5.1f}x)")
    print("\nDone. Upload the R2-hosted .gz files (see DEPLOY.md) and run redeploy.ps1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
