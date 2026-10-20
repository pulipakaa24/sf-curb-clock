#!/usr/bin/env python3
"""San Francisco 72-hour street parking map pipeline.

    python3 pipeline.py              # build citywide from raw/ (already downloaded)
    python3 pipeline.py --download   # fetch fresh raw/ files from DataSF first
    python3 pipeline.py --neighborhoods "Inner Sunset,Sunset/Parkside"   # build a subset

Reads   raw/sweeping.geojson, raw/regulations.geojson, raw/neighborhoods.geojson,
        raw/meters.geojson (optional)
Writes  out/sf_blocksides.geojson       block sides + sweeping rows + regulations + meters
        out/sf_blocksides.csv           one row per block side, for scanning outside the map
        out/sf_regulations.geojson      every clipped regulation, flagged matched/unmatched
        out/match_report.txt            join diagnostics
        sf_72hr_map.html                self-contained map (data, engine and Leaflet inlined)

Standard library only. Geometry is done in a local equirectangular projection (metres),
which is accurate to well under 1% across the city.
"""
import argparse
import csv
import datetime
import json
import math
import os
import sys
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, 'raw')
OUT = os.path.join(HERE, 'out')
VENDOR = os.path.join(HERE, 'vendor')

DATASETS = {
    'sweeping.geojson': 'yhqp-riqs',
    'regulations.geojson': 'hi6h-neyh',
    'neighborhoods.geojson': 'j2bu-swwd',
    'meters.geojson': '8vzz-qzz9',
}
HOSTS = ['https://data.sfgov.org', 'https://data.sf.gov']

METER_MATCH_M = 10.0                 # a meter belongs to the nearest side line within this distance
GRID_M = 150.0                       # spatial index cell size
SIDE_OFFSET_M = 6.0                  # draw each sweeping side this far from the centerline
MATCH_DIST_M = 6.5                   # regulation must be within this of the offset side line
MATCH_ANGLE_DEG = 25.0
COMPASS_MIN_DOT = 0.35               # below this the compass side is ambiguous -> use L/R

LAT0 = 37.75
KX = 111320.0 * math.cos(math.radians(LAT0))
KY = 110540.0
LON0 = -122.48


# ---------------------------------------------------------------- geometry helpers
def to_m(p):
    return ((p[0] - LON0) * KX, (p[1] - LAT0) * KY)


