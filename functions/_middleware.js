// Cloudflare Pages middleware -- t07, 2026-07-24. Serves the handful of
// data files too large for Pages' own 25 MiB per-static-file limit from an
// R2 bucket instead, while every other request (index.html, the smaller
// data files, the Python scripts, etc.) falls through unchanged to normal
// static asset serving via next().
//
// WHY THIS EXISTS: Cloudflare Pages hard-caps any individual static asset
// at 25 MiB (confirmed in this project's own earlier research, see
// Claude.md's "hosting-size math" entry -- already hit once before at a
// smaller scale). Six of this app's own generated data files exceed it
// now, one (current_grid_de.bin) by 7.5x. R2 (Cloudflare's object storage)
// has no such per-object limit and a free tier (10 GB) that comfortably
// covers this app's ~500 MB combined oversized-file total.
//
// WHY A MIDDLEWARE, NOT A CLIENT-SIDE URL CHANGE: this intercepts requests
// for these exact filenames at the SAME relative path the app already
// fetches them at (e.g. `fetch('current_grid_de.bin')` in
// _loadBinaryGrid(), or `<script src="enc_features_t17.js">`) -- so
// NOTHING in index.html needed to change to point at R2 specifically; only
// where those particular bytes physically come from changed.
//
// SETUP (see DEPLOY.md): requires an R2 bucket bound to this Pages project
// under the binding name TIDAL_DATA (Pages dashboard: Settings -> Functions
// -> R2 bucket bindings), with these 6 files uploaded to it under their
// own plain filenames (no folder prefix) -- see DEPLOY.md for the exact
// `wrangler r2 object put` commands.
//
// GZIP -- TRIED AND REVERTED, 2026-07-25 (see Claude.md): manually
// gzip-compressing these files and setting Content-Encoding: gzip here
// looked like an easy ~5x size win, but verified (against the REAL
// Cloudflare edge, via curl, Node fetch, AND a real Chromium browser via
// Playwright -- not just local `wrangler pages dev`) that the client
// receives the still-compressed bytes unmodified despite the header --
// Cloudflare Pages Functions do not get this decompressed transparently
// the way a normal precompressed static asset would. Confirmed via a
// scratch test endpoint before touching any real data, then reverted
// before it ever reached production. Don't re-attempt this exact approach
// without a different mechanism (e.g. decompressing server-side with
// DecompressionStream before responding, at the cost of Worker CPU time --
// not attempted, since it reintroduces the very CPU-budget risk that ruled
// out on-the-fly compression in the other direction).
const R2_FILES = new Set([
  'current_grid_de.bin',
  'current_grid.bin',
  'water_level_grid_de.bin',
  'water_level_grid.bin',
  'enc_soundg_t17.json',
  'enc_features_t17.js'
]);

export async function onRequest({ request, env, next }) {
  const url = new URL(request.url);
  const filename = url.pathname.replace(/^\//, '');
  if (!R2_FILES.has(filename)) {
    return next();
  }
  if (!env.TIDAL_DATA) {
    return new Response(
      'R2 bucket not bound to this Pages project (expected binding name TIDAL_DATA) -- see DEPLOY.md.',
      { status: 500 }
    );
  }
  const obj = await env.TIDAL_DATA.get(filename);
  if (!obj) {
    return new Response('File not found in R2 bucket: ' + filename, { status: 404 });
  }
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set('etag', obj.httpEtag);
  // These files are content-addressed by re-running the extraction
  // pipeline, not versioned by filename -- a long, immutable cache is safe
  // as long as a data refresh also re-uploads (overwrites) the R2 object,
  // which naturally busts any downstream cache via a fresh response anyway
  // only for clients that re-fetch; acceptable for data that changes on
  // the order of "when the extraction scripts are re-run," not per-minute.
  headers.set('Cache-Control', 'public, max-age=86400');
  return new Response(obj.body, { headers });
}
