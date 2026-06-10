#!/usr/bin/env python3
"""Deploy the tab viewer to GitHub Pages, with cache-bump and live verification.

Usage:
  ./deploy.py "commit message"
  ./deploy.py "commit message" --no-verify    # skip live-URL polling

What it does, in order:
1. If parse_tab.py changed (working tree or unpushed commits), runs
   tests/run_regression.py first and aborts on failure.
2. Bumps the service-worker cache version in sw.js (jarabi-vNN -> NN+1) so
   every client refetches fresh assets instead of serving a stale cache.
3. Stages tracked modifications (git add -u; untracked files are left alone),
   commits, and pushes to main.
4. Polls the live sw.js until GitHub Pages serves the new cache version
   (usually 1-3 minutes), proving the deploy actually landed.
"""
import re, subprocess, sys, time, urllib.request

LIVE = 'https://simonvangend-lang.github.io/jarabi-tab/'


def run(*cmd, capture=False):
    return subprocess.run(cmd, check=True, text=True,
                          capture_output=capture).stdout if capture else \
           subprocess.run(cmd, check=True)


def changed_files():
    files = set()
    for line in run('git', 'status', '--porcelain', capture=True).splitlines():
        if not line.startswith('??'):
            files.add(line[3:].strip())
    try:
        ahead = run('git', 'diff', '--name-only', '@{u}..HEAD', capture=True)
        files.update(f.strip() for f in ahead.splitlines() if f.strip())
    except subprocess.CalledProcessError:
        pass  # no upstream yet
    return files


def main():
    args = [a for a in sys.argv[1:] if a != '--no-verify']
    verify = '--no-verify' not in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    message = args[0]

    pending = changed_files()
    if 'parse_tab.py' in pending:
        print('parse_tab.py changed -> running regression check first...')
        if subprocess.run([sys.executable, 'tests/run_regression.py']).returncode != 0:
            print('\nABORTED: regression check failed. Fix or accept baselines first.')
            sys.exit(1)

    sw = open('sw.js').read()
    m = re.search(r"(jarabi-v)(\d+)", sw)
    if not m:
        print('ABORTED: no jarabi-vNN cache version found in sw.js')
        sys.exit(1)
    new_version = f"{m.group(1)}{int(m.group(2)) + 1}"
    open('sw.js', 'w').write(sw.replace(m.group(0), new_version, 1))
    print(f'sw.js cache: {m.group(0)} -> {new_version}')

    run('git', 'add', '-u')
    run('git', 'commit', '-m', message)  # sw.js bump guarantees a change
    run('git', 'push')
    print('pushed to main; GitHub Pages is rebuilding...')

    if not verify:
        print(f'done (verification skipped): {LIVE}')
        return
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f'{LIVE}sw.js?nocache={int(time.time())}',
                headers={'Cache-Control': 'no-cache'})
            body = urllib.request.urlopen(req, timeout=15).read().decode()
            if new_version in body:
                print(f'VERIFIED: {LIVE} is serving {new_version}')
                print('(open tabs pick it up on next reload; hard-refresh Cmd+Shift+R if impatient)')
                return
        except Exception:
            pass
        time.sleep(10)
        print('  ...waiting for Pages build')
    print(f'NOT VERIFIED after 5 min — check https://github.com/simonvangend-lang/jarabi-tab/actions')
    sys.exit(1)


if __name__ == '__main__':
    main()
