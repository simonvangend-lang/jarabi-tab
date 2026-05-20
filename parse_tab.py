#!/usr/bin/env python3
"""
Parse a Derek Gripper-style guitar tab PDF into scores/<id>.json.

Usage:
  python3 parse_tab.py <path-to-pdf> [score-id]

If score-id is omitted, it's derived from the PDF filename
(lowercase, hyphenated, no extension).

Tuning (without capo): E B F# D A D (strings 0-5, high to low)
CAPO III → open MIDI: G4(67) D4(62) A3(57) F3(53) C3(48) F2(41)
"""
import pdfplumber, json, sys, os, re

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)
PDF = sys.argv[1]
if not os.path.exists(PDF):
    print(f"PDF not found: {PDF}")
    sys.exit(1)
SCORE_ID = (sys.argv[2] if len(sys.argv) >= 3
            else re.sub(r'[^a-z0-9_-]+', '-',
                        os.path.splitext(os.path.basename(PDF))[0].lower()).strip('-'))
ROOT = os.path.dirname(os.path.abspath(__file__))
SCORES_DIR = os.path.join(ROOT, 'scores')
os.makedirs(SCORES_DIR, exist_ok=True)
OUT_PATH = os.path.join(SCORES_DIR, f'{SCORE_ID}.json')

OPEN_MIDI = [67, 62, 57, 53, 48, 41]

def is_fret(t):
    try: n = int(t); return 0 <= n <= 22
    except: return False

# ── Auto-detect the fret-number font size for this PDF ────────────────────────
# Different PDFs use different point sizes (Jarabi=10.4, Tubaka=7.4). The fret
# font is whichever size occurs most often among digit characters across the
# whole document.
from collections import Counter
def detect_fret_size(pdf):
    sizes = Counter()
    for p in pdf.pages:
        for c in p.chars:
            if c['text'].isdigit() and c.get('non_stroking_color') != (1.0, 1.0, 1.0):
                sizes[round(c['size'], 1)] += 1
    if not sizes:
        return 10.4
    return sizes.most_common(1)[0][0]

with pdfplumber.open(PDF) as pdf:
    FRET_SIZE = detect_fret_size(pdf)
print(f'Detected fret font size: {FRET_SIZE}pt')

# Grace-cluster thresholds scale with font size (calibrated for 10.4pt)
GRACE_GAP_MAX  = 8.0  * (FRET_SIZE / 10.4)   # tight-cluster horizontal gap
GRACE_2DIG_W   = 13.5 * (FRET_SIZE / 10.4)   # 2-digit cluster width above which it's grace

all_notes    = []
all_stems    = []
all_beams    = []
all_grace    = []          # list of (measure_global_raw, string, frets, x0_first_digit, bar_x0, bar_x1)
measure_idx  = 0

# Per-page set of (round(x0,1)) values for digit chars that belong to a
# tight grace-note cluster. We need this BEFORE building visible_xs so that
# those characters are excluded from the regular-note parsing.
def find_grace_skip_positions(page):
    """Return a set of (round(x0,1), round(y,0)) tuples for digit chars that are
    part of tight grace clusters. Per-(x,y) so chord notes on other strings at the
    same x are not affected."""
    digits = [c for c in page.chars
              if c['text'].isdigit() and round(c['size'], 1) == FRET_SIZE
              and c.get('non_stroking_color') != (1.0, 1.0, 1.0)]
    rows = {}
    for c in digits:
        key = round(c['top'])
        rows.setdefault(key, []).append(c)
    skip = set()
    for arr in rows.values():
        arr.sort(key=lambda c: c['x0'])
        i = 0
        while i < len(arr):
            cl = [arr[i]]
            while i + 1 < len(arr) and arr[i+1]['x0'] - arr[i]['x1'] < GRACE_GAP_MAX:
                cl.append(arr[i+1]); i += 1
            if len(cl) >= 2:
                seq = ''.join(c['text'] for c in cl)
                x0 = cl[0]['x0']; x1 = cl[-1]['x1']; width = x1 - x0
                is_grace = (len(cl) >= 3
                            or (len(cl) == 2 and (int(seq) > 22 or width > GRACE_2DIG_W)))
                if is_grace:
                    for c in cl:
                        skip.add((round(c['x0'], 1), round(c['top'])))
            i += 1
    return skip

