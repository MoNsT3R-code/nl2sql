"""Local web UI: python -m nl2sql.web [--db ...] [--port 8000], then open http://127.0.0.1:8000

Uses only the standard library. Connections get their own threads (browsers open idle
spare connections that would otherwise block a single-threaded server), but questions
are answered one at a time under a lock, so the database connection is never shared.

Web protections, on top of the engine's SecurityLayer:
  * listens on localhost only, and refuses any other address unless an access token is set
  * optional access token (--token or NL2SQL_TOKEN): open the page once as /?token=..., which sets
    an HttpOnly, SameSite=Strict cookie; the API also accepts "Authorization: Bearer <token>"
  * questions must be JSON from the same origin, so other websites can't submit them (CSRF)
  * rate limit per client address (default 20 questions a minute)
  * strict response headers: Content-Security-Policy, no framing, no referrer, no caching
"""

import argparse
import hmac
import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .generator import NL2SQL, NL2SQLError

INDEX_HTML = Path(__file__).parent / "static" / "index.html"
MAX_BODY = 16 * 1024
MAX_QUESTION = 2000
COOKIE = "nl2sql_token"
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def result_payload(res) -> dict:
    payload = {
        "question": res.question,
        "answerable": res.answer.answerable,
        "sql": res.answer.sql,
        "explanation": res.answer.explanation,
        "attempts": res.attempts,
        "repairs": res.errors,
        "tables": res.tables,
        "source": res.source,
        "masked": res.masked,
    }
    if res.result is not None:
        payload.update(columns=res.result.columns, rows=res.result.rows,
                       truncated=res.result.truncated)
    return payload


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[client]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def make_handler(engine: NL2SQL, model: str, token: str | None = None, rate_limit: int = 20):
    lock = threading.Lock()
    limiter = RateLimiter(rate_limit)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict, extra: dict | None = None) -> None:
            # default=str covers dates and Decimals from Postgres/MySQL.
            body = json.dumps(data, default=str).encode()
            self._send(status, body, "application/json", extra)

        def _authorised(self) -> bool:
            if not token:
                return True
            supplied = None
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    supplied = supplied or value
            return supplied is not None and hmac.compare_digest(supplied, token)

        def _same_origin(self) -> bool:
            if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                given = parse_qs(url.query).get("token", [None])[0]
                if token and given and hmac.compare_digest(given, token):
                    # Swap the token in the address bar for a cookie, so it isn't left in history.
                    return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {
                        "Location": "/", "Set-Cookie": f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"})
                if not self._authorised():
                    return self._send(HTTPStatus.UNAUTHORIZED,
                                      b"<!doctype html><title>Access token required</title><p>This nl2sql app needs an "
                                      b"access token. Open the link you were given, which ends in <code>?token=...</code>.",
                                      "text/html; charset=utf-8")
                self._send(HTTPStatus.OK, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/info":
                if not self._authorised():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "access token required"})
                self._json(HTTPStatus.OK, {
                    "dialect": engine.db.dialect,
                    "tables": [t.name for t in engine.tables],
                    "model": model,
                    "security": bool(engine.security),
                })
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if urlsplit(self.path).path != "/api/ask":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._authorised():
                return self._json(HTTPStatus.UNAUTHORIZED, {"error": "access token required"})
            if not self._same_origin():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "requests from other websites aren't accepted"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send JSON"})
            client = self.client_address[0]
            if not limiter.allow(client):
                return self._json(HTTPStatus.TOO_MANY_REQUESTS,
                                  {"error": f"Too many questions. The limit is {limiter.per_minute} a minute."},
                                  {"Retry-After": "60"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request size"})
            try:
                question = str(json.loads(self.rfile.read(length))["question"]).strip()
            except (ValueError, KeyError, TypeError):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "expected {\"question\": ...}"})
            if not question or len(question) > MAX_QUESTION:
                return self._json(HTTPStatus.BAD_REQUEST,
                                  {"error": f"question must be 1-{MAX_QUESTION} characters"})
            try:
                with lock:
                    payload = result_payload(engine.ask(question, user=client))
                self._json(HTTPStatus.OK, payload)
            except NL2SQLError as exc:
                if hasattr(exc, "rule"):  # a SecurityError
                    return self._json(HTTPStatus.FORBIDDEN, {"error": str(exc), "blocked": True, "rule": exc.rule})
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})

        def log_request(self, code="-", size="-"):  # keep the console quiet apart from errors
            if isinstance(code, int) and code >= 400:
                super().log_request(code, size)

    return Handler


def serve(engine: NL2SQL, model: str, host: str = "127.0.0.1", port: int = 8000,
          token: str | None = None, rate_limit: int = 20) -> ThreadingHTTPServer:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not token:
        raise SystemExit(f"Refusing to listen on {host} without an access token: anyone on the network could "
                         "query the database. Set --token (or NL2SQL_TOKEN), or use 127.0.0.1.")
    return ThreadingHTTPServer((host, port), make_handler(engine, model, token, rate_limit))


def main() -> None:
    from .__main__ import add_db_args, make_engine

    parser = argparse.ArgumentParser(prog="nl2sql.web", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_db_args(parser)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to listen on (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", default=os.environ.get("NL2SQL_TOKEN"),
                        help="require this access token (default: $NL2SQL_TOKEN, if set)")
    parser.add_argument("--rate-limit", type=int, default=20, help="questions per minute per client (default 20)")
    args = parser.parse_args()

    engine = make_engine(args)
    server = serve(engine, args.model, args.host, args.port, args.token, args.rate_limit)
    link = f"http://{args.host}:{args.port}" + (f"/?token={args.token}" if args.token else "")
    print(f"nl2sql web UI on {link}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
