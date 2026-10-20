# SF Curb Clock

An interactive map of San Francisco street parking. Pick an arrival time and it colors every block side by how long a car can stay there (up to 72 hours), and says exactly what ends the stay. It started as a Sunset District map and now covers all 41 analysis neighborhoods.

Open `sf_72hr_map.html` in any browser. Everything the map needs is inside that one file: the data, the rule engine and Leaflet. Only the background maps (Esri Streets, Light gray, Dark gray, Satellite) need internet; without them you still get every curb, with street names drawn from the data.

## Files

| Path | What it is |
|---|---|
| `sf_72hr_map.html` | The map: a complete, self-contained page (6.1 MB) |
| `out/sf_72hr_map.fragment.html` | The same page without `<html>/<head>`, for hosts that supply their own |
| `out/sf_blocksides.geojson` | 22,538 block sides (lines offset to their curb) with sweeping rows, matched regulations and meter counts |
| `out/sf_blocksides.csv` | One row per block side. Regulation-only curbs are at the end |
| `out/sf_regulations.geojson` | All 7,778 regulations, each with how many sides it matched |
| `out/match_report.txt` | Join diagnostics from the last run |
| `pipeline.py` | Download → clip → offset → join → write everything above (standard library only, ~4 s citywide) |
| `engine.js` | The time-window rules, shared by the page and the tests |
| `test_engine.js` | The brief's test cases plus citywide edge cases |
| `map_template.html` | Page template the pipeline fills in |
| `raw/` | Raw DataSF downloads used for this build |
| `vendor/` | Leaflet 1.9.4 (BSD-2, license included) |

## Run it

```bash
python3 pipeline.py
```

That rebuilds `out/` and the map from `raw/`. Other options:

```bash
python3 pipeline.py --neighborhoods "Inner Sunset,Sunset/Parkside"
```

```bash
python3 pipeline.py --download
```

```bash
node test_engine.js
```

`--neighborhoods` builds a smaller map of just those areas. `--download` fetches fresh data first and needs network access to data.sfgov.org.

## Data

| Dataset | DataSF ID | Used |
|---|---|---|
| Street Sweeping Schedule | `yhqp-riqs` | 37,878 rows → 22,538 block sides |
| Parking regulations (except non-metered color curb) | `hi6h-neyh` | 7,778 with geometry |
| Analysis Neighborhoods | `j2bu-swwd` | 41 polygons, for clipping and the neighborhood picker |
| Parking Meters | `8vzz-qzz9` | 38,740 meters → 29,338 active on-street meters on 3,662 block sides |

**Downloaded 2026-10-19 06:43 UTC** (`raw/DOWNLOADED_AT.txt`); meters downloaded 2026-10-19 21:18 UTC. The publisher last modified the regulations data on 2026-06-13, and many records were last edited in 2017.

**Meters:** if `raw/meters.geojson` is missing, the build still works, but the page shows a warning that metered curbs appear free. The meter list is current (weekly), but DataSF's [Meter Operating Schedules](https://data.sf.gov/Transportation/Meter-Operating-Schedules/6cqg-dxku) table was last updated in 2014. So any curb with an active meter within 10 m gets one assumed rule: metered every day 9 AM–6 PM. That's conservative for meters that skip Sundays, and wrong for meters with longer hours.

Meter matching uses active meters only (flag `M` or `T`, per DataSF) and skips the 581 active meters in off-street lots. Every cap color counts, including yellow commercial and red truck meters. 1,207 active meters (4%) have no curb line within 10 m, because their streets are missing from the sweeping data. Most are in Mission Bay (Terry A Francois Blvd, Townsend, Long Bridge), plus Iowa St and Portola Dr. Those curbs show no line at all.

## How it works

**Rules** (`engine.js`). For each block side, the max stay is the time from arrival to the first violation, capped at the stay length H (1–72 h):

- **Street sweeping:** parked during any part of a sweep is a violation. Week of month is `ceil(day / 7)`. The 824 "Holiday" rows are ignored.
- **Time limits:** the clock starts at the later of arrival and the start of the window. The stay ends when the limit runs out inside that window. A matching RPP permit exempts you.
- **Pay or Permit:** a violation when paid hours start, unless you hold a permit for that area.
- **Metered:** a violation when the assumed meter hours start.
- **No oversized vehicles:** applies only when the oversized option is on.
- **No parking any time, No Stopping, No overnight, Limited no parking, Government permit:** a violation as soon as the window overlaps the stay.
- Times are naive local wall-clock time. The page warns when a stay spans a daylight-saving change.

