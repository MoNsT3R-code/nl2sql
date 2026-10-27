"""Read-only database access.

SQLite files use the stdlib driver. Anything given as a URL (postgresql://, mysql+pymysql://,
sqlite:///...) goes through SQLAlchemy, which is an optional dependency.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


@dataclass
class TableInfo:
    name: str
    ddl: str
    columns: list[str]
    references: list[str]  # tables this one has foreign keys to


class Database:
    """Minimal interface the generator needs. Subclasses must keep every query read-only."""

    dialect: str = ""
    errors: tuple[type[Exception], ...] = ()

    def tables(self) -> list[TableInfo]:
        raise NotImplementedError

    def sample_rows(self, table: str, n: int) -> QueryResult:
        raise NotImplementedError

    def run(self, sql: str, max_rows: int = 200) -> QueryResult:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SQLiteDatabase(Database):
    dialect = "SQLite"
    errors = (sqlite3.Error,)

    def __init__(self, path: str | Path):
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        # The web UI serves from a worker thread; access is still one request at a time.
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.conn.execute("PRAGMA query_only = ON")

    def tables(self) -> list[TableInfo]:
        rows = self.conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        infos = []
        for name, ddl in rows:
            cols = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{name}")')]
            refs = sorted({r[2] for r in self.conn.execute(f'PRAGMA foreign_key_list("{name}")')})
            infos.append(TableInfo(name, ddl.strip(), cols, refs))
        return infos

    def sample_rows(self, table: str, n: int) -> QueryResult:
        return self.run(f'SELECT * FROM "{table}" LIMIT {int(n)}', max_rows=n)

    def run(self, sql: str, max_rows: int = 200) -> QueryResult:
        cur = self.conn.execute(sql)
        return _collect(cur, max_rows)

    def close(self) -> None:
        self.conn.close()


# Statements that make every transaction on a new connection read-only.
_READ_ONLY_SESSION = {
    "postgresql": "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
    "mysql": "SET SESSION TRANSACTION READ ONLY",
    "mariadb": "SET SESSION TRANSACTION READ ONLY",
    "sqlite": "PRAGMA query_only = ON",
}
_DIALECT_NAMES = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "sqlite": "SQLite"}


class SQLAlchemyDatabase(Database):
    def __init__(self, url: str, statement_timeout_ms: int = 15000):
        try:
            import sqlalchemy
        except ImportError as exc:
            raise ImportError(
                "database URLs need SQLAlchemy: pip install 'nl2sql[postgres]' or 'nl2sql[mysql]'"
            ) from exc

        self.engine = sqlalchemy.create_engine(url)
        name = self.engine.dialect.name
        if name not in _READ_ONLY_SESSION:
            raise ValueError(f"unsupported database dialect: {name}")
        self.dialect = _DIALECT_NAMES[name]
        self.errors = (sqlalchemy.exc.SQLAlchemyError,)

        setup = [_READ_ONLY_SESSION[name]]
        if name == "postgresql":
            setup.append(f"SET statement_timeout = {int(statement_timeout_ms)}")
        elif name == "mysql":
            setup.append(f"SET SESSION max_execution_time = {int(statement_timeout_ms)}")

        @sqlalchemy.event.listens_for(self.engine, "connect")
        def _make_read_only(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            for stmt in setup:
                cur.execute(stmt)
            cur.close()

    def tables(self) -> list[TableInfo]:
        import sqlalchemy

        insp = sqlalchemy.inspect(self.engine)
        infos = []
        for name in sorted(insp.get_table_names()):
            cols = insp.get_columns(name)
            pk = insp.get_pk_constraint(name).get("constrained_columns") or []
            fks = insp.get_foreign_keys(name)
            lines = [
                f"    {c['name']} {c['type']}{'' if c.get('nullable', True) else ' NOT NULL'}"
                for c in cols
            ]
            if pk:
                lines.append(f"    PRIMARY KEY ({', '.join(pk)})")
            for fk in fks:
                lines.append(
                    f"    FOREIGN KEY ({', '.join(fk['constrained_columns'])}) REFERENCES "
                    f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"
                )
            # CHECK rules list the allowed values of status-like columns ('active', 'blocked', ...).
            # They are part of the structure, not data, and the model needs them to spell values right.
            try:
                checks = insp.get_check_constraints(name)
            except NotImplementedError:
                checks = []
            for ck in checks:
                if allowed := _allowed_values(ck["sqltext"]):
                    lines.append(f"    CHECK ({allowed})")
            ddl = f"CREATE TABLE {name} (\n" + ",\n".join(lines) + "\n)"
            refs = sorted({fk["referred_table"] for fk in fks})
            infos.append(TableInfo(name, ddl, [c["name"] for c in cols], refs))
        return infos

    def sample_rows(self, table: str, n: int) -> QueryResult:
        quoted = self.engine.dialect.identifier_preparer.quote(table)
        return self.run(f"SELECT * FROM {quoted} LIMIT {int(n)}", max_rows=n)

    def run(self, sql: str, max_rows: int = 200) -> QueryResult:
        with self.engine.connect() as conn:
            try:
                # exec_driver_sql: no SQLAlchemy bind-param parsing of ":name" in the query.
                return _collect(conn.exec_driver_sql(sql), max_rows)
            finally:
                conn.rollback()

    def close(self) -> None:
        self.engine.dispose()


def _allowed_values(check_sql: str) -> str | None:
    """Rewrite a CHECK that lists allowed values compactly: status IN ('active', 'blocked').

    PostgreSQL reports `status IN (...)` as `(status)::text = ANY ((ARRAY['active'::character varying,
    ...])::text[])`. Other CHECKs (ranges, formats) are left out to keep the prompt short."""
    m = re.search(r"\(?(\w+)\)?(?:::[\w ]+)?\s*(?:=\s*ANY|IN)\s*\(+\s*(?:ARRAY\[)?(.*)", check_sql, re.IGNORECASE)
    if not m:
        return None
    values = re.findall(r"'((?:[^']|'')*)'", m.group(2))
    if not values:
        return None
    return f"{m.group(1)} IN ({', '.join(repr(v) for v in values)})"


def open_database(target: str | Path) -> Database:
    """A filesystem path opens SQLite directly; a URL (anything with ://) uses SQLAlchemy."""
    if isinstance(target, str) and "://" in target:
        return SQLAlchemyDatabase(target)
    return SQLiteDatabase(target)


def _collect(cursor, max_rows: int) -> QueryResult:
    # SQLAlchemy results expose keys(); DB-API cursors expose description.
    if hasattr(cursor, "keys"):
        columns = list(cursor.keys())
    else:
        columns = [d[0] for d in cursor.description or []]
    rows = [tuple(r) for r in cursor.fetchmany(max_rows + 1)]
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
