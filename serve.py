#!/usr/bin/env python3
"""
Local dev server for Jarabi tab player.
Serves static files + accepts POST /save to write notes.json in-place.
Run: python3 serve.py
"""
import json, os, re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8899
ROOT = os.path.dirname(os.path.abspath(__file__))
SCORES_DIR = os.path.join(ROOT, 'scores')
ID_OK = re.compile(r'^[a-zA-Z0-9_-]+$')

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

print(f'Serving on http://localhost:{PORT}')
HTTPServer(('', PORT), Handler).serve_forever()
