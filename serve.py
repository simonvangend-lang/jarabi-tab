#!/usr/bin/env python3
"""
Local dev server for the Jarabi tab player.

Serves static files + accepts POST /save to write notes.json in-place.

If AUTOPUSH=1 (default), each save is auto-committed and pushed to GitHub
so the hosted version stays in sync. Set AUTOPUSH=0 to disable.

Run: python3 serve.py
"""
import json, os, re, subprocess, threading, time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8899
ROOT = os.path.dirname(os.path.abspath(__file__))
SCORES_DIR = os.path.join(ROOT, 'scores')
ID_OK = re.compile(r'^[a-zA-Z0-9_-]+$')
AUTOPUSH = os.environ.get('AUTOPUSH', '1') != '0'

# Debounce: when several saves arrive in quick succession, fold them into one
# commit + push instead of spamming git history.
_pending = set()
_lock = threading.Lock()
_timer = [None]
DEBOUNCE_SEC = 4.0


def _git(*args, check=True):
    return subprocess.run(['git', *args], cwd=ROOT, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _do_push():
    with _lock:
        ids = sorted(_pending)
        _pending.clear()
        _timer[0] = None
    if not ids:
        return
    paths = [f'scores/{i}.json' for i in ids]
    try:
        _git('add', *paths)
        # If nothing's actually changed (e.g. the save was identical), skip.
        if not _git('diff', '--cached', '--quiet', check=False).returncode:
            print('autopush: no staged changes, nothing to commit')
            return
        names = ', '.join(ids)
        _git('-c', 'commit.gpgsign=false', 'commit', '-m', f'Edit via in-app save: {names}')
        _git('push', 'origin', 'main')
        print(f'autopush: pushed {names}')
    except subprocess.CalledProcessError as e:
        print(f'autopush failed: {e.stderr.strip() or e}')


def _schedule_push(score_id):
    with _lock:
        _pending.add(score_id)
        if _timer[0] is not None:
            _timer[0].cancel()
        _timer[0] = threading.Timer(DEBOUNCE_SEC, _do_push)
        _timer[0].daemon = True
        _timer[0].start()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/save':
            qs = parse_qs(parsed.query)
            score_id = qs.get('id', [''])[0]
            if not score_id or not ID_OK.match(score_id):
                self.send_response(400); self.end_headers()
                self.wfile.write(b'Bad or missing ?id=')
                return
            length = int(self.headers.get('Content-Length', 0))
            body   = self.rfile.read(length)
            try:
                data = json.loads(body)
                os.makedirs(SCORES_DIR, exist_ok=True)
                path = os.path.join(SCORES_DIR, f'{score_id}.json')
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                print(f'Saved scores/{score_id}.json ({len(body)} bytes)')
                if AUTOPUSH:
                    _schedule_push(score_id)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # Suppress normal request noise; only print saves
        pass

print(f'Serving on http://localhost:{PORT}'
      + ('  ·  AUTOPUSH on' if AUTOPUSH else '  ·  AUTOPUSH off'))
HTTPServer(('', PORT), Handler).serve_forever()
