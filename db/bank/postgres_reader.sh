#!/bin/sh
# Runs once when the Postgres container first starts (after schema.sql and seed.sql).
# Creates the login nl2sql uses: it can read every table and nothing else.
set -e

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$READER_PASSWORD" <<'EOSQL'
CREATE ROLE nl2sql_reader LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE bank TO nl2sql_reader;
GRANT USAGE ON SCHEMA public TO nl2sql_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_reader;
ALTER ROLE nl2sql_reader SET default_transaction_read_only = on;
ALTER ROLE nl2sql_reader SET statement_timeout = '15s';
EOSQL
