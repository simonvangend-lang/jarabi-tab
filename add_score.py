#!/usr/bin/env python3
"""Parse a PDF into scores/<id>.json, add it to scores/scores.json, then
commit & push so it appears in the picker.

Usage:
  ./add_score.py /path/to/piece.pdf
  ./add_score.py /path/to/piece.pdf --id custom-id --title "Display Title" --composer "Trad. arr. X"
  ./add_score.py /path/to/piece.pdf --no-push      # parse + commit only

If --title / --composer aren't passed, you'll be prompted. If the score
id already exists in scores.json, its entry is updated.
"""
import sys, os, json, subprocess, re, argparse

ROOT = os.path.dirname(os.path.abspath(__file__))


def slug(name):
    base = os.path.splitext(os.path.basename(name))[0].lower()
    return re.sub(r'[^a-z0-9_-]+', '-', base).strip('-')


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pdf', help='Path to the PDF file')
    ap.add_argument('--id', help='Score id (slug derived from filename by default)')
    ap.add_argument('--title', help='Display title')
    ap.add_argument('--composer', help='Composer / arranger credit')
    ap.add_argument('--no-push', action='store_true', help='Skip the final git push')
    ap.add_argument('--no-commit', action='store_true', help='Skip the commit step too')
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit(f'PDF not found: {args.pdf}')

    sid = args.id or slug(args.pdf)
    print(f'\n► Parsing {args.pdf}')
    run([sys.executable, os.path.join(ROOT, 'parse_tab.py'), args.pdf, sid])

    # Update scores/scores.json
    catalogue_path = os.path.join(ROOT, 'scores', 'scores.json')
    with open(catalogue_path) as f:
        cat = json.load(f)
    existing = next((s for s in cat['scores'] if s['id'] == sid), None)
    if existing:
        title    = args.title    or existing.get('title', sid)
        composer = args.composer or existing.get('composer', '')
    else:
        title    = args.title    or input(f'Title for "{sid}" [{sid}]: ').strip() or sid
        composer = args.composer or input(f'Composer / arranger: ').strip()
    entry = {'id': sid, 'title': title, 'composer': composer}
    if existing:
        cat['scores'][cat['scores'].index(existing)] = entry
        action = 'Update'
    else:
        cat['scores'].append(entry)
        action = 'Add'
    with open(catalogue_path, 'w') as f:
        json.dump(cat, f, indent=2)
        f.write('\n')
    print(f'► {action.lower()}d scores.json entry: {entry}')

    # Bump service-worker cache so phones pick up the new score on next reload
    sw_path = os.path.join(ROOT, 'sw.js')
    with open(sw_path) as f:
        sw = f.read()
    m = re.search(r"CACHE = 'jarabi-v(\d+)'", sw)
    if m:
        new_v = int(m.group(1)) + 1
        sw = sw.replace(f"jarabi-v{m.group(1)}", f"jarabi-v{new_v}", 1)
        with open(sw_path, 'w') as f:
            f.write(sw)
        print(f'► bumped sw.js cache to v{new_v}')

    if args.no_commit:
        print('\n✔ done (skipping commit/push as requested)')
        return

    files = [f'scores/{sid}.json', 'scores/scores.json', 'sw.js']
    run(['git', 'add'] + files)
    msg = f'{action} score: {title}'
    run(['git', '-c', 'commit.gpgsign=false', 'commit', '-m', msg])
    print(f'► committed: {msg}')

    if args.no_push:
        print('\n✔ done (skipping push as requested)')
        return

    run(['git', 'push', 'origin', 'main'])
    print('\n✔ pushed to GitHub. The score will appear in the picker after a reload.')


if __name__ == '__main__':
    main()
