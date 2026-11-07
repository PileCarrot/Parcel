"""Loopback-only, token-authenticated Edge to Parcel bridge."""
import json
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Bridge:
    def __init__(self, app, data_dir, port=43831):
        self.app = app
        self.requests = queue.Queue()
        self.token_path = Path(data_dir) / 'intercept-token.txt'
        if not self.token_path.exists():
            self.token_path.write_text(secrets.token_urlsafe(32), encoding='ascii')
        self.token = self.token_path.read_text(encoding='ascii').strip()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != '/v1/tomato' or self.headers.get('X-Parcel-Token') != bridge.token:
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size < 1 or size > 1024:
                        raise ValueError('invalid size')
                    command = json.loads(self.rfile.read(size))['command']
                    if command not in ('prepare', 'show'):
                        raise ValueError('invalid command')
                    reply = queue.Queue(maxsize=1)
                    bridge.requests.put((command, reply))
                    result = reply.get(timeout=5)
                    body = json.dumps(result).encode()
                    self.send_response(200)
                except (ValueError, KeyError, queue.Empty):
                    body = b'{}'
                    self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def poll(self):
        while not self.requests.empty():
            command, reply = self.requests.get_nowait()
            reply.put(self.app.intercept_command(command))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
