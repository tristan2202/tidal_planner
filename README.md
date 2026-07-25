# Tidal Router

A free, browser-based pre-departure planning tool for the Netherlands +
Germany Wadden Sea. Draw a route on the chart and it finds the departure
time that best rides the tidal stream (minimizing passage time and adverse
current) while keeping the boat's draft clear of the bottom the whole way,
at the actual water depth the boat would see at the time it's actually
there.

**Live at <https://tidal-planner.com>** — no install, no account, free.

---

## ⚠️ Disclaimer — read before using

**This tool is for passage *planning* only. It is not a navigational aid
and must never be used as the sole or primary means of navigation.**

- All data shown (current, water level, tide timing, and especially charted
  depth) is derived from third-party sources listed below, on the schedule
  described below. It may be **stale, incomplete, or wrong** — see
  "Known limitations" for specific, disclosed gaps.
- **Always cross-check against official, up-to-date nautical charts,
  Notices to Mariners, tide tables, and pilotage guidance from your
  national hydrographic authority** before and during any passage. This
  tool does not replace them and is not corrected against real-time
  Notices to Mariners.
- The Wadden Sea's channels and sandbanks shift over time (storms, dredging,
  natural drift). A channel shown as safe here may no longer be safe, and
  a channel shown as shallow/blocked may since have been dredged or
  changed.
- This is a hobby project, not a certified or professionally audited
  navigation product. It has no affiliation with, and is not endorsed by,
  RWS, BSH, BAW, or any hydrographic office.
- **No warranty of any kind is given or implied** — not of accuracy,
  completeness, fitness for a particular purpose, or availability. The
  author(s) accept **no liability whatsoever** for any loss, damage,
  injury, grounding, delay, or other consequence arising from use of this
  tool, its data, or its output, to the fullest extent permitted by law.
- **Use entirely at your own risk.** You, the skipper, remain solely
  responsible for the safe navigation of your vessel at all times.

---

## What it does

- Draw a route by clicking waypoints on the chart (drag to move, click a
  waypoint to delete, click the line to insert a point).
- **Live mode**: scrub a "map time" control and see the route's current,
  depth, and tidal-curve information at that exact instant.
- **Optimizer mode**: pick a real calendar date, and it sweeps a ±12.5 h
  window around it to recommend either the safest departure (if depth is
  ever a binding constraint anywhere along the route) or the fastest
  departure (if the whole route stays comfortably clear of the bottom
  regardless of tide).
- Covers the full Netherlands + Germany Wadden Sea coastal strip (roughly
  Den Helder to the Danish border).

## Data sources, and what they don't cover

| Data | Source | Coverage / notes |
|---|---|---|
| Tidal current + water level | [TrilaWatt Hydrodynamik](https://trilawatt.eu/en/) (BAW/mFUND, CC-BY 4.0) | A **2022 model run**, not a live forecast — one representative year's tidal cycle, keyed by tidal phase. Won't reflect unusual wind-driven surge/set or any change since 2022. |
| Charted depth (Netherlands) | RWS official ENC (`SOUNDG`/`DEPARE`) via [vaarweginformatie.nl](https://www.vaarweginformatie.nl/) | Real official chart data, RWS-corrected roughly weekly via Notices to Mariners upstream — but this app's own copy is a periodic snapshot, not live (see below). ~72% of the Dutch domain has official coverage; the remaining ~28% (mostly open sea north of the barrier islands) falls back to the older TrilaWatt model bathymetry. |
| Charted depth (Germany) | BSH public WMS data, via the [quantenschaum/mapping](https://github.com/quantenschaum/mapping/tree/bsh-data) community mirror (GeoNutzV license) | Sparser sounding density than the Dutch side; refreshed on that project's own irregular schedule, not RWS's weekly cadence. |
| Chart tiles (visual basemap) | [freenauticalchart.net](https://freenauticalchart.net/) (CC0, BSH/RWS-derived) | Visual reference only — **explicitly not for navigation** per its own license terms, same as this whole tool. |
| Tide predictions, Netherlands (HW/LW timing) | RWS [waterinfo.rws.nl](https://waterinfo.rws.nl/) astronomical API | Real per-station predictions, Harlingen used as this app's own reference clock. |
| Tide predictions, Germany (HW/LW timing) | [gezeiten.bsh.de](https://gezeiten.bsh.de/) | Real predictions for 5 German stations (Borkum, Norderney, Wilhelmshaven, Cuxhaven, List auf Sylt). |
| Measured water level, Netherlands | RWS [waterinfo.rws.nl](https://waterinfo.rws.nl/) | Fetched live on every page load. |
| Measured water level, Germany | [PEGELONLINE](https://www.pegelonline.wsv.de/) (WSV) | Fetched live on every page load, directly from the browser. |

## Known limitations

- **Germany's departure-time optimizer borrows the Netherlands' (Harlingen)
  real-calendar tidal reference clock**, not its own. The underlying
  current/depth *data* for German routes is genuine BSH/TrilaWatt data, but
  the *moment in the tidal cycle* it's evaluated against may not exactly
  match Germany's own true tidal state at that instant.
- **Routes crossing the Netherlands/Germany border are not blended** —
  there is a visible seam where the two countries' independent models meet.
- **German harbour timing offsets are not yet researched** (Netherlands'
  stations have real, sourced HW-timing corrections relative to the
  reference station; Germany's do not yet).
- **This is a planning tool, not a live forecast** for current/water level
  magnitude — see the TrilaWatt row above.
- Depth values are smoothed/composited from multiple official and model
  sources with different resolutions and vintages; a single very recent,
  very local change (e.g. a storm reshaping a channel overnight) will not
  be reflected until the underlying data is refreshed and this app's data
  is re-extracted.

## How current is the data, right now?

- **Measured water level** (both countries): genuinely live, refetched on
  every page load.
- **Tide predictions (HW/LW timing)**: real astronomical predictions,
  refreshed periodically by the maintainer (Netherlands: every 1–2 weeks,
  limited by a rolling ~16-day upstream window; Germany: roughly once a
  year, since the upstream source publishes a full year+ at a time).
- **Charted depth**: refreshed occasionally, not on a fixed schedule — see
  "Data sources" above for each side's upstream update cadence.
- **Tidal current/water-level *model***: tied to a single fixed model year
  (2022) and only re-extracted if the source project (TrilaWatt/BAW)
  publishes an actual new model version — this is a rare, announced event,
  not something re-running the extraction on the same source data would
  improve.

## Running locally

Requires a local static server (data files are loaded via `fetch()`, which
browsers block under `file://`):

```
python -m http.server 8000
```

Then open `http://localhost:8000/`. First load takes ~20–30s (fetches the
full current/depth/water-level grids for the whole NL+Germany domain).

## License

Source code is MIT-licensed — see `LICENSE`. Chart/data licenses are as
noted per source above — this project does not claim ownership of any
third-party data it displays.
