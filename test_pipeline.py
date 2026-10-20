"""Checks for the pipeline's meter matching. Run: python3 test_pipeline.py"""
import json
import os
import sys

import pipeline as P

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ('' if ok else f' (want {want!r})'))


# Street names
check('apostrophe', P.same_street('OFARRELL ST', "O'Farrell St"), True)
check('leading zero', P.same_street('3RD ST', '03rd St'), True)
check('The Embarcadero', P.same_street('EMBARCADERO SOUTH', 'The Embarcadero'), True)
check('frontage road', P.same_street('OCTAVIA ST', 'Octavia St, Frontage East'), True)
check('3rd is not 23rd', P.same_street('3RD ST', '23rd St'), False)
check('cross street', P.same_street('20TH ST', 'Illinois St'), False)

# Metered stretches on a 206 m block side
check('no meters', P.metered_pieces(206, []), [(0.0, 206, 0)])
check('corner meters only (23rd Ave at Irving)',
      [(round(a), round(b), n) for a, b, n in P.metered_pieces(206, [5, 12, 19, 26, 31])],
      [(0, 35, 5), (35, 206, 0)])
check('fully metered block, corner stubs folded in',
      [(round(a), round(b), n) for a, b, n in P.metered_pieces(206, list(range(20, 190, 7)))],
      [(0, 206, 25)])
check('short mid-block gap stays metered',
      len(P.metered_pieces(206, list(range(20, 90, 7)) + list(range(115, 190, 7)))), 1)

# Real data: 23rd Ave between Irving and Judah is only metered near Irving
path = os.path.join(P.OUT, 'sf_blocksides.geojson')
if os.path.exists(path):
    for f in json.load(open(path))['features']:
        p = f['properties']
        if p['street'] == '23rd Ave' and p['limits'] == 'Irving St - Judah St' and p['blockside'] == 'East':
            metered = sum(b - a for a, b in p['metered_stretches_m'])
            check('23rd Ave Irving-Judah east: metered under 50 m', metered < 50, True)

print(f'{fails} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
