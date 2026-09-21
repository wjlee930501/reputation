# /// script
# requires-python = ">=3.11"
# dependencies = ["redis"]
# ///
# How to run: docker compose service fixture (production image dependencies).
"""Test-only provider failure and cache-invalidation acceptance boundaries."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import redis

broker = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                             socket_timeout=5, socket_connect_timeout=5)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/api/v1/images":
            broker.incr("rehearsal:provider_calls")
            code, payload = 503, {"error": "TEST_ONLY_PROVIDER_UNAVAILABLE"}
        elif self.path == "/revalidate":
            if self.headers.get("x-revalidate-secret") != "test-only-revalidation":
                self.send_error(403)
                return
            paths = json.loads(body)["paths"]
            assert paths and all(path in {"/", "/sitemap.xml", "/llms.txt"} or path.startswith("/rehearsal-integrity") for path in paths)
            broker.rpush("rehearsal:callbacks", body)
            broker.set("rehearsal:callback_entered", "1")
            deadline = time.monotonic() + 120
            while broker.get("rehearsal:block_callback") == "1":
                if time.monotonic() > deadline:
                    self.send_error(504)
                    return
                time.sleep(0.1)
            broker.incr("rehearsal:callback_accepted")
            code, payload = 200, {"revalidated": True, "provenance": "TEST_ONLY"}
        else:
            broker.rpush("rehearsal:unexpected_requests", self.path)
            code, payload = 503, {"error": "UNEXPECTED_TEST_BOUNDARY"}
        encoded = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            self.log_message("test worker disconnected during simulated loss")


ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
