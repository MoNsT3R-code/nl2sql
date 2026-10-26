import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import FakeClient
from nl2sql import NL2SQL, SQLAnswer
from nl2sql.db import SQLiteDatabase
from nl2sql.web import serve


@pytest.fixture
def server(db_path):
    client = FakeClient(
        SQLAnswer(answerable=True, sql="SELECT name, price_cents FROM products ORDER BY id LIMIT 2",
                  explanation="First two products."),
        SQLAnswer(answerable=False, sql="", explanation="No weather data."),
        SQLAnswer(answerable=True, sql="DROP TABLE products", explanation=""),
        SQLAnswer(answerable=True, sql="DROP TABLE products", explanation=""),
    )
    engine = NL2SQL(SQLiteDatabase(db_path), client=client, model="test-model")
    srv = serve(engine, "test-model", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.read()


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_serves_page_and_info(server):
    status, body = get(server + "/")
    assert status == 200 and b"<title>nl2sql</title>" in body
    status, body = get(server + "/api/info")
    info = json.loads(body)
    assert info["dialect"] == "SQLite" and "orders" in info["tables"]
    assert info["model"] == "test-model"


def test_ask_flow(server):
    status, data = post(server + "/api/ask", {"question": "First two products?"})
    assert status == 200
    assert data["columns"] == ["name", "price_cents"]
    assert data["rows"] == [["Mechanical Keyboard", 8900], ["USB-C Hub", 3500]]

    status, data = post(server + "/api/ask", {"question": "Weather?"})
    assert status == 200 and data["answerable"] is False

    status, data = post(server + "/api/ask", {"question": "Drop it"})
    assert status == 422 and "gave up" in data["error"]


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "x" * 3000}])
def test_rejects_bad_input(server, payload):
    status, data = post(server + "/api/ask", payload)
    assert status == 400 and "error" in data
