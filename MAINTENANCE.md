# Data maintenance — what needs re-running, and how often

Quick answer to "is everything live now?": **no** — only the *measured*
water-level curves are genuinely live (as of 2026-07-24). Tidal
*predictions*, and all charted depth/current data, are still static
snapshots that need a human to re-run a script occasionally. This page is
the one place that tracks which is which and on what cadence.

## Nothing to do — genuinely live

- **RWS (NL) measured water level**: fetched fresh on every page load via
  the `/api/rws-waterlevel` Cloudflare Function (`functions/api/
  rws-waterlevel.js`), which proxies RWS's own API server-side.
- **PEGELONLINE (DE) measured water level**: fetched fresh on every page
  load directly from the browser (PEGELONLINE supports CORS, no proxy
  needed) — see `_refreshLivePegel()` in `index.html`.

Both keep their old static `rws_waterlevel_*.js`/`pegelonline_waterlevel_
*.js` files as an offline/local-dev fallback only — those files themselves
are still static and do go stale, but nothing reads them once the live
fetch succeeds, so there's no reason to refresh them just for the deployed
site.

## Re-run periodically — tidal *predictions* (HW/LW events)

- **`extract_astro_getij.py`** (Harlingen's real HW/LW events, the anchor
  every other NL station's phase is read against): pulls from RWS's
  `astronomische-getij` chart API, which only ever publishes a **rolling
  ~16-day window** (a fixed API preset, not something this project chose).
  **Re-run every 1-2 weeks** — if left too long, the app's real-calendar-
  date departure search runs out of real future data to plan against.
- **`extract_bsh_gezeiten.py`** (German stations' real HW/LW events):
  pulls a **full two calendar years at once** (2026+2027 as of when this
  was last run) from gezeiten.bsh.de. **Re-run roughly once a year**, once
  BSH has published predictions for the next year (check in Q4) — no need
  to touch this more often than that.

Both write small `tide_predictions_*.js` files (well under the 25 MiB
Pages limit) — a normal `redeploy.ps1` picks up the refreshed files with
no extra step.

## Re-run when you want fresher charted depth — no fixed cadence

- **NL ENC soundings/DEPARE** (`extract_grids.py`, the `SOUNDG`/`DEPARE`
  layers feeding `chartedDepthAt()`/the depth-chart overlay): sourced from
  RWS's own IENC product, corrected via **weekly** "Berichten aan
  Zeevarenden" (Notices to Mariners) upstream — so RWS's own data can be
  up to a week fresher than whatever this app last extracted. There's no
  urgency to match that exactly; re-run when planning through an area
  you know has changed recently (dredging, a reported shift), or every
  few months as routine upkeep.
- **DE ENC-equivalent (BSH via the `quantenschaum/mapping` GeoJSON
  mirror)** (`extract_grids_de.py`): community-maintained, refreshed on an
  irregular schedule by its maintainer (own README tracks manually-
  incorporated "Nachrichten für Seefahrer" corrections) — check that
  repo's own last-updated dates before assuming it's current; no fixed
  cadence to recommend here beyond "occasionally."

Re-running either script regenerates `depth_grid*.bin` and (if you also
pass the geojson flags) the ENC vector feature files — some of these are
among the 6 files hosted in R2, not git. **After re-running, re-upload the
changed files to R2** (see `DEPLOY.md` step 2, remember `--remote`), then
`redeploy.ps1` for anything else that changed.

## Re-run only if BAW ships a new model — current/water-level grids

- **TrilaWatt Hydrodynamik** (`current_grid*.bin`/`water_level_grid*.bin`,
  via `extract_grids.py`/`extract_grids_de.py`'s main current/water-level
  pipeline): this is a **historical model run tied to a fixed year**
  (2022, currently) — it does not get "more current" by re-running it
  against the same source data. There is **no time-based cadence** for
  this one at all. The only real trigger is BAW publishing an actual **new
  model version** (a rare, announced event, not a silent refresh) —
  worth an occasional manual check of
  [trilawatt.eu](https://trilawatt.eu/en/) (e.g. once a year), not a
  scheduled job. Re-running against the *same* 2022 source data on any
  schedule would be pure wasted effort.
- Same reasoning applies to the depth pipeline's TrilaWatt/EasyGSH-DB
  background layer (used only where ENC has no coverage) — tied to the
  same model releases, same "check occasionally, don't schedule" status.

## After any data re-run
1. If the file is one of the 6 in `.gitignore` (`current_grid_de.bin`,
   `current_grid.bin`, `water_level_grid_de.bin`, `water_level_grid.bin`,
   `enc_soundg_native_t17.js`, `enc_features_t17.js`): re-upload it to R2
   with `wrangler r2 object put <bucket>/<file> --file=<file> --remote`
   (see `DEPLOY.md`).
2. For everything else (including the smaller `tide_predictions_*.js`/
   `depth_grid*.bin`/ENC vector files that DO live in git): commit, push,
   and run `.\redeploy.ps1`.
3. `redeploy.ps1` alone is enough for any `index.html`/`functions/*.js`
   change that doesn't touch data files at all.
