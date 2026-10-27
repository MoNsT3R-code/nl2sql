"""The bank database: SQL files load cleanly, the data is internally consistent, and the
committed seed.sql matches what the generator produces."""

import pytest

from nl2sql.bank_db import SQL_DIR, build_sqlite, generate, seed_sql
from nl2sql.db import SQLiteDatabase
from nl2sql.eval import BANK_CASES, check_gold, load_cases

# Each query counts rows that break an invariant; all must be zero.
INVARIANTS = {
    "foreign key violations": "SELECT COUNT(*) FROM pragma_foreign_key_check",
    "balance differs from transactions": """
        SELECT COUNT(*) FROM accounts a
        WHERE ROUND(a.balance - COALESCE(
            (SELECT SUM(amount) FROM transactions t WHERE t.account_id = a.id), 0), 2) <> 0""",
    "negative balances": "SELECT COUNT(*) FROM accounts WHERE balance < 0",
    "closed accounts holding money": "SELECT COUNT(*) FROM accounts WHERE status = 'closed' AND balance <> 0",
    "transfers without a matching side": """
        SELECT COUNT(*) FROM transactions o
        WHERE o.txn_type = 'transfer_out' AND NOT EXISTS (
            SELECT 1 FROM transactions i
            WHERE i.txn_type = 'transfer_in' AND i.account_id = o.counterparty_account_id
              AND i.counterparty_account_id = o.account_id AND i.posted_on = o.posted_on
              AND i.amount = -o.amount)""",
    "transactions outside the account's life": """
        SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE t.posted_on < a.opened_on OR (a.closed_on IS NOT NULL AND t.posted_on > a.closed_on)""",
    "fraud alerts on non-card transactions": """
        SELECT COUNT(*) FROM fraud_alerts f JOIN transactions t ON t.id = f.transaction_id
        WHERE t.txn_type <> 'card_payment'""",
    "card payments without card or merchant": """
        SELECT COUNT(*) FROM transactions
        WHERE txn_type = 'card_payment' AND (card_id IS NULL OR merchant_id IS NULL)""",
    "2025 loan payments without a debit": """
        SELECT COUNT(*) FROM loan_payments lp
        WHERE lp.paid_on >= '2025-01-01' AND NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE t.txn_type = 'loan_payment' AND t.posted_on = lp.paid_on
              AND t.description LIKE 'Loan #' || lp.loan_id || ' %')""",
}


@pytest.fixture(scope="module")
def bank(tmp_path_factory):
    db = SQLiteDatabase(build_sqlite(tmp_path_factory.mktemp("bank") / "bank.db"))
    yield db
    db.close()


def test_committed_seed_is_up_to_date():
    # If this fails, run: python -m nl2sql.bank_db --write-sql db/bank
    committed = (SQL_DIR / "seed.sql").read_text(encoding="utf-8")
    assert seed_sql(generate()) == committed


def test_generator_is_deterministic():
    assert generate(seed=7) == generate(seed=7)


@pytest.mark.parametrize("name", INVARIANTS)
def test_invariant(bank, name):
    assert bank.run(INVARIANTS[name]).rows == [(0,)], name


def test_has_interesting_data(bank):
    """Every state the eval questions ask about actually occurs."""
    def count(sql):
        return bank.run(sql).rows[0][0]

    assert count("SELECT COUNT(*) FROM customers WHERE kyc_status = 'pending'") > 0
    assert count("SELECT COUNT(*) FROM loans WHERE status = 'defaulted'") > 0
    assert count("SELECT COUNT(*) FROM loans WHERE status = 'paid_off'") > 0
    assert count("SELECT COUNT(*) FROM loan_payments WHERE paid_on > due_on") > 0
    assert count("SELECT COUNT(*) FROM fraud_alerts WHERE confirmed_fraud = TRUE") > 0
    assert count("SELECT COUNT(*) FROM fraud_alerts WHERE reviewed_by IS NULL") > 0
    assert count("SELECT COUNT(*) FROM accounts WHERE status = 'frozen'") > 0
    assert count("SELECT COUNT(*) FROM transactions") > 10_000


def test_bank_gold_queries_run(bank):
    cases = load_cases(BANK_CASES)
    assert len(cases) == 20 and len({c.id for c in cases}) == 20
    assert check_gold(bank, cases) == []


def test_schema_sees_all_tables(bank):
    names = {t.name for t in bank.tables()}
    assert names == {"branches", "employees", "customers", "accounts", "cards", "merchants",
                     "transactions", "loans", "loan_payments", "fraud_alerts"}
    loans = next(t for t in bank.tables() if t.name == "loans")
    assert loans.references == ["branches", "customers", "employees"]
