# Banking Management System: ERD, schema and data

A complete data model for a Pakistani retail bank: 20 tables, 20 primary keys and 50 foreign keys,
each table with 10 attributes and 10 example values. It covers branches and staff, customers and KYC,
Asaan, Roshan Digital, Kids Club and Teens Club accounts, member cards (PayPak, World, Platinum),
Raast and 1LINK payments, merchants, loans and transactions.

| File | What it is |
|---|---|
| [`banking-erd.html`](banking-erd.html) | Interactive ERD. Open it in a browser: search tables, columns and values; switch between table names, keys and all columns; connect any two tables to get the join path and SQL; follow guided banking stories |
| [`banking-erd.png`](banking-erd.png) | The full diagram as an image |
| [`banking-erd-example-values.png`](banking-erd-example-values.png) | Ten example values for every table |
| [`banking_schema.sql`](banking_schema.sql) | PostgreSQL schema: 20 tables, 50 foreign keys, unique rules on every real-world identifier (CNIC, IBAN, email, card and loan numbers), format and value checks, and an index on every foreign key |
| [`generate_bank_data.py`](generate_bank_data.py) | Generates realistic linked data for the schema. Defaults to 1,000 customers and scales to 500,000 |
| [`bank_data.sql`](bank_data.sql) | 1,000 generated customers, ready to load |

## Load it

```bash
createdb bank
psql -d bank -v ON_ERROR_STOP=1 -f banking_schema.sql -f bank_data.sql
```

Tested on PostgreSQL 16: the schema and data load with every unique, check and foreign-key rule enforced,
every account balance equals its last transaction, and no balance goes negative.

## Make more data

```bash
python generate_bank_data.py --customers 500000 --out bank_data_500k.sql
```

Plain Python, nothing to install. 20,000 customers took 11 seconds to generate and 23 seconds to load, so
500,000 should take about 5 minutes and 10 minutes respectively, with a file of about 1.4 GB. The same
`--seed` always produces the same data. All names, CNICs and account numbers are made up; IBANs use the
fictional bank code NBMS.

## Demo: ask the bank questions in plain English, free and offline

```bash
pip install pgserver sqlalchemy "psycopg[binary]" pydantic
ollama pull qwen2.5-coder:3b        # optional: the free local AI (about 2 GB, needs Ollama installed)
python erd/run_demo.py              # opens http://127.0.0.1:8000
```

`run_demo.py` starts a private PostgreSQL on this computer, loads the bank the first time, and opens the
`nl2sql` web app on it. No API key and no internet connection are needed:

- **25 rehearsed questions** ([`demo_questions.json`](demo_questions.json)) answer instantly with SQL
  checked against this data, for example "Which Asaan accounts are dormant?" or "How many Raast
  transfers were made and what was their total value?". Rephrasings still match.
- **Any other question** goes to a small open model (`qwen2.5-coder:3b`) running in Ollama on the same
  laptop. Nothing leaves the machine. On a laptop CPU an answer takes from several seconds to about a
  minute; the page shows a timer while it works.
- Every answer is labelled with who wrote the SQL (rehearsed, local AI or Claude), shows the SQL, and runs
  read-only.

With an Anthropic API key, `python -m nl2sql.web --db postgresql+psycopg://postgres@127.0.0.1:55432/bank`
uses Claude instead, which is more accurate on hard questions.
