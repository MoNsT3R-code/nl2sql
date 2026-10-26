# nl2sql (prototype)

Ask a question in plain English, get back a SQL query, its results, and a short explanation.
Uses Claude (`claude-opus-5-5`) with structured outputs against a SQLite database.

```
$ python -m nl2sql "Which customers spent the most on shipped orders?"

SQL:
  SELECT c.name, SUM(p.price_cents * oi.quantity) / 100.0 AS total_spent ...

name         | total_spent
-------------+------------
Lucas Martin | 249.0
...
```

## How it works

1. **Schema introspection** (`schema.py`) – reads `CREATE TABLE` statements plus a couple of
   sample rows per table and puts them in the system prompt (prompt-cached across questions).
2. **Generation** (`generator.py`) – `client.messages.parse(..., output_format=SQLAnswer)` returns
   a validated `{answerable, sql, explanation}` object. Unanswerable questions are reported, not guessed.
3. **Safety** – `guard.py` rejects anything that isn't a single `SELECT`/`WITH` query, and
   `db.py` opens the database read-only (`mode=ro` + `PRAGMA query_only`), so even a query that slips
   past the guard can't write.
4. **Self-repair** – if the SQL fails (bad column, guard rejection), the error is sent back and Claude
   gets one more attempt.
5. **Refusals** – requests opt into server-side refusal fallbacks (`fallbacks: "default"`); a final
   `refusal` stop reason surfaces as an `NL2SQLError`.

## Setup

```bash
pip install -e ".[dev]"
```

Set `ANTHROPIC_API_KEY` (or log in with `ant auth login`).

## Usage

```bash
python -m nl2sql "How many orders were cancelled?"   # one-shot; creates sample.db if missing
python -m nl2sql                                     # interactive
python -m nl2sql --db path/to/your.db "..."          # your own SQLite file
```

From Python:

```python
from nl2sql import NL2SQL
from nl2sql.db import connect_readonly

engine = NL2SQL(connect_readonly("sample.db"))
res = engine.ask("Top 3 products by units sold")
print(res.answer.sql, res.result.rows)
```

## Tests

```bash
pytest
```

Tests use a fake client, so they need no API key and make no network calls.

## Not yet done

- Only SQLite; Postgres/MySQL would need a dialect-aware prompt and a read-only DB role.
- Large schemas are sent in full; past a few hundred tables you'd want table retrieval first.
- No eval set yet – a small question→expected-result set would be the next step for measuring accuracy.
