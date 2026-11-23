"""Shared test setup: the skill's scripts on the path, a throwaway data folder, a loopback server."""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = ROOT  # the skill folder holds its tests
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from jp import cli, http as net  # noqa: E402


class DataDirCase(unittest.TestCase):
    """Every test gets an empty data folder."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._previous = os.environ.get("JOBPILOT_DATA_DIR")
        os.environ["JOBPILOT_DATA_DIR"] = self._temp.name
        self.data_dir = self._temp.name

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("JOBPILOT_DATA_DIR", None)
        else:
            os.environ["JOBPILOT_DATA_DIR"] = self._previous
        self._temp.cleanup()

    def run_cli(self, *argv, stdin=None):
        """Run a command; returns `(exit code, parsed JSON output)`."""
        out = io.StringIO()
        previous = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out):
                code = cli.main([str(arg) for arg in argv])
        finally:
            sys.stdin = previous
        text = out.getvalue()
        return code, (json.loads(text) if text.strip() else None)

    def write(self, name, text):
        path = os.path.join(self.data_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class Loopback:
    """A local HTTP server for fixtures. `routes` maps a path (with query) to
    `(status, content type, body)` or to a function `(handler) -> that tuple`. Use as a context
    manager; it lets the client read loopback while it is open.

    The proxy variables are cleared for the duration so requests reach it directly."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __enter__(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, self.path, self.body))
                route = outer.routes.get(self.path)
                if route is None:
                    route = outer.routes.get(self.path.split("?")[0], (404, "text/plain", "not found"))
                if callable(route):
                    route = route(self)
                status, content_type, body = route[:3]
                headers = route[3] if len(route) > 3 else {}
                if isinstance(body, (dict, list)):
                    body = json.dumps(body)
                if isinstance(body, str):
                    body = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self._allow, net.TEST_ALLOW_PRIVATE = net.TEST_ALLOW_PRIVATE, True
        self._delay, net.HOST_DELAY = net.HOST_DELAY, 0
        self._client, net._shared = net._shared, net.Http(host_delay=0)
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        net.TEST_ALLOW_PRIVATE, net.HOST_DELAY, net._shared = self._allow, self._delay, self._client