def to_ll(q):
    return [round(q[0] / KX + LON0, 6), round(q[1] / KY + LAT0, 6)]


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def length(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def point_along(pts, frac):
    """Point and unit direction at a fraction of the polyline length."""
    target = length(pts) * frac
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)
        if d > 0 and target <= d:
            t = target / d
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
        target -= d
    a, b = pts[-2], pts[-1]
    d = math.dist(a, b) or 1.0
    return b, ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def offset_line(pts, dist):
    """Offset a polyline to its left by dist metres (negative = right), mitred at vertices."""
    normals = []
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b) or 1.0
        normals.append((-(b[1] - a[1]) / d, (b[0] - a[0]) / d))
    out = []
    for i, p in enumerate(pts):
        if i == 0:
            n = normals[0]
        elif i == len(pts) - 1:
            n = normals[-1]
        else:
            n1, n2 = normals[i - 1], normals[i]
            n = (n1[0] + n2[0], n1[1] + n2[1])
            ln = math.hypot(*n) or 1.0
            cos_half = max(0.5, ln / 2)            # cap mitre length at 2x
            n = (n[0] / ln / cos_half, n[1] / ln / cos_half)
        out.append((p[0] + n[0] * dist, p[1] + n[1] * dist))
    return out


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_multipolygon(lon, lat, mp):
    for poly in mp:
        if point_in_ring(lon, lat, poly[0]) and not any(point_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


COMPASS = {
    'north': (0, 1), 'south': (0, -1), 'east': (1, 0), 'west': (-1, 0),
    'northeast': (0.7071, 0.7071), 'northwest': (-0.7071, 0.7071),
    'southeast': (0.7071, -0.7071), 'southwest': (-0.7071, -0.7071),
}
WEEKDAY = {'su': 0, 'mo': 1, 'tu': 2, 'we': 3, 'th': 4, 'fr': 5, 'sa': 6}


# ---------------------------------------------------------------- download
def download():
    os.makedirs(RAW, exist_ok=True)
    for fname, ident in DATASETS.items():
        limit = 1000 if fname == 'neighborhoods.geojson' else 100000
        if fname == 'meters.geojson':
            limit = 60000
        for host in HOSTS:
            url = f'{host}/resource/{ident}.geojson?$limit={limit}'
            try:
                print('GET', url)
                with urllib.request.urlopen(url, timeout=120) as r:
                    data = r.read()
                json.loads(data)
                with open(os.path.join(RAW, fname), 'wb') as f:
                    f.write(data)
                break
            except Exception as e:  # try the next host
                print('  failed:', e)
        else:
            sys.exit(f'Could not download {fname}')
    with open(os.path.join(RAW, 'DOWNLOADED_AT.txt'), 'w') as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') + '\n')


# ---------------------------------------------------------------- load + clip
def load(name):
    with open(os.path.join(RAW, name)) as f:
        return json.load(f)['features']


def load_neighborhoods(wanted=None):
    """[(name, multipolygon, bbox)] for the analysis neighborhoods (optionally a subset)."""
    out = []
    for f in load('neighborhoods.geojson'):
        name = f['properties'].get('nhood')
        if wanted and name not in wanted:
            continue
        g = f['geometry']
        mp = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
        pts = [c for poly in mp for c in poly[0]]
        out.append((name, mp, (min(c[0] for c in pts), min(c[1] for c in pts), max(c[0] for c in pts), max(c[1] for c in pts))))
    if wanted and len(out) != len(wanted):
        sys.exit(f'Unknown neighborhood(s): {sorted(set(wanted) - {n for n, _, _ in out})}')
    return out


def make_locator(nhoods):
    def locate(lon, lat):
        for name, mp, b in nhoods:
            if b[0] <= lon <= b[2] and b[1] <= lat <= b[3] and point_in_multipolygon(lon, lat, mp):
                return name
        return None
    return locate


class Grid:
    """Bucket items by the metre-grid cells their bounding box touches."""
    def __init__(self):
        self.cells = defaultdict(list)

    def _span(self, xs, ys, pad):
        return (range(int((min(xs) - pad) // GRID_M), int((max(xs) + pad) // GRID_M) + 1),
                range(int((min(ys) - pad) // GRID_M), int((max(ys) + pad) // GRID_M) + 1))

    def add(self, item, pts, pad=0.0):
        cx, cy = self._span([p[0] for p in pts], [p[1] for p in pts], pad)
        for i in cx:
            for j in cy:
                self.cells[(i, j)].append(item)

    def near(self, pts, pad=0.0):
        cx, cy = self._span([p[0] for p in pts], [p[1] for p in pts], pad)
        seen = set()
        for i in cx:
            for j in cy:
                for it in self.cells.get((i, j), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        yield it


def midpoint_ll(coords):
    pts = [to_m(p) for p in coords]
    q, _ = point_along(pts, 0.5)
    return to_ll(q)


def as_int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- build
def build(wanted=None):
    nhoods = load_neighborhoods(wanted)
    locate = make_locator(nhoods)
    report = []
    log = lambda *a: report.append(' '.join(str(x) for x in a))
    log('Study area:', ', '.join(sorted(wanted)) if wanted else f'all {len(nhoods)} analysis neighborhoods')

    # --- sweeping: group rows into block sides keyed by (cnn, L/R)
    sides = {}
    geom_mismatch = 0
    skipped_null = 0
    for f in load('sweeping.geojson'):
        g, p = f['geometry'], f['properties']
        if not g:
            skipped_null += 1
            continue
        coords = g['coordinates']
        key = (p['cnn'], p['cnnrightleft'])
        if key not in sides:
            mid = midpoint_ll(coords)
            nh = locate(*mid)
            if nh is None:
                continue
        elif sides[key] is None:
            continue
        key = (p['cnn'], p['cnnrightleft'])
        wd = WEEKDAY.get(str(p.get('weekday') or '')[:2].lower())
        row = {
            'wd': wd, 'from': as_int(p.get('fromhour'), 0), 'to': as_int(p.get('tohour'), 0),
            'weeks': [p.get(f'week{i}') == '1' for i in range(1, 6)],
            'holidays': p.get('holidays') == '1',
        }
        s = sides.get(key)
        if s is None:
            s = sides[key] = {
                'cnn': p['cnn'], 'lr': p['cnnrightleft'], 'street': p.get('corridor') or '',
                'limits': ' '.join((p.get('limits') or '').split()), 'blockside': p.get('blockside'),
                'coords': coords, 'mid': mid, 'area': nh, 'sweeps': [], 'ids': [], 'meters': 0,
            }
        elif s['coords'] != coords:
            geom_mismatch += 1
        if wd is not None and not any(r == row for r in s['sweeps']):
            s['sweeps'].append(row)
        s['ids'].append(p.get('blocksweepid'))
    holiday_rows = sum(1 for f in load('sweeping.geojson') if str(f['properties'].get('weekday') or '').lower().startswith('hol'))
    log(f'Sweeping: {len(sides)} block sides in study area ({holiday_rows} citywide "Holiday" rows ignored); {skipped_null} rows citywide had no geometry; '
        f'{geom_mismatch} rows had geometry differing from the first row of their side')

    # --- decide which side of the centerline each block side is on, then offset
    agree = Counter()
    for s in sides.values():
        pts = [to_m(c) for c in s['coords']]
        dx, dy = pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]
        d = math.hypot(dx, dy) or 1.0
        left_normal = (-dy / d, dx / d)
        lr_sign = 1 if s['lr'] == 'L' else -1
        comp = COMPASS.get(str(s['blockside'] or '').lower())
        sign, how = lr_sign, 'L/R'
        if comp:
            dot = left_normal[0] * comp[0] + left_normal[1] * comp[1]
            if abs(dot) >= COMPASS_MIN_DOT:
                sign, how = (1 if dot > 0 else -1), 'compass'
                agree['compass agrees with L/R' if sign == lr_sign else 'compass DISAGREES with L/R'] += 1
            else:
                agree['compass ambiguous, used L/R'] += 1
        else:
            agree['no compass side, used L/R'] += 1
        s['pts'] = pts
        s['off'] = offset_line(pts, sign * SIDE_OFFSET_M)
        s['how'] = how
    log('Side placement:', dict(agree))

    # --- regulations
    regs = []
    for f in load('regulations.geojson'):
        g, p = f['geometry'], f['properties']
        if not g:
            continue
        lines = g['coordinates'] if g['type'] == 'MultiLineString' else [g['coordinates']]
        allpts = [c for l in lines for c in l]
        mid = midpoint_ll(max(lines, key=len))
        nh = locate(*mid)
        if nh is None:
            continue
        rpp = [a for a in (p.get('rpparea1'), p.get('rpparea2'), p.get('rpparea3')) if a]
        regs.append({
            'id': p.get('objectid'),
            'regulation': p.get('regulation'), 'days': p.get('days'), 'hours': p.get('hours'),
            'hrs_begin': p.get('hrs_begin'), 'hrs_end': p.get('hrs_end'), 'hrlimit': p.get('hrlimit'),
            'rpp': rpp, 'regdetails': p.get('regdetails'), 'exceptions': p.get('exceptions'),
            'neighborhood': p.get('analysis_neighborhood'), 'length_ft': p.get('length_ft'),
            'last_edited': (p.get('last_edited_date') or '')[:10],
            'area': nh, 'lines': lines, 'mlines': [[to_m(c) for c in l] for l in lines],
            'matched': 0,
        })
    log(f'Regulations: {len(regs)} records in study area;', dict(Counter(r["regulation"] for r in regs)))

    # --- join: a regulation applies to a side if a sample point (25/50/75%) of either line lies
    # within MATCH_DIST_M of the other line and the two run roughly parallel. Checking both
    # directions catches regulations much shorter than the block (e.g. Taraval storefront runs).
    min_cos = math.cos(math.radians(MATCH_ANGLE_DEG))

    def near_parallel(samples, lines):
        for q, dirv in samples:
            for line in lines:
                for a, c in zip(line, line[1:]):
                    if seg_dist(q, a, c) > MATCH_DIST_M:
                        continue
                    L = math.dist(a, c) or 1.0
                    if abs((c[0] - a[0]) / L * dirv[0] + (c[1] - a[1]) / L * dirv[1]) >= min_cos:
                        return True
        return False

    grid = Grid()
    for ri, r in enumerate(regs):
        r['idx'] = ri
        r['samples'] = [point_along(l, fr) for l in r['mlines'] if length(l) > 0 for fr in (0.25, 0.5, 0.75)]
        grid.add(r, [p for l in r['mlines'] for p in l], pad=MATCH_DIST_M)
    for s in sides.values():
        s['regs'] = []
        samples = [point_along(s['off'], fr) for fr in (0.25, 0.5, 0.75)]
        for r in grid.near(s['off'], pad=MATCH_DIST_M):
            if near_parallel(samples, r['mlines']) or near_parallel(r['samples'], [s['off']]):
                s['regs'].append(r['idx'])
                r['matched'] += 1
        s['regs'].sort()
    unmatched = [r for r in regs if not r['matched']]
    log(f'Join: {sum(1 for s in sides.values() if s["regs"])} block sides matched >=1 regulation; '
        f'{len(regs) - len(unmatched)} regulations matched, {len(unmatched)} unmatched (drawn on their own)')
    log('Regulations matched per side:', dict(Counter(len(s['regs']) for s in sides.values())))
    log('Sides matched per regulation:', dict(sorted(Counter(r['matched'] for r in regs).items())))

    # --- meters: each active meter goes to the nearest offset side line within METER_MATCH_M
    mpath = os.path.join(RAW, 'meters.geojson')
    if os.path.exists(mpath):
        sgrid = Grid()
        for s in sides.values():
            sgrid.add(s, s['off'], pad=METER_MATCH_M)
        stats = Counter()
        for f in load('meters.geojson'):
            p, g = f['properties'], f['geometry']
            active = str(p.get('active_meter_flag') or p.get('active_met') or '').upper()
            if active not in ('M', 'T'):
                stats['inactive or removed'] += 1
                continue
            if str(p.get('on_offstreet_type') or 'ON').upper() == 'OFF':
                stats['off-street lot'] += 1
                continue
            if g and g.get('type') == 'Point':
                lon, lat = g['coordinates'][:2]
            else:
                try:
                    lon, lat = float(p['longitude']), float(p['latitude'])
                except (KeyError, TypeError, ValueError):
                    stats['no location'] += 1
                    continue
            q = to_m((lon, lat))
            best, bd = None, METER_MATCH_M
            for s in sgrid.near([q], pad=METER_MATCH_M):
                d = min(seg_dist(q, a, b) for a, b in zip(s['off'], s['off'][1:]))
                if d < bd:
                    best, bd = s, d
            if best is None:
                stats['active, no side within %dm' % METER_MATCH_M] += 1
            else:
                best['meters'] += 1
                stats['active, matched'] += 1
        log('Meters:', dict(stats), f'-> {sum(1 for s in sides.values() if s["meters"])} metered block sides')
    else:
        log('Meters: raw/meters.geojson not found, metered curbs are NOT marked')

    return sides, regs, unmatched, report


# ---------------------------------------------------------------- outputs
def write_outputs(sides, regs, unmatched, report):
    os.makedirs(OUT, exist_ok=True)
    downloaded = open(os.path.join(RAW, 'DOWNLOADED_AT.txt')).read().strip() if os.path.exists(os.path.join(RAW, 'DOWNLOADED_AT.txt')) else 'unknown'

    reg_public = lambda r: {k: r[k] for k in ('id', 'regulation', 'days', 'hours', 'hrs_begin', 'hrs_end', 'hrlimit', 'rpp',
                                              'regdetails', 'exceptions', 'neighborhood', 'length_ft', 'last_edited')}
    order = sorted(sides.values(), key=lambda s: (s['street'], s['limits'], s['lr']))

    # full GeoJSON of block sides
    feats = []
    for s in order:
        feats.append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [to_ll(q) for q in s['off']]},
                      'properties': {'cnn': s['cnn'], 'side_lr': s['lr'], 'blockside': s['blockside'], 'street': s['street'],
                                     'limits': s['limits'], 'neighborhood': s['area'], 'side_placed_by': s['how'], 'active_meters': s['meters'],
                                     'blocksweepids': s['ids'], 'sweeps': s['sweeps'],
                                     'regulations': [reg_public(regs[i]) for i in s['regs']]}})
    with open(os.path.join(OUT, 'sf_blocksides.geojson'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'downloaded_at': downloaded, 'features': feats}, f)

    with open(os.path.join(OUT, 'sf_regulations.geojson'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'MultiLineString', 'coordinates': r['lines']},
             'properties': {**reg_public(r), 'matched_sides': r['matched']}} for r in regs]}, f)

    # CSV summary
    wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    ords = ['1st', '2nd', '3rd', '4th', '5th']
    def sweep_txt(r):
        w = 'every' if all(r['weeks']) else ' & '.join(o for o, on in zip(ords, r['weeks']) if on)
        return f"{w} {wd[r['wd']]} {r['from']:02d}:00-{r['to']:02d}:00"
    def merge_sweeps(rows):
        merged = {}
        for r in rows:
            k = (r['wd'], r['from'], r['to'])
            merged[k] = [a or b for a, b in zip(merged.get(k, [False] * 5), r['weeks'])]
        return [{'wd': k[0], 'from': k[1], 'to': k[2], 'weeks': w} for k, w in sorted(merged.items(), key=lambda kv: ((kv[0][0] + 6) % 7, kv[0][1]))]
    def reg_txt(r):
        bits = [r['regulation'] or '?', r['days'] or 'every day', r['hours'] or 'all day']
        if r['hrlimit'] and r['hrlimit'] != '0':
            bits.append(f"{r['hrlimit']}h limit")
        if r['rpp']:
            bits.append('RPP ' + '/'.join(r['rpp']))
        return ' '.join(bits)
    with open(os.path.join(OUT, 'sf_blocksides.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['street', 'limits', 'side', 'side_lr', 'neighborhood', 'cnn', 'sweeping_schedule', 'regulations', 'active_meters', 'source'])
        for s in order:
            w.writerow([s['street'], s['limits'], s['blockside'], s['lr'], s['area'], s['cnn'],
                        '; '.join(sweep_txt(r) for r in merge_sweeps(s['sweeps'])),
                        '; '.join(reg_txt(regs[i]) for i in s['regs']), s['meters'], 'sweeping'])
        for r in unmatched:
            w.writerow(['', '', '', '', r['area'], '', '', reg_txt(r), '', f"regulation only (objectid {r['id']})"])

    with open(os.path.join(OUT, 'match_report.txt'), 'w') as f:
        f.write('\n'.join(report) + '\n')

    # compact payload for the map
    used = sorted({i for s in sides.values() for i in s['regs']} | {r['idx'] for r in unmatched})
    nh_names = sorted({s['area'] for s in sides.values()} | {r['area'] for r in unmatched})
    nh_index = {n: i for i, n in enumerate(nh_names)}
    remap = {old: new for new, old in enumerate(used)}
    compact_regs = [{k: regs[i][k] for k in ('regulation', 'days', 'hours', 'hrs_begin', 'hrs_end', 'hrlimit', 'rpp', 'regdetails', 'last_edited')}
                    for i in used]
    nh_bounds = {}
    for s in sides.values():
        b = nh_bounds.setdefault(s['area'], [90, 180, -90, -180])
        for lon, lat in s['coords']:
            b[0] = min(b[0], lat); b[1] = min(b[1], lon); b[2] = max(b[2], lat); b[3] = max(b[3], lon)
    rnd = lambda pts: [[round(c[0], 5), round(c[1], 5)] for c in pts]
    payload = {
        'downloaded_at': downloaded,
        'regs': compact_regs,
        'has_meters': os.path.exists(os.path.join(RAW, 'meters.geojson')),
        'nh': nh_names,
        'nh_bounds': nh_bounds,
        'sides': [{'s': s['street'], 'l': s['limits'], 'b': s['blockside'] or s['lr'], 'a': nh_index[s['area']], 'm': s['meters'],
                   'w': [[r['wd'], r['from'], r['to'], sum(1 << i for i, on in enumerate(r['weeks']) if on)] for r in s['sweeps']],
                   'r': [remap[i] for i in s['regs']],
                   'g': rnd([to_ll(q) for q in s['off']])} for s in order],
        'orphans': [{'r': remap[r['idx']], 'a': nh_index[r['area']],
                     'g': [rnd(l) for l in r['lines']]} for r in unmatched],
        'raw': [{'r': remap[i], 'g': [rnd(l) for l in regs[i]['lines']]} for i in used],
        'rpp_areas': sorted({a for i in used for a in regs[i]['rpp']}),
    }
    return payload


def build_html(payload):
    tpl = open(os.path.join(HERE, 'map_template.html')).read()
    engine = open(os.path.join(HERE, 'engine.js')).read()
    leaflet_js = open(os.path.join(VENDOR, 'leaflet.js')).read()
    leaflet_css = open(os.path.join(VENDOR, 'leaflet.css')).read()
    data = json.dumps(payload, separators=(',', ':')).replace('</', '<\\/')
    html = (tpl.replace('/*__LEAFLET_CSS__*/', leaflet_css)
               .replace('/*__LEAFLET_JS__*/', leaflet_js)
               .replace('/*__ENGINE_JS__*/', engine)
               .replace('/*__DATA__*/null', data))
    # fragment (for hosts that supply their own <head>), then a complete standalone document
    with open(os.path.join(OUT, 'sf_72hr_map.fragment.html'), 'w') as f:
        f.write(html)
    doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
           '<style>html,body{margin:0}</style>\n</head>\n<body>\n' + html + '\n</body>\n</html>\n')
    path = os.path.join(HERE, 'sf_72hr_map.html')
    with open(path, 'w') as f:
        f.write(doc)
    return path, len(doc.encode())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--download', action='store_true', help='fetch fresh raw data from DataSF first')
    ap.add_argument('--neighborhoods', help='comma-separated analysis neighborhoods to build (default: all of SF)')
    args = ap.parse_args()
    if args.download:
        download()
    wanted = {n.strip() for n in args.neighborhoods.split(',') if n.strip()} if args.neighborhoods else None
    sides, regs, unmatched, report = build(wanted)
    payload = write_outputs(sides, regs, unmatched, report)
    path, size = build_html(payload)
    print('\n'.join(report))
    print(f'Wrote out/ and {os.path.basename(path)} ({size / 1e6:.2f} MB)')


if __name__ == '__main__':
    main()
