"""Read-only loopback focus signal. Never exposes tasks, history or credentials."""
from __future__ import annotations
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT = 47821

def focus_payload(active, age):
    return {'app': 'tomato-focus-v1', 'focus': bool(
        0 <= age < 20 and active and active.get('running') is True and active.get('phase') == 'work')}

class FocusBridge:
    def __init__(self, port=PORT):
        self._lock = threading.Lock()
        self._active = None
        self._updated = time.monotonic()
        self.server = None
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                origin = self.headers.get('Origin', '')
                if self.headers.get('Host') != f'127.0.0.1:{bridge.port}':
                    self.send_error(403); return
                if origin and not origin.startswith('chrome-extension://'):
                    self.send_error(403); return
                if urlsplit(self.path).path != '/focus':
                    self.send_error(404); return
                payload = json.dumps(bridge.payload()).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        try:
            self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            self.server.daemon_threads = True
            self.port = self.server.server_port
            threading.Thread(target=self.server.serve_forever, daemon=True, name='tomato-focus-bridge').start()
        except OSError:
            # Manual extension sessions still work if another process occupies the port.
            self.server = None

    def update(self, active):
        with self._lock:
            self._active = {'running': active.get('running'), 'phase': active.get('phase')} if active else None
            self._updated = time.monotonic()

    def payload(self):
        with self._lock:
            return focus_payload(self._active, time.monotonic() - self._updated)

    def close(self):
        self.update(None)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
