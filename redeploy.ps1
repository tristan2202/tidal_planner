# Re-deploy the Tidal Router to Cloudflare Pages.
#
# The 6 R2-hosted files (current_grid_de.bin, etc.) exceed Cloudflare
# Pages' 25 MiB per-file limit even on a direct CLI upload -- they're kept
# in this directory for local dev (python -m http.server reads them
# directly) but served from R2 in production (see functions/_middleware.js
# and DEPLOY.md). This script moves them out just long enough to deploy,
# then puts them back -- so a UI/code-only change is one command:
#
#   .\redeploy.ps1
#
# Requires: wrangler installed and logged in (npm install -g wrangler;
# wrangler login), and the "tidal-planner" Pages project already created
# (see DEPLOY.md -- only needed once).
$ErrorActionPreference = "Stop"

$bigFiles = @(
  "current_grid_de.bin",
  "current_grid.bin",
  "water_level_grid_de.bin",
  "water_level_grid.bin",
  "enc_soundg_native_t17.js",
  "enc_features_t17.js"
)

$stash = Join-Path $env:TEMP "tidal_router_r2_stash"
New-Item -ItemType Directory -Force -Path $stash | Out-Null

foreach ($f in $bigFiles) {
  if (Test-Path $f) { Move-Item -Force $f $stash }
}

try {
  wrangler pages deploy . --project-name=tidal-planner --commit-dirty=true
} finally {
  foreach ($f in $bigFiles) {
    $stashed = Join-Path $stash $f
    if (Test-Path $stashed) { Move-Item -Force $stashed . }
  }
  Remove-Item -Recurse -Force $stash -ErrorAction SilentlyContinue
}
