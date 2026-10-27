#!/bin/sh
# Runs once when the MySQL container first starts (after schema.sql and seed.sql).
# Creates the login nl2sql uses: it can read the bank database and nothing else.
set -e

mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<EOSQL
CREATE USER 'nl2sql_reader'@'%' IDENTIFIED BY '${READER_PASSWORD}';
GRANT SELECT ON bank.* TO 'nl2sql_reader'@'%';
EOSQL
