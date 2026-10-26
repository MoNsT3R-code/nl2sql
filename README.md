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
python -m nl2sql "How many orders were cancelled?"   # one question; creates the demo sample.db if missing
python -m nl2sql                                     # interactive
python -m nl2sql --db path/to/file.db "..."          # your own SQLite file
python -m nl2sql --db postgresql://readonly@localhost/shop "..."
python -m nl2sql --db mysql+pymysql://readonly@localhost/shop "..."
```

### Web UI

```bash
python -m nl2sql.web                 # http://127.0.0.1:8000
python -m nl2sql.web --db postgresql://... --port 9000
```

A single page: type a question (Ctrl+Enter), see the SQL with a copy button, the explanation and the
result table. It listens on localhost only by default. Don't expose it to a network without adding
authentication: anyone who can reach it can query the database and spend your API credit.

### From Python

```python
from nl2sql import NL2SQL, open_database

engine = NL2SQL(open_database("sample.db"))   # or a postgresql:// / mysql:// URL
res = engine.ask("Top 3 products by units sold")
print(res.answer.sql, res.result.rows)
```

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

`nl2sql/data/demo_eval.jsonl` holds 20 questions about the demo database. 18 have a reference ("gold")
query, and 2 should be answered as unanswerable.

```bash
python -m nl2sql.eval --check-gold        # check the gold queries run (no API calls)
python -m nl2sql.eval --out results.json  # run all 20 through Claude and report accuracy
```

A question passes when its results match the gold query's results. Column order, extra columns and
small rounding differences are ignored, and row order only counts where the question asks for it.
Use `--file` and `--db` to run your own question set against your own database.

## Tests

```bash
pytest
```

55 tests, using a fake Claude client, so no API key or network is needed. The database tests run on
both the SQLite driver and SQLAlchemy.

## Not yet done

- No live run against the API yet, so the eval has no baseline score.
- PostgreSQL and MySQL are untested against real servers; the SQLAlchemy path is tested through SQLite.
- Table selection is keyword-based; embeddings would handle questions that use different words from the schema.
