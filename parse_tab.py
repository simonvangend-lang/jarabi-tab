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

def detect_grace_size(pdf, fret_size):
    """Some PDFs (Tubaka) use a second smaller font for hammer-on/pull-off
    ornaments. Pick the most common digit size below fret_size that has at
    least ~5 occurrences per page on average."""
    sizes = Counter()
    for p in pdf.pages:
        for c in p.chars:
            if (c['text'].isdigit()
                and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
                and round(c['size'], 1) < fret_size - 0.5):
                sizes[round(c['size'], 1)] += 1
    if not sizes: return None
    sz, n = sizes.most_common(1)[0]
    return sz if n >= 5 * max(1, len(pdf.pages)) else None

with pdfplumber.open(PDF) as pdf:
    FRET_SIZE = detect_fret_size(pdf)
    GRACE_SIZE = detect_grace_size(pdf, FRET_SIZE)
print(f'Detected fret font size: {FRET_SIZE}pt'
      + (f'  ·  grace font: {GRACE_SIZE}pt' if GRACE_SIZE else ''))

# Grace-cluster thresholds scale with font size (calibrated for 10.4pt)
GRACE_GAP_MAX  = 8.0  * (FRET_SIZE / 10.4)   # tight-cluster horizontal gap
GRACE_2DIG_W   = 13.5 * (FRET_SIZE / 10.4)   # 2-digit cluster width above which it's grace

all_notes    = []
all_stems    = []
all_beams    = []
all_grace    = []          # list of (measure_global_raw, string, frets, x0_first_digit, bar_x0, bar_x1)
measure_idx  = 0
piece_has_anacrusis = False  # set to True if bar 0 is a true pickup (skip the +1 shift)

