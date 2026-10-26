import pytest

from conftest import FakeClient
from nl2sql import NL2SQL, SQLAnswer
from nl2sql.db import QueryResult
from nl2sql.eval import DEMO_CASES, Case, check_gold, load_cases, results_match, run_eval


def qr(*rows):
    return QueryResult(["x"] * (len(rows[0]) if rows else 0), list(rows), False)


def test_demo_gold_queries_all_run(db):
    cases = load_cases(DEMO_CASES)
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)
    assert check_gold(db, cases) == []


@pytest.mark.parametrize("gold, pred, ordered, expected", [
    (qr((1,)), qr((1.0,)), False, True),                              # int vs float
    (qr(("a", 1), ("b", 2)), qr(("b", 2), ("a", 1)), False, True),    # row order ignored
    (qr(("a", 1), ("b", 2)), qr(("b", 2), ("a", 1)), True, False),    # ...unless ordered
    (qr(("Chair",)), qr(("Chair", 249.0)), False, True),              # extra column ok
    (qr(("a", 1)), qr((1, "a")), False, True),                        # column order ignored
    (qr(("a",)), qr(("a",), ("b",)), False, False),                   # row count differs
    (qr((464.0,)), qr((464.001,)), False, True),                      # rounding
    (qr((1,)), qr((2,)), False, False),
])
def test_results_match(gold, pred, ordered, expected):
    assert results_match(gold, pred, ordered) is expected


def test_run_eval_scores_cases(db):
    cases = [
        Case("ok", "How many customers?", "SELECT COUNT(*) FROM customers"),
        Case("wrong", "How many orders?", "SELECT COUNT(*) FROM orders"),
        Case("unans", "Weather?", None),
        Case("missed", "How many products?", "SELECT COUNT(*) FROM products"),
    ]
    client = FakeClient(
        SQLAnswer(answerable=True, sql="SELECT COUNT(*) AS n FROM customers", explanation=""),
        SQLAnswer(answerable=True, sql="SELECT COUNT(*) FROM customers", explanation=""),
        SQLAnswer(answerable=False, sql="", explanation="no weather"),
        SQLAnswer(answerable=False, sql="", explanation="unsure"),
    )
    results = run_eval(NL2SQL(db, client=client), cases)
    assert [r.passed for r in results] == [True, False, True, False]
    assert results[1].reason == "results differ"
    assert results[3].reason.startswith("said unanswerable")
