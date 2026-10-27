"""The free, offline answering path: a local model in Ollama, and rehearsed questions."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nl2sql import NL2SQL, NL2SQLError, SQLAnswer
from nl2sql.local import LocalAIError, OllamaClient, RehearsedClient

REHEARSED = [
    {"question": "How many customers does the bank have?", "sql": "SELECT count(*) AS n FROM customers",
     "explanation": "Counts customers."},
    {"question": "What is the credit score of our customers?", "sql": None, "explanation": "Not stored."},
]


@pytest.fixture
def ollama():
    """A stand-in for Ollama's /api/chat and /api/tags that replies with queued answers."""
    state = {"replies": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply({"models": [{"name": "qwen2.5-coder:3b"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(body)
            self._reply({"message": {"role": "assistant", "content": state["replies"].pop(0)}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()
    srv.server_close()


def answer(sql, answerable=True, explanation="x"):
    return json.dumps({"answerable": answerable, "sql": sql, "explanation": explanation})


def test_local_ai_answers_and_repairs(db, ollama):
    ollama["replies"] += [answer("SELECT nme FROM customers"), answer("SELECT name FROM customers WHERE id = 1")]
    engine = NL2SQL(db, client=OllamaClient(url=ollama["url"]))
    res = engine.ask("Name of customer 1?")

    assert res.result.rows == [("Ayesha Khan",)]
    assert res.attempts == 2 and res.source == "local:qwen2.5-coder:3b"
    first, retry = ollama["requests"]
    assert first["format"]["properties"].keys() >= {"answerable", "sql", "explanation"}  # structured output
    assert first["options"]["temperature"] == 0
    assert first["messages"][0]["role"] == "system" and "CREATE TABLE customers" in first["messages"][0]["content"]
    assert "failed" in retry["messages"][-1]["content"]  # the error goes back for the repair


def test_local_ai_json_wrapped_in_text_is_accepted(db, ollama):
    ollama["replies"].append("Sure! " + answer("SELECT COUNT(*) FROM orders") + " Hope that helps.")
    res = NL2SQL(db, client=OllamaClient(url=ollama["url"])).ask("How many orders?")
    assert res.result.rows == [(6,)]


def test_local_ai_not_running_is_a_clear_error(db):
    client = OllamaClient(url="http://127.0.0.1:9")  # nothing listens here
    assert client.available() == (False, "Ollama isn't running.")
    with pytest.raises(NL2SQLError, match="local AI isn't running"):
        NL2SQL(db, client=client).ask("How many orders?")
    assert issubclass(LocalAIError, NL2SQLError)


def test_rehearsed_questions_answer_without_the_ai(db, ollama):
    engine = NL2SQL(db, client=RehearsedClient(REHEARSED, fallback=OllamaClient(url=ollama["url"])))
    res = engine.ask("how many customers does the bank have")  # different case and punctuation
    assert res.result.rows == [(5,)] and res.source == "rehearsed"
    no = engine.ask("What is the credit score of our customers?")
    assert not no.answer.answerable and no.source == "rehearsed"
    assert ollama["requests"] == []  # the AI was never called


def test_other_questions_go_to_the_local_ai(db, ollama):
    ollama["replies"].append(answer("SELECT COUNT(*) FROM products"))
    engine = NL2SQL(db, client=RehearsedClient(REHEARSED, fallback=OllamaClient(url=ollama["url"])))
    res = engine.ask("How many products do we sell?")
    assert res.result.rows == [(5,)] and res.source.startswith("local:")
    assert len(ollama["requests"]) == 1


def test_without_an_ai_unknown_questions_explain_what_to_do(db):
    res = NL2SQL(db, client=RehearsedClient(REHEARSED)).ask("How many products do we sell?")
    assert not res.answer.answerable
    assert "Ollama" in res.answer.explanation
