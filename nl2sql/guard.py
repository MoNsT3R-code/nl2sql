"""Cheap static checks on generated SQL.

This is a first line of defence only; the real guarantee is that queries run on a
read-only connection (see db.py).
"""

import re

_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX"
    r"|TRUNCATE|GRANT|REVOKE|MERGE|COPY|CALL|EXEC|EXECUTE|SET|LOCK|INTO|LOAD|HANDLER)\b",
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS = re.compile(r"'(?:[^']|'')*'")


class UnsafeSQLError(ValueError):
    pass


def check_sql(sql: str) -> str:
    """Return the cleaned SQL if it looks like a single read-only query, else raise."""
    cleaned = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeSQLError("empty query")

    # Ignore keywords that only appear inside string literals.
    code_only = _STRINGS.sub("''", cleaned)
    if ";" in code_only:
        raise UnsafeSQLError("multiple statements are not allowed")
    if not _ALLOWED_START.match(code_only):
        raise UnsafeSQLError("only SELECT / WITH queries are allowed")
    match = _FORBIDDEN.search(code_only)
    if match:
        raise UnsafeSQLError(f"forbidden keyword: {match.group(1).upper()}")
    return cleaned