with pdfplumber.open(PDF) as pdf:
    for page_num, page in enumerate(pdf.pages):

        # ── Visible characters only (filter white-fill phantom markers) ──────
        visible_chars = [
            ch for ch in page.chars
            if ch.get('non_stroking_color') != (1.0, 1.0, 1.0)
        ]
        words       = page.extract_words(x_tolerance=3, y_tolerance=3)
        grace_skip  = find_grace_skip_positions(page)
        # (x, y) pairs of visible single-digit chars, minus grace-cluster ones
        visible_keys = set(
            (round(float(ch['x0']), 1), round(float(ch['top'])))
            for ch in visible_chars if is_fret(ch['text'])
        ) - grace_skip
        # Keep a simple x-only set for quick rejection (any char with this x exists somewhere)
        visible_xs = set(k[0] for k in visible_keys)
        vlines      = page.lines
        curves      = page.curves   # beam rectangles live here

        # ── Staff-line clustering into systems ───────────────────────────────
        hlines = sorted(
            [l for l in vlines
             if abs(l['top'] - l['bottom']) < 1.5 and l['width'] > 200],
            key=lambda l: l['top']
        )
        systems_h = []
        cluster   = []
        for hl in hlines:
            if cluster and hl['top'] - cluster[-1]['top'] > 30:
                if len(cluster) >= 5:
                    systems_h.append((
                        cluster[0]['top'], cluster[-1]['bottom'],
                        min(l['x0'] for l in cluster),
                        max(l['x1'] for l in cluster)
                    ))
                cluster = []
            cluster.append(hl)
        if len(cluster) >= 5:
            systems_h.append((
                cluster[0]['top'], cluster[-1]['bottom'],
                min(l['x0'] for l in cluster),
                max(l['x1'] for l in cluster)
            ))

        # ── Visible fret words ───────────────────────────────────────────────
        fret_words = []
        for w in words:
            if not is_fret(w['text']): continue
            x0 = float(w['x0']); y = float(w['top'])
            if page_num > 0 and y < 60: continue
            if page_num == 0 and x0 < 112 and w['text'] == '4': continue
            if round(x0, 1) not in visible_xs: continue
            # Reject if this (x, y) is part of a grace cluster — but only when the
            # word's first digit matches a grace-skip key (chord notes on other
            # strings at the same x stay visible).
            if (round(x0, 1), round(y)) not in visible_keys: continue
            # Use horizontal centre for stem matching (x0 is left edge, biases left)
            x_mid = (x0 + float(w['x1'])) / 2
            fret_words.append((x_mid, y, int(w['text'])))

        # ── Find grace clusters per page (for attaching as grace notes later) ──
        page_digits = [c for c in page.chars
                       if c['text'].isdigit() and round(c['size'], 1) == FRET_SIZE
                       and c.get('non_stroking_color') != (1.0, 1.0, 1.0)]

        # Per-bar stem data, so the grace pass can snap to actual stem positions
        # instead of using a uniform-x heuristic.
        bar_stems = {}   # global_m -> [(stem_x, stem_bim), ...]

        # ── Process each system ──────────────────────────────────────────────
        for sys_top, sys_bot, staff_x0, staff_x1 in systems_h:

            # Min barline height is proportional to staff height (was hardcoded
            # 35pt, which excluded shorter staves in other PDFs)
            staff_height = sys_bot - sys_top
            barline_min_h = staff_height * 0.7

            # Barlines
            barlines_x = sorted(set(
                round(l['x0'], 1)
                for l in vlines
                if (abs(l['x0'] - l['x1']) < 2
                    and l['height'] >= barline_min_h
                    and l['top'] >= sys_top - 5
                    and l['bottom'] >= sys_bot - 5   # must reach near staff bottom
                    and l['bottom'] <= sys_bot + 5)
            ))
            if not barlines_x:
                continue

            # Merge barlines within 8pt — double/repeat barlines produce two
            # lines very close together; keep only the rightmost of each pair.
            merged_bx = []
            for bx in barlines_x:
                if merged_bx and bx - merged_bx[-1] <= 8:
                    merged_bx[-1] = bx   # keep rightmost
                else:
                    merged_bx.append(bx)
            barlines_x = merged_bx

            boundaries = [staff_x0] + barlines_x
            measures   = list(zip(boundaries[:-1], boundaries[1:]))

            # Beam shapes for this system. Some PDFs (Jarabi) use filled curves,
            # others (Tubaka) use rectangles. Accept both. The shape must sit
            # above the staff and be wider than it is tall.
            def looks_like_beam(s):
                return (s.get('fill', True) is not False
                        and s['height'] < 6
                        and s['width'] >= 5
                        and s['width'] > s['height']
                        and s['top']    >= sys_top - staff_height * 2
                        and s['bottom'] <= sys_top + 5)
            sys_beams = [s for s in curves + page.rects if looks_like_beam(s)]

            def is_beamed(x):
                return any(b['x0'] - 5 <= x <= b['x1'] + 5 for b in sys_beams)

            # String y-positions
            staff_ys = sorted(set(
                round(l['top'], 1) for l in vlines
                if (abs(l['top'] - l['bottom']) < 1.5
                    and l['width'] > 200
                    and sys_top - 2 <= l['top'] <= sys_bot + 2)
            ))

            # Notes in this system
            sys_notes = [
                (x, y, fret)
                for x, y, fret in fret_words
                if sys_top - 5 <= y <= sys_bot + 5
            ]

            # ── Per-measure beat calculation via stems + beams ───────────────
            for mi, (mx0, mx1) in enumerate(measures):
                m_width = mx1 - mx0
                if m_width < 1:
                    continue
                global_m = measure_idx + mi

                # For the last measure in a system, extend the right edge to
                # catch notes that sit just past the final barline.
                is_last  = (mi == len(measures) - 1)
                note_x1  = staff_x1 + 10 if is_last else mx1

                # All stem x-positions in this measure (short vertical lines)
                # A barline spans the full staff (bottom >= sys_bot - 5).
                # Stems — including tall two-voice bass stems — stop short of the
                # staff bottom, so we use that to distinguish them from barlines.
                # Min height filters out flag-strokes (PDFs that use flagged
                # eighths render the flag as a separate short vline near the stem).
                STEM_MIN_H = max(3, staff_height * 0.18)
                raw_xs = sorted(set(
                    round(l['x0'], 0)
                    for l in vlines
                    if (abs(l['x0'] - l['x1']) < 2
                        and l['height'] >= STEM_MIN_H
                        and l['bottom'] < sys_bot - 5   # doesn't span full staff
                        and mx0 - 1 <= l['x0'] <= note_x1 + 1
                        and l['top'] >= sys_top - staff_height   # exclude vlines from staff above
                        and l['bottom'] <= sys_bot + 5)
                ))
                # Deduplicate within 3pt, exclude barlines / staff start
                stem_xs = []
                for x in raw_xs:
                    if abs(x - staff_x0) <= 5: continue
                    if any(abs(x - bx) <= 5 for bx in barlines_x): continue
                    if not stem_xs or x - stem_xs[-1] > 3:
                        stem_xs.append(x)

                if not stem_xs:
                    # Fallback: place any notes at beat 0
                    for x, y, fret in sys_notes:
                        if not (mx0 - 1 <= x <= note_x1 + 1): continue
                        str_idx = (min(range(len(staff_ys)),
                                       key=lambda i: abs(staff_ys[i] - y))
                                   if staff_ys else 0)
                        str_idx = max(0, min(5, str_idx))
                        all_notes.append({
                            'beat': round(global_m * 4.0, 4),
                            'midi': OPEN_MIDI[str_idx] + fret,
                            'string': str_idx, 'fret': fret,
                            'measure': global_m, 'beat_in_measure': 0.0,
                        })
                    continue

                # Build cumulative beat positions from stem durations
                stem_beat = {}
                cur = 0.0
                for x in stem_xs:
                    stem_beat[x] = cur
                    cur += 0.5 if is_beamed(x) else 1.0

                # Record per-bar stems for grace-pass snapping
                bar_stems[global_m] = [(x, stem_beat[x]) for x in stem_xs]

                # Record beam pairs (each beam rectangle → one {beat, beat_end} entry)
                for beam in sys_beams:
                    if beam['x0'] < mx0 - 5 or beam['x1'] > note_x1 + 5:
                        continue
                    covered = [sx for sx in stem_xs
                               if beam['x0'] - 3 <= sx <= beam['x1'] + 3]
                    if len(covered) >= 2:
                        b0 = min(3.75, stem_beat[covered[0]])
                        b1 = min(3.75, stem_beat[covered[-1]])
                        all_beams.append({
                            'beat':     round(global_m * 4.0 + b0, 4),
                            'beat_end': round(global_m * 4.0 + b1, 4),
                        })

                # Record stems
                for x in stem_xs:
                    b = stem_beat[x]
                    b = max(0.0, min(3.75, b))
                    all_stems.append({
                        'beat': round(global_m * 4.0 + b, 4),
                        'measure': global_m,
                        'beat_in_measure': round(b, 4),
                    })

                # Map each note to its beat via nearest stem
                for x, y, fret in sys_notes:
                    if not (mx0 - 1 <= x <= note_x1 + 1): continue
                    nearest = min(stem_xs, key=lambda sx: abs(sx - x))
                    b = stem_beat[nearest]
                    b = max(0.0, min(3.75, b))
                    global_beat = global_m * 4.0 + b

                    str_idx = (min(range(len(staff_ys)),
                                   key=lambda i: abs(staff_ys[i] - y))
                               if staff_ys else 0)
                    str_idx = max(0, min(5, str_idx))

                    all_notes.append({
                        'beat':           round(global_beat, 4),
                        'midi':           OPEN_MIDI[str_idx] + fret,
                        'string':         str_idx,
                        'fret':           fret,
                        'measure':        global_m,
                        'beat_in_measure': round(b, 4),
                    })

            # ── Grace-note clusters in this system ──────────────────────────
            # Group digits in this system by string row, find tight x-clusters
            sys_grace_digits = [c for c in page_digits
                                if sys_top - 5 <= c['top'] <= sys_bot + 5]
            grace_rows = {}
            for c in sys_grace_digits:
                if not staff_ys: continue
                si = min(range(len(staff_ys)), key=lambda i: abs(staff_ys[i] - c['top']))
                if abs(staff_ys[si] - c['top']) > 5: continue
                grace_rows.setdefault(max(0, min(5, si)), []).append(c)
            for st, arr in grace_rows.items():
                arr.sort(key=lambda c: c['x0'])
                i = 0
                while i < len(arr):
                    cl = [arr[i]]
                    while i + 1 < len(arr) and arr[i+1]['x0'] - arr[i]['x1'] < GRACE_GAP_MAX:
                        cl.append(arr[i+1]); i += 1
                    if len(cl) >= 2:
                        seq = ''.join(c['text'] for c in cl)
                        x0 = cl[0]['x0']; x1 = cl[-1]['x1']; width = x1 - x0
                        is_grace = (len(cl) >= 3
                                    or (len(cl) == 2 and (int(seq) > 22 or width > GRACE_2DIG_W)))
                        if is_grace:
                            frets = [int(c['text']) for c in cl]
                            mid_x = (x0 + x1) / 2
                            for mi, (mx0, mx1) in enumerate(measures):
                                if mx0 <= mid_x <= mx1:
                                    g_m = measure_idx + mi
                                    all_grace.append({
                                        'measure_raw': g_m,
                                        'string': st,
                                        'frets': frets,
                                        'x0': x0,
                                        'x1': x1,
                                        'bar_x0': mx0,
                                        'bar_x1': mx1,
                                        'bar_stems': bar_stems.get(g_m, []),
                                    })
                                    break
                    i += 1

            measure_idx += len(measures)

