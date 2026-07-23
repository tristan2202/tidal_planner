# Deploying the Tidal Router to Cloudflare Pages

This app is static HTML/CSS/vanilla JS with two small additions for live
data: a Cloudflare Pages **Function** (`functions/api/rws-waterlevel.js`)
that proxies RWS's water-level API (which has no CORS support, so a
browser can never call it directly from a deployed site), and a Cloudflare
**R2** bucket holding the handful of data files too large for Pages'
25 MiB per-static-file limit (served by `functions/_middleware.js`).

Everything below is written assuming you have **no existing Cloudflare or
GitHub account** — if you already have either, skip the matching steps.

## 1. Push this repo to GitHub

1. Create a GitHub account at [github.com/signup](https://github.com/signup)
   if you don't have one.
2. Create a new **empty** repository (no README/license/gitignore — this
   directory already has its own), e.g. `tidal-router`.
3. From this directory:
   ```
   git remote add origin https://github.com/<your-username>/tidal-router.git
   git branch -M main
   git push -u origin main
   ```

## 2. Create a Cloudflare account and an R2 bucket

1. Sign up at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up).
2. In the dashboard: **R2 Object Storage** → **Create bucket** → give it
   any name (below, `<bucket>` stands for whatever you actually named
   it — e.g. if you called it `tidal-planner-data`, substitute that
   everywhere `<bucket>` appears). R2's free tier (10 GB storage, no
   egress fees) comfortably covers this app's ~500 MB of oversized files.
3. Install `wrangler` (Cloudflare's CLI) if you haven't already:
   ```
   npm install -g wrangler
   wrangler login
   ```
   (opens a browser to authorize wrangler against the account you just made)
4. Upload the 6 files this repo's `.gitignore` deliberately excludes (they
   live locally in this same directory, just not in git — see the
   `.gitignore` comment for why). **Replace `<bucket>` with your actual
   bucket name in all 6 lines**:
   ```
   wrangler r2 object put <bucket>/current_grid_de.bin --file=current_grid_de.bin
   wrangler r2 object put <bucket>/current_grid.bin --file=current_grid.bin
   wrangler r2 object put <bucket>/water_level_grid_de.bin --file=water_level_grid_de.bin
   wrangler r2 object put <bucket>/water_level_grid.bin --file=water_level_grid.bin
   wrangler r2 object put <bucket>/enc_soundg_native_t17.js --file=enc_soundg_native_t17.js
   wrangler r2 object put <bucket>/enc_features_t17.js --file=enc_features_t17.js
   ```
   Verify each one actually landed before moving on:
   ```
   wrangler r2 object get <bucket>/current_grid_de.bin --file=/dev/null
   ```
   (or check the bucket's contents in the dashboard: R2 → your bucket →
   should list all 6 objects with real byte sizes, not 0).
   Re-run whichever of these after re-running the matching Python
   extraction script locally, to refresh R2 with new data — same "re-run
   to refresh" pattern this project already uses for every other data
   source, just uploading afterward instead of just overwriting a local
   file.

## 3. Connect this GitHub repo to Cloudflare Pages

1. Dashboard → **Workers & Pages** → **Create** → **Pages** → **Connect to
   Git** → pick the repo you pushed in step 1.
2. Build settings: **Framework preset: None**, **Build command: (leave
   empty)**, **Build output directory: `/`** (this repo's root IS the
   site root — no build step, per this project's own architecture).
3. Deploy. Cloudflare assigns a free `<something>.pages.dev` URL —
   confirm the site loads there before doing anything else.

## 4. Bind the R2 bucket to this Pages project

1. In the Pages project's dashboard: **Settings** → **Functions** → **R2
   bucket bindings** → **Add binding**.
2. **Variable name**: `TIDAL_DATA` (must match exactly — this is the name
   `functions/_middleware.js` reads via `env.TIDAL_DATA`).
3. **R2 bucket**: the `<bucket>` (your actual bucket name) from step 2.
4. Save, then trigger a redeploy (Pages → Deployments → Retry deployment)
   so the new binding takes effect.

## 5. Verify end to end
- Open the `*.pages.dev` URL. Open DevTools → Network, reload, confirm
  `current_grid_de.bin` (and the other 5 large files) load with a `200`
  from the deployed origin, not a 404 — this confirms the R2 binding is
  wired correctly.
- Draw a route, switch to Optimizer mode, confirm a real result appears
  (confirms the binary grids loaded and parsed correctly from R2).
- Check the route's own tidal-curve widget shows a solid "measured" line
  ending near the actual current time, not just a dashed predicted line —
  confirms `/api/rws-waterlevel` (the live RWS proxy Function) is working.
  Germany's PEGELONLINE curves should also show fresh data (fetched
  directly from the browser, no Function needed there).

## Custom domain (later, not required for launch)
Pages → your project → **Custom domains** → **Set up a custom domain**.
Requires owning/registering a domain first (~$10-20/yr, see the tidal
router project's own `Claude.md` for the earlier cost research) — the
free `*.pages.dev` URL works fine indefinitely if you don't need one.

## What does NOT need daily attention
- **BSH/RWS tide predictions (HW/LW events)**: astronomical, valid for the
  full calendar year(s) already baked in — no refresh needed until BSH/RWS
  publish a new year.
- **ENC/depth data**: re-run `extract_grids.py`/`extract_grids_de.py` only
  when you want fresher charted-depth data (weekly-ish RWS ENC updates,
  much less frequent for BSH) — re-upload the resulting large files to R2
  per step 2 above if they changed.
- **RWS/PEGELONLINE measured water level**: now genuinely live (see above)
  — nothing to re-run manually for this any more.
