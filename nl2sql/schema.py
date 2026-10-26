"""Introspect a SQLite database into a compact schema description for the prompt."""

import sqlite3


def describe_schema(conn: sqlite3.Connection, sample_rows: int = 2) -> str:
    """Return CREATE statements plus a few sample rows per table."""
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    parts = []
    for name, create_sql in tables:
        parts.append(create_sql.strip() + ";")
        if sample_rows > 0:
            cur = conn.execute(f'SELECT * FROM "{name}" LIMIT ?', (sample_rows,))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            if rows:
                parts.append(f"-- sample rows from {name} ({', '.join(cols)}):")
                parts.extend(f"--   {row!r}" for row in rows)
        parts.append("")
    return "\n".join(parts).strip()