**Parsing:**
- `days`: `M-S` is read as Mon–Sat, comma lists like `M, TH` are supported, and a missing value means every day.
- Hours come from `hrs_begin`/`hrs_end`, falling back to the `hours` text. `2400` is midnight; `0-0` or missing means all day.
- A missing `hrlimit` falls back to `regdetails` ("4 Hour Time Limit").

**Sides:** each sweeping side is moved 6 m toward its compass `blockside`. That agreed with the dataset's L/R flag on 22,106 of 22,114 sides (8 disagree; the compass wins). 424 sides with no compass side use L/R.

**Join:** regulation lines are drawn at the curb, so each one belongs to one side. A regulation matches a side when a point at 25, 50 or 75% along either line is within 6.5 m of the other and they run within 25° of parallel. A spatial grid keeps this fast.

- 6,808 of 7,778 regulations matched.
- Most of the 970 unmatched ones are more than 20 m from any swept street, mostly in Russian Hill (266), North Beach (166) and Nob Hill (133). Those streets are largely missing from the sweeping data: Russian Hill has 132 swept sides against 307 regulation records. These curbs are drawn dashed and treated as unswept, which is probably wrong.

## Tests

All 21 pass. The brief's seven (arrival Sun 2026-10-18 20:00, H = 72):

| Rule | Expected | Got |
|---|---|---|
| Time limited 2 h, M-F 8 AM–6 PM | 14 h | 14 h |
| Sweeping Tue 8–10 AM, all weeks | 36 h | 36 h |
| Sweeping Tue, week 1 only | 72 h | 72 h |
| Sweeping Tue, week 3 | 36 h | 36 h |
| No oversized 12 AM–6 AM, oversized vehicle | 4 h | 4 h |
| Same, not oversized | 72 h | 72 h |
| No parking any time | 0 h | 0 h |

The other 14 cover RPP exemption and a wrong-area permit, null days/hours, mid-sweep arrival, `M-S`, hours only in the text field, Pay or Permit with and without a permit, metered curbs, overnight bans, and `M, TH`.

## Spot checks: not yet done against signs

The brief asks for blocks to be compared with Street View and SFMTA's lookup. This build environment's policy blocks google.com and sfmta.com, so that hasn't been done. Every popup now has a Street View link to make it quick. Sunset blocks to check (arrival Sun 2026-10-18 8 PM, 72 h):

| Block side | Sweeping (data) | Regulations (data) | Map says | Posted signs |
|---|---|---|---|---|
| Taraval St, 29th–30th Ave, north | every Tue & Fri 7–8 AM | 1-hr limit M–Sa 7 AM–6 PM | 12 h, ends Mon 8 AM | ? |
| Taraval St, 28th–29th Ave, north | every Tue & Fri 7–8 AM | **none** | 35 h, ends Tue 7 AM | ? |
| Lawton St, 11th–12th Ave, north | 1st & 3rd Mon 8–10 AM | 2-hr limit M–F 8 AM–5 PM, RPP J | 12 h, ends Mon 8 AM | ? |
| Irving St, 19th–20th Ave, south | every Mon, Wed, Fri, Sat 2–6 AM | none | 6 h, ends Mon 2 AM | ? |
| 46th Ave, Judah–Kirkham, west | 1st, 3rd & 5th Tue noon–2 PM | none | 40 h, ends Tue noon | ? |
| Lower Great Hwy, Judah–Kirkham, west | 1st, 3rd & 5th Tue 1–3 PM | no oversized 12–6 AM | 41 h, ends Tue 1 PM | ? |

## Known limitations

These are also on the page.

- **Posted signs always win.** Regulation data is partly stale and unvetted.
- **Meters:** hours are assumed (daily 9 AM–6 PM). Meters on streets missing from the sweeping data (mostly Mission Bay) aren't shown. Without `raw/meters.geojson`, meters are missing entirely and the page says so.
- **Tow-away lanes** (commute-hour no stopping) are mostly missing. Read every sign downtown and on arterials.
- **Time limits are patchy on commercial streets.** In the Sunset, only 9 of 60 Taraval block sides have one.
- **Sweeping data has gaps,** notably Russian Hill, North Beach and Nob Hill. Regulation-only curbs there are treated as unswept.
- Color curbs, driveways, hydrants and corner/crosswalk clearance are not in the data.
- Temporary restrictions and street closures are not included.
- Bike-lane sweeping and holiday sweeping rules are ignored.
- A stay that crosses a daylight-saving change can be off by an hour.
- The 72-hour rule (CVC 22651(k)) applies even with a placard or permit, and enforcement is complaint-driven.
- Streets in none of the datasets have no line; check the posted signs there.