all_notes.sort(key=lambda e: (e['beat'], e['string']))

# Deduplicate stems by beat
seen = set()
unique_stems = []
for s in sorted(all_stems, key=lambda e: e['beat']):
    key = round(s['beat'] * 8)   # 0.125-beat resolution
    if key not in seen:
        seen.add(key)
        unique_stems.append(s)

# Add empty first measure (for pickup/preparation time)
# Shift all beats and measures forward by 1 measure (4 beats)
for n in all_notes:
    n['beat'] += 4
    n['beat_in_measure'] = n['beat'] % 4
    n['measure'] += 1

for s in unique_stems:
    s['beat'] += 4
    s['beat_in_measure'] = s['beat'] % 4
    s['measure'] += 1

for b in all_beams:
    b['beat'] += 4
    b['beat_end'] += 4

# ── Append grace notes (snap to nearest stem position by x) ───────────────────
# If that beat already has a regular note on the same string, shift the grace
# ornament a tiny bit later so it plays AFTER the main note (hammer-on/pull-off).
for g in all_grace:
    measure_global = g['measure_raw'] + 1  # pickup shift
    stems_in_bar = g.get('bar_stems') or []
    if stems_in_bar:
        # Snap grace's leading x to the nearest stem's bim
        gx = g['x0']
        nearest = min(stems_in_bar, key=lambda sb: abs(sb[0] - gx))
        start_bim = max(0.0, min(3.5, nearest[1]))
    else:
        rel = (g['x0'] - g['bar_x0']) / (g['bar_x1'] - g['bar_x0']) * 4.0
        start_bim = max(0.0, min(3.0, round(rel)))
    # Conflict check: is there a regular note at this beat on this string?
    conflict = any(
        n['measure'] == measure_global and n['string'] == g['string']
        and not n.get('grace')
        and abs(n['beat_in_measure'] - start_bim) < 0.05
        for n in all_notes
    )
    if conflict:
        start_bim = min(3.85, start_bim + 0.15)   # shift so ornament follows the main note
    for k, fret in enumerate(g['frets']):
        bim = start_bim + k * 0.15
        if bim >= 4.0: bim = 3.99
        all_notes.append({
            'beat': round(measure_global * 4.0 + bim, 4),
            'midi': OPEN_MIDI[g['string']] + fret,
            'string': g['string'],
            'fret': fret,
            'measure': measure_global,
            'beat_in_measure': round(bim, 4),
            'grace': True,
        })

all_notes.sort(key=lambda e: (e['beat'], e['string']))

total_measures = measure_idx + 1
print(f"Total measures: {total_measures}")
print(f"Total notes:    {len(all_notes)}")
print(f"Total stems:    {len(unique_stems)}")

for n in all_notes[:30]:
    print(f"  m={n['measure']:3d}  b={n['beat_in_measure']:.2f}"
          f"  str={n['string']}  fret={n['fret']:2d}  midi={n['midi']}"
          f"  beat={n['beat']:.2f}")

out = {
    'bpm':            80,
    'total_measures': total_measures,
    'open_midi':      OPEN_MIDI,
    'notes':          all_notes,
    'stems':          unique_stems,
    'beams':          all_beams,
}
with open(OUT_PATH, 'w') as f:
    json.dump(out, f, indent=2)
print(f"\nSaved {OUT_PATH}")
print(f"Remember to add this score to scores/scores.json so it appears in the picker:")
print(f'  {{ "id": "{SCORE_ID}", "title": "...", "composer": "..." }}')
