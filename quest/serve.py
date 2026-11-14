"""Dev server for the Quest viewer: serves the repo root on 127.0.0.1:8001 with caching off,
so a refresh in the headset always loads the newest modules (nested ES imports can't carry ?v=).

    python3 quest/serve.py          # then: adb reverse tcp:8001 tcp:8001
    open http://localhost:8001/quest/?mock=1&debug=1 in the Quest browser
"""
import functools
import http.server
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("PORT", "8001"))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=ROOT)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as httpd:
        print(f"serving {ROOT} on http://127.0.0.1:{PORT}/quest/")
        httpd.serve_forever()
