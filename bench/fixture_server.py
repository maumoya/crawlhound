"""Serve tests/fixtures/site on 127.0.0.1 and log every request (method, path) as JSON lines.

Write methods get 405 and are logged: the benchmark counts how many writes reached the server."""
import http.server
import json
import sys
import threading
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "site"


def make_handler(log_path: Path, origin_for_sitemap: str):
    lock = threading.Lock()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(SITE), **kw)

        def log_message(self, *a):
            pass

        def _log(self):
            with lock, open(log_path, "a") as fh:
                fh.write(json.dumps({"method": self.command, "path": self.path,
                                     "ua": self.headers.get("User-Agent", "")}) + "\n")

        def do_GET(self):
            self._log()
            path = self.path.split("?")[0]
            if path == "/sitemap.xml":
                body = (SITE / "sitemap.xml").read_text().replace("{ORIGIN}", origin_for_sitemap).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/app.html":
                self.path = "/app.html"
            super().do_GET()

        def do_HEAD(self):
            self._log()
            super().do_HEAD()

        def _write(self):
            self._log()
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _write

    return Handler


def start(log_path: Path, host: str = "127.0.0.1", public_host: str = "127.0.0.1"):
    """Start in a thread; returns (server, origin as the crawler should see it)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    server = http.server.ThreadingHTTPServer((host, 0), make_handler(log_path, ""))
    origin = f"http://{public_host}:{server.server_address[1]}"
    server.RequestHandlerClass = make_handler(log_path, origin)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, origin


if __name__ == "__main__":
    srv, org = start(Path(sys.argv[1]))
    print(org, flush=True)
    threading.Event().wait()
