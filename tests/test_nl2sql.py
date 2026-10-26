import sqlite3
from types import SimpleNamespace

import pytest

from nl2sql import SQLAnswer, NL2SQL, NL2SQLError
from nl2sql.db import connect_readonly, format_table, run_query
from nl2sql.guard import UnsafeSQLError, check_sql
from nl2sql.sample_db import build
from nl2sql.schema import describe_schema


@pytest.fixture
def conn(tmp_path):
    path = build(tmp_path / "store.db")
    c = connect_readonly(path)
    yield c
    c.close()


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued SQLAnswers from messages.parse."""

    def __init__(self, *answers, stop_reason="end_turn"):
        self.answers = list(answers)
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)
        self.stop_reason = stop_reason

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=answer,
                               content=[{"type": "text", "text": answer.model_dump_json()}])


# --- guard ---------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "  select name from customers;  ",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT name FROM customers WHERE name = 'DROP TABLE; x'",
    "-- leading comment\nSELECT 1",
])
def test_guard_allows_reads(sql):
    assert check_sql(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "SELECT 1; DROP TABLE customers",
    "UPDATE customers SET name = 'x'",
    "WITH t AS (SELECT 1) DELETE FROM customers",
    "PRAGMA table_info(customers)",
    "",
    "-- only a comment",
])
def test_guard_rejects_writes(sql):
    with pytest.raises(UnsafeSQLError):
        check_sql(sql)


# --- db / schema -----------------------------------------------------------

def test_connection_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM customers")


def test_schema_lists_all_tables(conn):
    schema = describe_schema(conn)
    for table in ("customers", "products", "orders", "order_items"):
        assert f"CREATE TABLE {table}" in schema
    assert "sample rows from customers" in schema


def test_run_query_truncates(conn):
    result = run_query(conn, "SELECT id FROM customers ORDER BY id", max_rows=2)
    assert result.rows == [(1,), (2,)]
    assert result.truncated
    assert "truncated" in format_table(result)


# --- generator ---------------------------------------------------------------

def test_ask_runs_generated_sql(conn):
    client = FakeClient(SQLAnswer(
        answerable=True,
        sql="SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY country;",
        explanation="Counts customers per country.",
    ))
    res = NL2SQL(conn, client=client).ask("How many customers per country?")

    assert res.attempts == 1
    assert res.result.columns == ["country", "n"]
    assert ("France", 1) in res.result.rows
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is SQLAnswer
    assert "CREATE TABLE orders" in call["system"][0]["text"]


def test_ask_repairs_after_sql_error(conn):
    client = FakeClient(
        SQLAnswer(answerable=True, sql="SELECT nme FROM customers", explanation="typo"),
        SQLAnswer(answerable=True, sql="SELECT name FROM customers WHERE id = 1", explanation="fixed"),
    )
    res = NL2SQL(conn, client=client).ask("Name of customer 1?")

    assert res.attempts == 2
    assert res.result.rows == [("Ayesha Khan",)]
    assert "no such column" in res.errors[0]
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "no such column" in retry_messages[-1]["content"]


def test_ask_rejects_unsafe_sql_then_gives_up(conn):
    bad = SQLAnswer(answerable=True, sql="DROP TABLE customers", explanation="")
    client = FakeClient(bad, bad)
    with pytest.raises(NL2SQLError, match="gave up"):
        NL2SQL(conn, client=client).ask("Delete everything")
    # Table is still there.
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone() == (5,)


def test_unanswerable_question(conn):
    client = FakeClient(SQLAnswer(answerable=False, sql="", explanation="No weather data."))
    res = NL2SQL(conn, client=client).ask("What's the weather?")
    assert res.result is None
    assert not res.answer.answerable


def test_refusal_raises(conn):
    client = FakeClient(SQLAnswer(answerable=True, sql="SELECT 1", explanation=""),
                        stop_reason="refusal")
    with pytest.raises(NL2SQLError, match="declined"):
        NL2SQL(conn, client=client).ask("?")
