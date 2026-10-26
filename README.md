# nl2sql (prototype)

Ask a question in plain English and get back a SQL query, its results, and a short explanation.
Uses Claude (`claude-opus-5-5`) with structured outputs. Works with SQLite, PostgreSQL and MySQL,
always read-only. Comes with a command line, a local web page, and an accuracy eval.

```
$ python -m nl2sql "Which customers spent the most on shipped orders?"

SQL:
  SELECT c.name, SUM(p.price_cents * oi.quantity) / 100.0 AS total_spent ...

name         | total_spent
-------------+------------
Lucas Martin | 249.0
...
```

## Setup

```bash
pip install -e ".[dev]"            # SQLite only
pip install -e ".[dev,postgres]"   # + PostgreSQL
pip install -e ".[dev,mysql]"      # + MySQL / MariaDB
```

Set `ANTHROPIC_API_KEY` (or log in with `ant auth login`).

## Usage

```bash
python -m nl2sql "How many orders were cancelled?"   # one question; creates the small demo sample.db if missing
python -m nl2sql --db bank.db "Which loans are in default?"   # the bank database (built on first use)
python -m nl2sql                                     # interactive
python -m nl2sql --db path/to/file.db "..."          # your own SQLite file
python -m nl2sql --db postgresql+psycopg://nl2sql_reader:PASSWORD@localhost/bank "..."
python -m nl2sql --db mysql+pymysql://nl2sql_reader:PASSWORD@localhost/bank "..."
```

### Web UI

```bash
python -m nl2sql.web --db bank.db    # http://127.0.0.1:8000
python -m nl2sql.web --db postgresql+psycopg://... --port 9000
```

A single page: type a question (Ctrl+Enter), see the SQL with a copy button, the explanation and the
result table. It listens on localhost only by default. Don't expose it to a network without adding
authentication: anyone who can reach it can query the database and spend your API credit.

### From Python

```python
from nl2sql import NL2SQL, open_database

engine = NL2SQL(open_database("bank.db"))   # or a postgresql+psycopg:// / mysql+pymysql:// URL
res = engine.ask("Top 3 merchants by card spending in September")
print(res.answer.sql, res.result.rows)
```

## The bank database

A realistic retail bank to try questions against, in [db/bank/](db/bank/). It has 10 tables:

- **People:** `branches` (8 branches in US cities) and `employees` (46 staff with a management chain, loan officers and fraud analysts).
- **Customers:** `customers` (250, with KYC status and risk rating), their `accounts` (checking, savings and business) and debit `cards`.
- **Money movement:** `merchants`, and about 15,000 `transactions` from January to October 2025 (salaries, card payments, ATM withdrawals, transfers, fees and interest).
- **Lending:** `loans` and their monthly `loan_payments`, some late and some missed.
- **Risk:** `fraud_alerts` raised on card payments.

The data is simulated so that it stays consistent:
- Every account balance equals the sum of its transactions, and nothing goes negative.
- Transfers appear on both accounts.
- Loan instalments are debited from the borrower's checking account.
- Fraud alerts point at real card payments.

`tests/test_bank.py` checks all of this.

| File | What it is |
|---|---|
| `db/bank/schema.sql` | Tables, keys and constraints, with comments explaining each column |
| `db/bank/seed.sql` | The data (generated; rebuild with `python -m nl2sql.bank_db --write-sql db/bank`) |
| `db/bank/postgres_reader.sh`, `mysql_reader.sh` | Create `nl2sql_reader`, a login that can only `SELECT` |
| `nl2sql/bank_db.py` | The generator, and `--sqlite FILE` to build a SQLite copy |
| `nl2sql/data/bank_eval.jsonl` | 20 eval questions about the bank |

Both `.sql` files are plain SQL that runs unchanged on PostgreSQL, MySQL 8 and SQLite.

### Running it on a real database server

With [Docker](https://docs.docker.com/get-docker/) installed:

```bash
docker compose up -d                   # PostgreSQL 16 on localhost:5432, loaded with the bank data
docker compose --profile mysql up -d   # also MySQL 8.4 on localhost:3306
```

Then connect as the read-only user. The default password is `change-me-reader`; set
`BANK_READER_PASSWORD` and `BANK_ADMIN_PASSWORD` in a `.env` file before the first start to change it.

```bash
pip install -e ".[postgres]"
python -m nl2sql.web --db postgresql+psycopg://nl2sql_reader:change-me-reader@localhost/bank
```

Without Docker, load the same two files with your database's own client, e.g.
`psql -d bank -f db/bank/schema.sql -f db/bank/seed.sql`.

## How it works

1. **Schema** (`schema.py`): `CREATE TABLE` statements plus a couple of sample rows per table go into
   the system prompt, which is prompt-cached across questions.
2. **Large schemas**: with more than 30 tables, each question gets only the relevant ones (up to 12),
   picked by word overlap with table and column names, plus the tables they join to through foreign
   keys. The rest are listed by name, so Claude can say when it needs a table it can't see.
3. **Generation** (`generator.py`): `client.messages.parse(..., output_format=SQLAnswer)` returns a
   validated `{answerable, sql, explanation}`. Questions the database can't answer are reported, not guessed.
4. **Read-only, twice over**: `guard.py` only accepts a single `SELECT`/`WITH` query, and every database
   connection is itself read-only: SQLite opens with `mode=ro` and `PRAGMA query_only`; PostgreSQL and MySQL
   sessions are set to `READ ONLY` transactions with a 15 s statement timeout. For Postgres/MySQL, also connect
   as a database user that only has `SELECT` rights.
5. **Self-repair**: if the SQL fails, the error goes back to Claude for one more attempt.
6. **Refusals**: requests opt into server-side refusal fallbacks (`fallbacks: "default"`); a final
   `refusal` stop reason surfaces as an `NL2SQLError`.

## Measuring accuracy

There are two question sets of 20 each: `demo` (the small shop in `sample.db`) and `bank`. In each, 18
questions have a reference ("gold") query and 2 should be answered as unanswerable.

```bash
python -m nl2sql.eval --set bank --check-gold        # check the gold queries run (no API calls)
python -m nl2sql.eval --set bank --out results.json  # run all 20 through Claude and report accuracy
```

A question passes when its results match the gold query's results. Column order, extra columns and
small rounding differences are ignored, and row order only counts where the question asks for it.
Use `--file` and `--db` to run your own question set against your own database.

## Tests

```bash
pytest
```

70 tests, using a fake Claude client, so no API key or network is needed. The database tests run on
both the SQLite driver and SQLAlchemy.

## Not yet done

- No live run against the API yet, so the evals have no baseline score.
- The bank SQL and Docker setup are only tested on SQLite so far; PostgreSQL and MySQL haven't been
  run against real servers.
- The bank generator reads `db/bank/` from the repository, so build the bank database from a checkout
  (or an editable install), not from an installed package.
- Table selection is keyword-based; embeddings would handle questions that use different words from the schema.
