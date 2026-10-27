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

## Ask it questions in plain English

The `nl2sql` tool at the root of this repository can query this database:

```bash
python -m nl2sql --db postgresql+psycopg://postgres@localhost/bank "Which customers missed a loan instalment?"
```
