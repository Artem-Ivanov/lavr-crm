"""Bounded, in-memory fake Bot API. Development only; never forwards requests."""
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket

FAKE_TOKEN = '123456:development-only-token-000000000'
MAX_BODY = 65536
RESPONSES = {200, 400, 401, 403, 404, 429, 500, 502, 503, 'malformed', 'disconnect'}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        # Bot API paths contain credentials; this fake needs no request logs.
        pass

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def body(self):
        length = self.headers.get('Content-Length', '')
        if self.headers.get('Transfer-Encoding') or not length.isdecimal():
            self.reply(400, {'ok': False, 'description': 'Invalid body length'})
            return None
        size = int(length)
        if size > MAX_BODY:
            self.reply(413, {'ok': False, 'description': 'Body too large'})
            return None
        try:
            raw = self.rfile.read(size)
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError, OSError):
            self.reply(400, {'ok': False, 'description': 'Invalid JSON'})
            return None
        if not isinstance(data, dict):
            self.reply(400, {'ok': False, 'description': 'Expected object'})
            return None
        return data

    def do_GET(self):
        if self.path == '/healthz':
            self.reply(200, {'ok': True})
        elif self.path == '/messages':
            self.reply(200, {'messages': list(self.server.messages)})
        else:
            self.reply(404, {'ok': False})

    def do_DELETE(self):
        if self.path != '/messages':
            self.reply(404, {'ok': False})
            return
        self.server.messages.clear()
        self.server.responses.clear()
        self.server.next_message_id = 1000
        self.reply(200, {'ok': True})

    def do_POST(self):
        if self.path not in ('/control', f'/bot{FAKE_TOKEN}/sendMessage'):
            self.reply(404, {'ok': False})
            return
        data = self.body()
        if data is None:
            return
        if self.path == '/control':
            responses = data.get('responses')
            if not isinstance(responses, list) or len(responses) > 100 or any(
                type(value) not in (int, str) or value not in RESPONSES for value in responses
            ):
                self.reply(400, {'ok': False, 'description': 'Invalid scripted responses'})
                return
            self.server.responses = deque(responses)
            self.reply(200, {'ok': True})
            return
        if not isinstance(data.get('chat_id'), (str, int)) or isinstance(data.get('chat_id'), bool) or not isinstance(data.get('text'), str) or not data['text']:
            self.reply(400, {'ok': False, 'description': 'Expected chat_id and text'})
            return
        scripted = self.server.responses.popleft() if self.server.responses else 200
        status = scripted if type(scripted) is int else 200
        message_id = None
        if scripted == 200:
            self.server.next_message_id += 1
            message_id = self.server.next_message_id
        self.server.messages.append({'payload': data, 'status': status, 'message_id': message_id, 'response': scripted})
        if scripted == 'disconnect':
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
        elif scripted == 'malformed':
            self.send_response(200)
            self.send_header('Content-Length', '1')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(b'{')
            self.close_connection = True
        elif status == 200:
            self.reply(200, {'ok': True, 'result': {'message_id': message_id, 'chat': {'id': data['chat_id']}, 'text': data['text']}})
        elif status == 429:
            self.reply(429, {'ok': False, 'error_code': 429, 'description': 'Synthetic rate limit', 'parameters': {'retry_after': 1}})
        else:
            self.reply(status, {'ok': False, 'error_code': status, 'description': 'Synthetic failure'})


def make_server(address):
    # Serial requests and per-connection timeouts bound concurrent memory/work.
    server = HTTPServer(address, Handler)
    server.messages = deque(maxlen=10000)
    server.responses = deque()
    server.next_message_id = 1000
    return server


if __name__ == '__main__':
    with make_server(('0.0.0.0', 8080)) as server:
        server.serve_forever()
