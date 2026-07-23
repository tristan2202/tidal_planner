// Cloudflare Pages Function -- t07, 2026-07-24. Proxies RWS's
// WaterWebservices water-level API server-side, so the browser can get
// genuinely live NL water-level data instead of the static
// rws_waterlevel_*.js snapshot files this app used before (see Claude.md).
//
// WHY THIS EXISTS: confirmed (curl, both a real POST and an OPTIONS CORS
// preflight, see extract_rws_waterlevel.py's own docstring) that RWS's API
// sends NO Access-Control-Allow-Origin header at all -- a browser fetch()
// from this app's own deployed origin would be blocked by CORS regardless
// of anything client-side code does. CORS only applies to browser-to-
// server requests, though -- a Cloudflare Pages Function runs server-side
// in the edge Worker runtime, so ITS OWN outbound fetch to RWS has no such
// restriction. This is the same trick Nautinect's own backend proxy
// already uses (confirmed by reading their app.js directly, see Claude.md).
//
// Route: GET /api/rws-waterlevel?code=<location code>&type=meting|verwachting&days=<N>
//   code: RWS location code, e.g. "harlingen.waddenzee" (NOT the short
//         SEATALK-style codes used elsewhere in this project -- see
//         extract_rws_waterlevel.py's own docstring for how these were found).
//   type: "meting" (measured, default) or "verwachting" (predicted).
//         Measured only ever covers the past; predicted also covers a
//         forward window (see futureDays below).
//   days: how many days INTO THE PAST to request (default 4, matching
//         extract_rws_waterlevel.py's own PAST_DAYS).
//   futureDays: for type=verwachting only, how many days FORWARD to
//         request (default 10, matching PAST_DAYS/FUTURE_DAYS there).
//
// Response: JSON array of [ms, cm] pairs (NAP-referenced), sorted
// ascending by time -- the exact shape _rwsSeriesFor()/_interpSeries()
// already expect from the old static files, so no client-side parsing
// logic needed to change, only where the data comes from.

const RWS_API = 'https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen';

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    // Real RWS measurements land every ~10 min; no point re-fetching more
    // often than that from the edge cache's point of view. Cloudflare's
    // edge cache still respects this per-URL (code+type+days combination).
    'Cache-Control': 'public, max-age=120'
  };
}

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const type = url.searchParams.get('type') === 'verwachting' ? 'verwachting' : 'meting';
  const days = Number(url.searchParams.get('days') || 4);
  const futureDays = Number(url.searchParams.get('futureDays') || 10);

  if (!code) {
    return new Response(JSON.stringify({ error: 'missing required "code" query param' }),
      { status: 400, headers: corsHeaders() });
  }

  const now = new Date();
  const begin = new Date(now.getTime() - days * 86400000);
  const end = type === 'verwachting' ? new Date(now.getTime() + futureDays * 86400000) : now;

  const body = {
    Locatie: { Code: code },
    AquoPlusWaarnemingMetadata: {
      AquoMetadata: {
        Compartiment: { Code: 'OW' },
        Grootheid: { Code: 'WATHTE' },
        ProcesType: type
      }
    },
    Periode: {
      Begindatumtijd: begin.toISOString().replace('.000Z', '.000+00:00'),
      Einddatumtijd: end.toISOString().replace('.000Z', '.000+00:00')
    }
  };

  let rwsResp;
  try {
    rwsResp = await fetch(RWS_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: 'upstream fetch failed: ' + err.message }),
      { status: 502, headers: corsHeaders() });
  }

  if (rwsResp.status === 204) {
    return new Response('[]', { headers: corsHeaders() });
  }
  if (!rwsResp.ok) {
    return new Response(JSON.stringify({ error: 'upstream RWS error ' + rwsResp.status }),
      { status: 502, headers: corsHeaders() });
  }

  const data = await rwsResp.json();
  if (!data.Succesvol || !data.WaarnemingenLijst) {
    return new Response('[]', { headers: corsHeaders() });
  }

  const out = [];
  for (const series of data.WaarnemingenLijst) {
    for (const m of series.MetingenLijst || []) {
      const waarde = m.Meetwaarde && m.Meetwaarde.Waarde_Numeriek;
      const tijdstip = m.Tijdstip;
      if (waarde == null || !tijdstip) continue;
      out.push([Date.parse(tijdstip), Math.round(waarde * 10) / 10]);
    }
  }
  out.sort((a, b) => a[0] - b[0]);

  return new Response(JSON.stringify(out), { headers: corsHeaders() });
}
