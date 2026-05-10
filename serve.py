#!/usr/bin/env python3
"""
Local dev server for Jarabi tab player.
Serves static files + accepts POST /save to write notes.json in-place.
Run: python3 serve.py
"""
import json, os
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8899
ROOT = os.path.dirname(os.path.abspath(__file__))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_POST(self):
        if self.path == '/save':
            length = int(self.headers.get('Content-Length', 0))
            body   = self.rfile.read(length)
            try:
                data = json.loads(body)
                path = os.path.join(ROOT, 'notes.json')
                with open(path, 'w') as f:
                    json.dump(data, f)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                print(f'Saved notes.json ({len(body)} bytes)')
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
