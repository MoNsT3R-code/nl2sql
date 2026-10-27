"""Local web UI: python -m nl2sql.web [--db ...] [--port 8000], then open http://127.0.0.1:8000

Uses only the standard library. Connections get their own threads (browsers open idle
spare connections that would otherwise block a single-threaded server), but questions
are answered one at a time under a lock, so the database connection is never shared.
"""

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .generator import NL2SQL, NL2SQLError

INDEX_HTML = Path(__file__).parent / "static" / "index.html"
MAX_BODY = 16 * 1024
MAX_QUESTION = 2000


def result_payload(res) -> dict:
    payload = {
        "question": res.question,
        "answerable": res.answer.answerable,
        "sql": res.answer.sql,
        "explanation": res.answer.explanation,
        "attempts": res.attempts,
        "repairs": res.errors,
        "tables": res.tables,
    }
    if res.result is not None:
        payload.update(columns=res.result.columns, rows=res.result.rows,
                       truncated=res.result.truncated)
    return payload


def make_handler(engine: NL2SQL, model: str):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            # default=str covers dates and Decimals from Postgres/MySQL.
            body = json.dumps(data, default=str).encode()
            self._send(status, body, "application/json")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/info":
                self._json(HTTPStatus.OK, {
                    "dialect": engine.db.dialect,
                    "tables": [t.name for t in engine.tables],
                    "model": model,
                })
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/ask":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
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
                    payload = result_payload(engine.ask(question))
                self._json(HTTPStatus.OK, payload)
            except NL2SQLError as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})

        def log_request(self, code="-", size="-"):  # keep the console quiet apart from errors
            if isinstance(code, int) and code >= 400:
                super().log_request(code, size)

    return Handler


def serve(engine: NL2SQL, model: str, host: str = "127.0.0.1",
          port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(engine, model))


def main() -> None:
    from .__main__ import add_db_args, make_engine

    parser = argparse.ArgumentParser(prog="nl2sql.web", description=__doc__)
    add_db_args(parser)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to listen on (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    engine = make_engine(args)
    server = serve(engine, args.model, args.host, args.port)
    print(f"nl2sql web UI on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
