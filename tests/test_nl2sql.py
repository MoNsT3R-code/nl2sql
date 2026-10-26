import pytest

from conftest import FakeClient
from nl2sql import NL2SQL, NL2SQLError, SQLAnswer
from nl2sql.db import TableInfo, format_table, open_database
from nl2sql.guard import UnsafeSQLError, check_sql
from nl2sql.schema import describe_schema, select_tables


# --- guard ---------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "  select name from customers;  ",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT name FROM customers WHERE name = 'DROP TABLE; x'",
    "-- leading comment\nSELECT 1",
    "SELECT name FROM customers LIMIT 5 OFFSET 2",
])
def test_guard_allows_reads(sql):
    assert check_sql(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "SELECT 1; DROP TABLE customers",
    "UPDATE customers SET name = 'x'",
    "WITH t AS (SELECT 1) DELETE FROM customers",
    "PRAGMA table_info(customers)",
    "SELECT * INTO backup FROM customers",
    "SELECT * FROM customers FOR UPDATE",
    "",
    "-- only a comment",
])
def test_guard_rejects_writes(sql):
    with pytest.raises(UnsafeSQLError):
        check_sql(sql)


# --- db / schema (both backends) -------------------------------------------

def test_connection_is_read_only(db):
    with pytest.raises(db.errors):
        db.run("DELETE FROM customers")
    assert db.run("SELECT COUNT(*) FROM customers").rows == [(5,)]


def test_tables_and_foreign_keys(db):
    tables = {t.name: t for t in db.tables()}
    assert set(tables) == {"customers", "products", "orders", "order_items"}
    assert tables["orders"].references == ["customers"]
    assert tables["order_items"].references == ["orders", "products"]
    assert "price_cents" in tables["products"].columns


def test_schema_description(db):
    schema = describe_schema(db, db.tables(), other_tables=["audit_log"])
    for table in ("customers", "products", "orders", "order_items"):
        assert f"CREATE TABLE {table}" in schema
    assert "sample rows from customers" in schema
    assert "audit_log" in schema


def test_run_query_truncates(db):
    result = db.run("SELECT id FROM customers ORDER BY id", max_rows=2)
    assert result.columns == ["id"]
    assert result.rows == [(1,), (2,)]
    assert result.truncated
    assert "truncated" in format_table(result)


def test_open_database_picks_backend(db_path):
    assert open_database(db_path).dialect == "SQLite"
    pytest.importorskip("sqlalchemy")
    assert type(open_database(f"sqlite:///{db_path.as_posix()}")).__name__ == "SQLAlchemyDatabase"


# --- table selection ---------------------------------------------------------

def _table(name, cols, refs=()):
    return TableInfo(name, f"CREATE TABLE {name} (...)", cols, list(refs))


def test_select_tables_ranks_and_adds_join_partners():
    tables = [_table("customers", ["id", "name", "country"]),
              _table("orders", ["id", "customer_id", "status"], ["customers"]),
              _table("invoices", ["id", "order_id", "amount"], ["orders"])]
    tables += [_table(f"misc_{i}", ["id", "value"]) for i in range(50)]

    chosen = [t.name for t in select_tables(tables, "How many cancelled orders per country?", 5)]
    assert "orders" in chosen and "customers" in chosen
    assert "invoices" in chosen  # references a chosen table
    assert not any(n.startswith("misc_") for n in chosen)


def test_select_tables_falls_back_when_nothing_matches():
    tables = [_table(f"t{i}", ["a"]) for i in range(20)]
    assert len(select_tables(tables, "zzz", 4)) == 4


# --- generator ---------------------------------------------------------------

def test_ask_runs_generated_sql(db):
    client = FakeClient(SQLAnswer(
        answerable=True,
        sql="SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY country;",
        explanation="Counts customers per country.",
    ))
    res = NL2SQL(db, client=client).ask("How many customers per country?")

    assert res.attempts == 1
    assert res.result.columns == ["country", "n"]
    assert ("France", 1) in res.result.rows
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is SQLAnswer
    assert "CREATE TABLE orders" in call["system"][0]["text"]
    assert "single SQLite SELECT" in call["system"][0]["text"]


def test_large_schema_sends_only_selected_tables(db):
    client = FakeClient(SQLAnswer(answerable=True, sql="SELECT COUNT(*) FROM orders",
                                  explanation=""))
    engine = NL2SQL(db, client=client, full_schema_limit=2, max_tables=2)
    res = engine.ask("How many orders are there?")

    system = client.calls[0]["system"][0]["text"]
    assert "CREATE TABLE orders" in system
    assert "CREATE TABLE products" not in system
    assert "Other tables in this database" in system and "products" in system
    assert "orders" in res.tables and len(res.tables) <= 2


def test_ask_repairs_after_sql_error(db):
    client = FakeClient(
        SQLAnswer(answerable=True, sql="SELECT nme FROM customers", explanation="typo"),
        SQLAnswer(answerable=True, sql="SELECT name FROM customers WHERE id = 1", explanation="fixed"),
    )
    res = NL2SQL(db, client=client).ask("Name of customer 1?")

    assert res.attempts == 2
    assert res.result.rows == [("Ayesha Khan",)]
    assert "nme" in res.errors[0]
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "failed" in retry_messages[-1]["content"]


def test_ask_rejects_unsafe_sql_then_gives_up(db):
    bad = SQLAnswer(answerable=True, sql="DROP TABLE customers", explanation="")
    client = FakeClient(bad, bad)
    with pytest.raises(NL2SQLError, match="gave up"):
        NL2SQL(db, client=client).ask("Delete everything")
    assert db.run("SELECT COUNT(*) FROM customers").rows == [(5,)]


def test_unanswerable_question(db):
    client = FakeClient(SQLAnswer(answerable=False, sql="", explanation="No weather data."))
    res = NL2SQL(db, client=client).ask("What's the weather?")
    assert res.result is None
    assert not res.answer.answerable


def test_refusal_raises(db):
    client = FakeClient(SQLAnswer(answerable=True, sql="SELECT 1", explanation=""),
                        stop_reason="refusal")
    with pytest.raises(NL2SQLError, match="declined"):
        NL2SQL(db, client=client).ask("?")
