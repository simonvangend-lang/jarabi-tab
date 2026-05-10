#!/usr/bin/env python3
"""
Parse Jarabi PDF tab using barlines + beam detection for accurate musical timing.
Outputs notes.json with beat positions derived from note values:
  - Beamed stems (eighth notes): 0.5-beat steps
  - Unbeamed stems (quarter/half/whole): 1.0-beat steps

Tuning (without capo): E B F# D A D (strings 0-5, high to low)
CAPO III → open MIDI: G4(67) D4(62) A3(57) F3(53) C3(48) F2(41)
"""
import pdfplumber, json

PDF       = "/Volumes/T9/Dropbox/simonvangend/guitar/Derek Gripper TABs/Jarabi.pdf"
OPEN_MIDI = [67, 62, 57, 53, 48, 41]
STAFF_H_MIN = 35.0   # min height for a barline

def is_fret(t):
    try: n = int(t); return 0 <= n <= 22
    except: return False

all_notes    = []
all_stems    = []
all_beams    = []
measure_idx  = 0

with pdfplumber.open(PDF) as pdf:
    for page_num, page in enumerate(pdf.pages):

        # ── Visible characters only (filter white-fill phantom markers) ──────
        visible_chars = [
            ch for ch in page.chars
            if ch.get('non_stroking_color') != (1.0, 1.0, 1.0)
        ]
        words       = page.extract_words(x_tolerance=3, y_tolerance=3)
        visible_xs  = set(round(float(ch['x0']), 1)
                          for ch in visible_chars if is_fret(ch['text']))
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
            # Use horizontal centre for stem matching (x0 is left edge, biases left)
            x_mid = (x0 + float(w['x1'])) / 2
            fret_words.append((x_mid, y, int(w['text'])))

        # ── Process each system ──────────────────────────────────────────────
        for sys_top, sys_bot, staff_x0, staff_x1 in systems_h:

            # Barlines
            barlines_x = sorted(set(
                round(l['x0'], 1)
                for l in vlines
                if (abs(l['x0'] - l['x1']) < 2
                    and l['height'] >= STAFF_H_MIN
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

            # Beam curves for this system (filled, elongated, above the staff)
            sys_beams = [
                c for c in curves
                if (c.get('fill')
                    and c['height'] < 6
                    and c['width'] > 8
                    and c['width'] > c['height'] * 3
                    and c['top']    >= sys_top - 70
                    and c['bottom'] <= sys_top + 5)
            ]

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
                raw_xs = sorted(set(
                    round(l['x0'], 0)
                    for l in vlines
                    if (abs(l['x0'] - l['x1']) < 2
                        and l['height'] >= 0.5
                        and l['bottom'] < sys_bot - 5   # doesn't span full staff
                        and mx0 - 1 <= l['x0'] <= note_x1 + 1
                        and l['top'] >= sys_top - 70
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
with open('notes.json', 'w') as f:
    json.dump(out, f)
print(f"\nSaved notes.json")
