#!/usr/bin/env python3
"""Regression check for parse_tab.py.

Re-parses every tab in tests/manifest.json and compares the output against
tests/baselines/<id>.json. Any difference is reported as a structured summary
(measure counts, added/removed notes per measure, note pile-ups) instead of a
10,000-line JSON diff.

Usage:
  python3 tests/run_regression.py                 # check all tabs
  python3 tests/run_regression.py --update-baselines   # accept current output
                                                  # (only after visual check!)

Exit code 0 = no regressions; 1 = differences found or a parse failed.
"""
import json, os, subprocess, sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, 'tests')
LAST = os.path.join(TESTS, '.last_run')
UPDATE = '--update-baselines' in sys.argv


def note_key(n):
    return (n['measure'], n['string'], round(n['beat'], 3),
            n['fret'], n['midi'], bool(n.get('grace')))


def pileups(notes, eps=0.05):
    """Same string + same measure + (nearly) same beat = physically impossible
    stacked notes — the classic regression class for this parser."""
    seen, hits = {}, []
    for n in notes:
        if n.get('grace'):
            continue
        k = (n['measure'], n['string'])
        if any(abs(b - n['beat']) < eps for b in seen.get(k, [])):
            hits.append((n['measure'], n['string'], n['beat']))
        seen.setdefault(k, []).append(n['beat'])
    return hits


def summarize(base, new):
    msgs = []
    for k in ('bpm', 'total_measures', 'open_midi'):
        if base.get(k) != new.get(k):
            msgs.append(f"  {k}: {base.get(k)} -> {new.get(k)}")
    b, n = Counter(map(note_key, base['notes'])), Counter(map(note_key, new['notes']))
    removed, added = b - n, n - b
    for label, bag in (('removed', removed), ('added', added)):
        if bag:
            by_measure = Counter(k[0] for k in bag.elements())
            detail = ', '.join(f"m{m}:{c}" for m, c in sorted(by_measure.items())[:12])
            more = '' if len(by_measure) <= 12 else f" (+{len(by_measure)-12} more measures)"
            msgs.append(f"  notes {label}: {sum(bag.values())} ({detail}{more})")
    for k in ('stems', 'beams'):
        if len(base.get(k, [])) != len(new.get(k, [])):
            msgs.append(f"  {k}: {len(base[k])} -> {len(new[k])}")
    new_piles, base_piles = pileups(new['notes']), pileups(base['notes'])
    if len(new_piles) > len(base_piles):
        sample = ', '.join(f"m{m} s{s} b{b:g}" for m, s, b in new_piles[:8])
        msgs.append(f"  NOTE PILE-UPS: {len(base_piles)} -> {len(new_piles)} ({sample})")
    return msgs


def main():
    manifest = json.load(open(os.path.join(TESTS, 'manifest.json')))
    os.makedirs(LAST, exist_ok=True)
    failed = False
    for entry in manifest:
        sid, pdf = entry['id'], os.path.join(ROOT, entry['pdf'])
        if not os.path.exists(pdf):
            print(f"SKIP  {sid}: missing {entry['pdf']}")
            continue
        rt_id = f"{sid}__rt"
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'parse_tab.py'), pdf, rt_id],
                           capture_output=True, text=True)
        out_path = os.path.join(ROOT, 'scores', f'{rt_id}.json')
        if r.returncode != 0 or not os.path.exists(out_path):
            print(f"FAIL  {sid}: parse_tab.py errored\n{r.stdout[-500:]}{r.stderr[-500:]}")
            failed = True
            continue
        kept = os.path.join(LAST, f'{sid}.json')
        os.replace(out_path, kept)  # keep scores/ clean; full output stays inspectable
        new = json.load(open(kept))
        base_path = os.path.join(TESTS, 'baselines', f'{sid}.json')
        if UPDATE:
            json.dump(new, open(base_path, 'w'))
            print(f"UPDATED  {sid}: baseline now matches current parser output")
            continue
        base = json.load(open(base_path))
        if base == new:
            print(f"PASS  {sid}: identical to baseline")
        else:
            print(f"DIFF  {sid}:")
            print('\n'.join(summarize(base, new)))
            print(f"      full output: tests/.last_run/{sid}.json")
            failed = True
    if failed and not UPDATE:
        print("\nDifferences found. If they are INTENDED and visually verified in the")
        print("viewer, rerun with --update-baselines to accept them.")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