# Per-page set of (round(x0,1)) values for digit chars that belong to a
# tight grace-note cluster. We need this BEFORE building visible_xs so that
# those characters are excluded from the regular-note parsing.
def find_grace_skip_positions(page):
    """Return a set of (round(x0,1), round(y,0)) tuples for digit chars that are
    part of tight grace clusters. Per-(x,y) so chord notes on other strings at the
    same x are not affected.

    Logic:
    - Jarabi-style PDFs (GRACE_SIZE is None): graces use the same font as mains
      and are identified solely by tight clustering. Skip all such clusters.
    - Tubaka-style PDFs (GRACE_SIZE is set): graces have their own smaller font,
      handled by a separate code path. Tight clusters at the main fret size are
      usually slurred runs of main notes, NOT graces — so only skip those that
      look like triplets (3 digits with a "3" marker above)."""
    # Triplet markers anywhere on the page (a "3" character in a non-fret font)
    triplet_marks_x = [
        c['x0'] for c in page.chars
        if c.get('text') == '3'
        and round(c['size'], 1) != FRET_SIZE
        and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
    ]
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
                    is_triplet = (len(cl) == 3
                                  and any(x0 - 5 <= tx <= x1 + 5 for tx in triplet_marks_x))
                    # Skip only if Jarabi-style (no GRACE_SIZE) OR this cluster
                    # is a triplet (will be re-emitted as duration='t' notes).
                    if GRACE_SIZE is None or is_triplet:
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
        # (x, y) pairs of visible single-digit chars at the MAIN fret size,
        # minus grace-cluster ones. Filtering by size keeps Tubaka-style PDFs
        # (which use a smaller font for ornaments) from polluting the main pass.
        visible_keys = set(
            (round(float(ch['x0']), 1), round(float(ch['top'])))
            for ch in visible_chars
            if is_fret(ch['text']) and round(ch['size'], 1) == FRET_SIZE
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

        # Some PDFs draw the same digit twice at identical coordinates (a
        # bolding artifact). extract_words then concatenates "2" + "2" into
        # "22" which passes as fret 22. Build a set of (x, y) positions where
        # the source PDF has duplicated identical digits, so we can detect
        # and collapse those "22"s back to "2".
        from collections import Counter
        dup_positions = set()
        char_counter = Counter()
        for ch in visible_chars:
            if ch['text'].isdigit() and round(ch['size'], 1) == FRET_SIZE:
                key = (round(ch['x0'], 1), round(ch['top']), ch['text'])
                char_counter[key] += 1
        for key, n in char_counter.items():
            if n > 1:
                dup_positions.add((key[0], key[1]))

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
            text = w['text']
            # Collapse duplicate-character artefacts: "22" at a position with
            # two identical "2" chars → "2".
            if len(text) >= 2 and len(set(text)) == 1 and (round(x0, 1), round(y)) in dup_positions:
                text = text[0]
            # Use horizontal centre for stem matching (x0 is left edge, biases left)
            x_mid = (x0 + float(w['x1'])) / 2
            fret_words.append((x_mid, y, int(text)))

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
                # Beam sits within ~staff_height above sys_top. Earlier we used
                # 2x staff_height which leaked beams from the system above.
                return (s.get('fill', True) is not False
                        and s['height'] < 6
                        and s['width'] >= 5
                        and s['width'] > s['height']
                        and s['top']    >= sys_top - staff_height * 0.8
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

            # ── Pre-pass: find triplet x-ranges in this system ───────────────
            # A triplet = a "3" marker above the staff PLUS a tight 3-digit
            # cluster of fret-size digits beneath it. Each marker→range used
            # to mark contained stems as 1/3-beat instead of 0.5-beat.
            triplet_marks_x = [
                c['x0'] for c in page.chars
                if c.get('text') == '3'
                and sys_top - 25 <= c['top'] <= sys_top - 3
                and round(c['size'], 1) != FRET_SIZE
                and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
            ]
            # Find 3-digit fret clusters and pair them with triplet markers.
            sys_fret_digits = [c for c in page.chars
                               if c['text'].isdigit()
                               and round(c['size'], 1) == FRET_SIZE
                               and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
                               and sys_top - 5 <= c['top'] <= sys_bot + 5]
            triplet_ranges = []   # [(x0, x1, string, frets)]
            by_row = {}
            for c in sys_fret_digits:
                if not staff_ys: continue
                si = min(range(len(staff_ys)), key=lambda i: abs(staff_ys[i] - c['top']))
                if abs(staff_ys[si] - c['top']) > 5: continue
                by_row.setdefault(max(0, min(5, si)), []).append(c)
            for si, arr in by_row.items():
                arr.sort(key=lambda c: c['x0'])
                i = 0
                while i < len(arr):
                    cl = [arr[i]]
                    while i + 1 < len(arr) and arr[i+1]['x0'] - arr[i]['x1'] < GRACE_GAP_MAX:
                        cl.append(arr[i+1]); i += 1
                    if len(cl) == 3:
                        cx0, cx1 = cl[0]['x0'], cl[-1]['x1']
                        if any(cx0 - 5 <= tx <= cx1 + 5 for tx in triplet_marks_x):
                            triplet_ranges.append((cx0, cx1, si, [int(c['text']) for c in cl]))
                    i += 1

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
                STEM_MIN_H = max(3, staff_height * 0.22)
                # Barlines span the FULL staff height (both ends within ~5pt of
                # sys_top/sys_bot). Anything shorter is a stem — including
                # voice-2 stems whose bottoms reach into the staff-bottom area.
                def is_barline(l):
                    return (l['top'] >= sys_top - 5 and l['top'] <= sys_top + 5
                            and l['bottom'] >= sys_bot - 5 and l['bottom'] <= sys_bot + 5
                            and l['height'] >= staff_height * 0.7)
                raw_stems_obj = sorted(
                    [l for l in vlines
                     if (abs(l['x0'] - l['x1']) < 2
                         and l['height'] >= STEM_MIN_H
                         and not is_barline(l)
                         and mx0 - 1 <= l['x0'] <= note_x1 + 1
                         and l['top'] >= sys_top - staff_height
                         and l['bottom'] <= sys_bot + 5)],
                    key=lambda l: l['x0']
                )
                # Deduplicate within ~1pt while keeping float precision for beat calc
                raw_xs = []
                seen = set()
                for l in raw_stems_obj:
                    key = round(l['x0'])
                    if key in seen: continue
                    seen.add(key); raw_xs.append(l['x0'])
                # Map x → stem object (the tallest one at that x) for chord-merge
                stem_by_x = {}
                for l in raw_stems_obj:
                    # Use the float x directly so subsequent lookups match
                    if l['x0'] not in stem_by_x or l['height'] > stem_by_x[l['x0']]['height']:
                        stem_by_x[l['x0']] = l
                # Exclude barlines / staff start
                kept = [x for x in raw_xs
                        if abs(x - staff_x0) > 5
                        and not any(abs(x - bx) <= 5 for bx in barlines_x)]
                # Pre-compute which strings have notes near each stem's x.
                # Used by chord-merge to distinguish voice-paired chords (notes
                # on different strings, same beat) from sequential eighth notes
                # (notes on the same string).
                def strings_at(stem_x):
                    out = set()
                    for x, y, _ in sys_notes:
                        if abs(x - stem_x) <= 5 and staff_ys:
                            si = min(range(len(staff_ys)),
                                     key=lambda i: abs(staff_ys[i] - y))
                            if abs(staff_ys[si] - y) <= 5:
                                out.add(max(0, min(5, si)))
                    return out

                # Voice-paired chord merge: a melody-voice stem and a bass-voice
                # stem that share the SAME beat sit at essentially the same x
                # (within a couple of points). Sequential eighth-note chords,
                # by contrast, sit roughly one eighth apart. So only merge
                # stems that are nearly co-located AND where one is markedly
                # taller (the cross-voice stem) AND their notes are on
                # disjoint strings (so it's a chord, not a same-string flam).
                est_eighth = (mx1 - mx0) / 8.0
                # Voice-paired stems are typically within a few pt; cap at
                # 25% of an eighth so a real eighth-pair never merges.
                merge_thresh = min(5.0, est_eighth * 0.25)
                stem_xs = []
                for x in kept:
                    if stem_xs:
                        prev_x = stem_xs[-1]
                        if x - prev_x < merge_thresh:
                            h_prev = stem_by_x[prev_x]['height']
                            h_cur  = stem_by_x[x]['height']
                            tall_thresh = staff_height * 0.55
                            h_tall, h_short = max(h_prev, h_cur), min(h_prev, h_cur)
                            # Only merge if one is genuinely cross-voice tall
                            # AND meaningfully taller than the other (i.e. the
                            # shorter is a same-beat partial-voice artifact).
                            if h_tall > tall_thresh and h_tall > h_short * 1.4:
                                strs_prev = strings_at(prev_x)
                                strs_cur  = strings_at(x)
                                if not (strs_prev & strs_cur):
                                    if h_cur > h_prev:
                                        stem_xs[-1] = x
                                    continue
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

                # Anacrusis detection: if the very first bar of the piece has
                # only a partial measure's worth of stems AND those stems are
                # positioned in the right half of the bar, treat it as a pickup
                # and number stems counting BACKWARD from the next barline.
                total_normal_beats = sum(0.5 if is_beamed(x) else 1.0 for x in stem_xs)
                bar_width = mx1 - mx0
                stems_in_right_half = all((x - mx0) / max(1, bar_width) > 0.4 for x in stem_xs)
                is_anacrusis = (global_m == 0
                                and total_normal_beats < 2.0
                                and stems_in_right_half)
                if is_anacrusis:
                    piece_has_anacrusis = True

                def beam_stack(x):
                    """Count how many beam rectangles cover this stem x.
                    1 beam = eighth, 2 = sixteenth, 3 = thirty-second."""
                    return sum(1 for b in sys_beams
                               if b['x0'] - 3 <= x <= b['x1'] + 3)

                def stem_duration(x):
                    # Triplet stems: 1/3 beat each so three of them sum to 1 beat
                    for cx0, cx1, _, _ in triplet_ranges:
                        if cx0 - 5 <= x <= cx1 + 5:
                            return 1.0 / 3.0
                    bs = beam_stack(x)
                    if bs >= 3: return 0.125     # 32nd
                    if bs == 2: return 0.25      # 16th
                    if bs == 1: return 0.5       # 8th
                    return 1.0                    # quarter

                # Detect multi-voice bars by counting distinct beam-top groups.
                # 3+ separate top-y levels = multiple voices laid out across the
                # bar — cumulative beat counting may overcount if voice-paired
                # stems weren't fully merged. Fall back to uniform x→beat
                # mapping ONLY when the cumulative count is clearly off (total
                # durations significantly exceed one bar). With beam-stack
                # counting (sixteenths get 0.25, eighths 0.5), most multi-voice
                # bars now total to ~4.0 and don't need the fallback.
                tops = sorted(stem_by_x[x]['top'] for x in stem_xs)
                top_groups = []
                for t in tops:
                    if top_groups and t - top_groups[-1] < 3: continue
                    top_groups.append(t)
                cum_total = sum(stem_duration(x) for x in stem_xs)
                is_multi_voice = (len(top_groups) >= 3
                                  and len(stem_xs) > 8
                                  and cum_total > 4.5)

                stem_beat = {}
                if is_multi_voice:
                    # Uniform-x → beat, snapped to nearest sixteenth (0.25).
                    # Slightly biased downward (use 0.4 instead of 0.5) so a
                    # closely spaced sixteenth-run lands on consecutive grid
                    # positions instead of jumping ahead one sixteenth.
                    bar_w = max(1, mx1 - mx0)
                    for x in stem_xs:
                        raw = (x - mx0) / bar_w * 4.0
                        bim = int(raw * 4 + 0.35) / 4
                        stem_beat[x] = max(0.0, min(3.75, bim))
                elif is_anacrusis:
                    cur = 4.0
                    for x in reversed(stem_xs):
                        cur -= stem_duration(x)
                        stem_beat[x] = cur
                else:
                    cur = 0.0
                    for x in stem_xs:
                        stem_beat[x] = cur
                        cur += stem_duration(x)

                # Record per-bar stems for grace-pass snapping
                bar_stems[global_m] = [(x, stem_beat[x]) for x in stem_xs]

                # ── Emit triplets in this measure ──────────────────────────
                # Triplets whose x-range falls inside this measure get their
                # three notes added at the matching stem beats (now spaced 1/3
                # each thanks to stem_duration above).
                for cx0, cx1, t_str, t_frets in triplet_ranges:
                    if not (mx0 - 1 <= (cx0 + cx1) / 2 <= mx1 + 1): continue
                    # Find the 3 stems sitting inside this triplet range
                    inside = [x for x in stem_xs if cx0 - 5 <= x <= cx1 + 5]
                    if len(inside) < 1: continue
                    # Use the leading stem's bim as the start; if fewer than 3
                    # stems were detected, synthesise 1/3-beat steps.
                    start_bim = stem_beat[inside[0]]
                    for k, fret in enumerate(t_frets):
                        if k < len(inside):
                            bim_tr = stem_beat[inside[k]]
                        else:
                            bim_tr = start_bim + k * (1.0/3.0)
                        beat_tr = global_m * 4.0 + bim_tr
                        all_notes.append({
                            'beat':           round(beat_tr, 4),
                            'midi':           OPEN_MIDI[t_str] + fret,
                            'string':         t_str,
                            'fret':           fret,
                            'measure':        global_m,
                            'beat_in_measure': round(bim_tr, 4),
                            'duration':       't',
                        })

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

                # Map each note to its beat via nearest stem (default) or
                # uniform-x mapping of the note's own x (for multi-voice bars,
                # where stem positions may be offset from digit positions).
                main_notes_in_bar = []   # (stem_x, str_idx, fret, beat_global)
                for x, y, fret in sys_notes:
                    if not (mx0 - 1 <= x <= note_x1 + 1): continue
                    nearest = min(stem_xs, key=lambda sx: abs(sx - x))
                    if is_multi_voice:
                        bar_w = max(1, mx1 - mx0)
                        raw = (x - mx0) / bar_w * 4.0
                        b = max(0.0, min(3.75, int(raw * 4 + 0.35) / 4))
                    else:
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
                    main_notes_in_bar.append((nearest, str_idx, fret, global_beat, b))

                # ── Small grace digits (Tubaka-style hammer-on/pull-off + triplets) ────
                if GRACE_SIZE is not None:
                    sm_digits = [c for c in page.chars
                                 if c['text'].isdigit()
                                 and round(c['size'], 1) == GRACE_SIZE
                                 and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
                                 and mx0 - 1 <= c['x0'] <= note_x1 + 1
                                 and sys_top - 5 <= c['top'] <= sys_bot + 5]
                    # Group by string row + tight x-cluster. 3+ tight digits = triplet.
                    rows = {}
                    for c in sm_digits:
                        if not staff_ys: continue
                        si = min(range(len(staff_ys)),
                                 key=lambda i: abs(staff_ys[i] - c['top']))
                        if abs(staff_ys[si] - c['top']) > 6: continue
                        si = max(0, min(5, si))
                        rows.setdefault(si, []).append(c)
                    handled = set()  # id(c) of digits we've already processed
                    for si, arr in rows.items():
                        arr.sort(key=lambda c: c['x0'])
                        i = 0
                        while i < len(arr):
                            cl = [arr[i]]
                            while (i + 1 < len(arr)
                                   and arr[i+1]['x0'] - arr[i]['x1'] < GRACE_GAP_MAX * 1.5):
                                cl.append(arr[i+1]); i += 1
                            if len(cl) >= 3:
                                # Triplet — emit at 1/3-beat spacing starting at
                                # the beat closest to the cluster's leading x.
                                lead_x = (cl[0]['x0'] + cl[0]['x1']) / 2
                                if stem_xs:
                                    near = min(stem_xs, key=lambda sx: abs(sx - lead_x))
                                    start_bim = stem_beat[near]
                                else:
                                    start_bim = 0.0
                                for k, dc in enumerate(cl):
                                    fret = int(dc['text'])
                                    bim_tr  = start_bim + k * (1.0/3.0)
                                    beat_tr = global_m * 4.0 + bim_tr
                                    all_notes.append({
                                        'beat':           round(beat_tr, 4),
                                        'midi':           OPEN_MIDI[si] + fret,
                                        'string':         si,
                                        'fret':           fret,
                                        'measure':        global_m,
                                        'beat_in_measure': round(bim_tr, 4),
                                        'duration':       't',
                                    })
                                    handled.add(id(dc))
                            i += 1
                    # Remaining small digits → hammer-on/pull-off graces (original logic)
                    for c in sm_digits:
                        if id(c) in handled: continue
                        if not staff_ys: continue
                        si = min(range(len(staff_ys)),
                                 key=lambda i: abs(staff_ys[i] - c['top']))
                        if abs(staff_ys[si] - c['top']) > 6: continue
                        si = max(0, min(5, si))
                        fret = int(c['text'])
                        gx   = (c['x0'] + c['x1']) / 2
                        candidates = [n for n in main_notes_in_bar
                                      if n[1] == si and n[0] > gx]
                        if not candidates: continue
                        target = min(candidates, key=lambda n: n[0] - gx)
                        target_beat   = target[3]
                        target_bim    = target[4]
                        grace_beat    = target_beat - 0.15
                        grace_measure = int(grace_beat // 4)
                        grace_bim     = grace_beat - grace_measure * 4
                        all_notes.append({
                            'beat':           round(grace_beat, 4),
                            'midi':           OPEN_MIDI[si] + fret,
                            'string':         si,
                            'fret':           fret,
                            'measure':        grace_measure,
                            'beat_in_measure': round(grace_bim, 4),
                            'grace':          True,
                        })
                        for n in all_notes:
                            if (n.get('measure') == global_m
                                and n.get('string') == si
                                and abs(n.get('beat_in_measure', -1) - target_bim) < 1e-3
                                and n.get('fret') == target[2]
                                and not n.get('grace')):
                                n['hammer'] = True
                                break

            # ── Triplet markers above this system (Tubaka uses a "3" char in a
            # non-fret font, sitting above the staff). Detect them so 3-digit
            # clusters that have one can be emitted as triplets rather than
            # graces.
            triplet_marks_x = [
                c['x0'] for c in page.chars
                if c.get('text') == '3'
                and sys_top - 25 <= c['top'] <= sys_top - 3
                and round(c['size'], 1) != FRET_SIZE
                and c.get('non_stroking_color') != (1.0, 1.0, 1.0)
            ]

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
                        # Skip clusters that are triplets (handled in main pass)
                        is_triplet = (len(cl) == 3 and any(
                            x0 - 5 <= tx <= x1 + 5 for tx in triplet_marks_x))
                        # Only emit as Jarabi-style grace when there's no separate
                        # GRACE_SIZE font. For Tubaka, the cluster's digits remain
                        # in the main pass (slurred runs of main-size notes).
                        if is_grace and not is_triplet and GRACE_SIZE is None:
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

# Add empty first measure (for pickup/preparation time). Skipped when the
# piece already has a real anacrusis — there's no need for an extra empty
# bar in front.
PICKUP_SHIFT = 0 if piece_has_anacrusis else 4
PICKUP_BARS  = 0 if piece_has_anacrusis else 1
for n in all_notes:
    n['beat'] += PICKUP_SHIFT
    n['beat_in_measure'] = n['beat'] % 4
    n['measure'] += PICKUP_BARS

for s in unique_stems:
    s['beat'] += PICKUP_SHIFT
    s['beat_in_measure'] = s['beat'] % 4
    s['measure'] += PICKUP_BARS

for b in all_beams:
    b['beat'] += PICKUP_SHIFT
    b['beat_end'] += PICKUP_SHIFT

# ── Append grace notes (snap to nearest stem position by x) ───────────────────
# If that beat already has a regular note on the same string, shift the grace
# ornament a tiny bit later so it plays AFTER the main note (hammer-on/pull-off).
for g in all_grace:
    measure_global = g['measure_raw'] + PICKUP_BARS  # pickup shift
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

total_measures = measure_idx + PICKUP_BARS
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
