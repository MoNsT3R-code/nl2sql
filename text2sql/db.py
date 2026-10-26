"""Read-only SQLite access."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def run_query(conn: sqlite3.Connection, sql: str, max_rows: int = 200) -> QueryResult:
    cur = conn.execute(sql)
    columns = [d[0] for d in cur.description or []]
    rows = cur.fetchmany(max_rows + 1)
    return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)


def format_table(result: QueryResult) -> str:
    if not result.columns:
        return "(no columns)"
    cells = [[str(v) for v in row] for row in result.rows]
    widths = [
        max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(result.columns)
    ]
    line = lambda vals: " | ".join(v.ljust(w) for v, w in zip(vals, widths))
    out = [line(result.columns), "-+-".join("-" * w for w in widths)]
    out += [line(r) for r in cells]
    if not cells:
        out.append("(no rows)")
    if result.truncated:
        out.append(f"... truncated to {len(cells)} rows")
    return "\n".join(out)
