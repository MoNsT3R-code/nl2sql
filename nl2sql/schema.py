"""Turn database metadata into prompt text, and pick relevant tables for large schemas."""

import re

from .db import Database, TableInfo

_WORD = re.compile(r"[a-z0-9]+")


def describe_schema(db: Database, tables: list[TableInfo], sample_rows: int = 2,
                    other_tables: list[str] | None = None) -> str:
    """CREATE statements plus a few sample rows for each table in `tables`.

    `other_tables` are listed by name only, so the model knows they exist without
    paying for their full definitions.
    """
    parts = []
    for t in tables:
        parts.append(t.ddl.rstrip(";") + ";")
        if sample_rows > 0:
            sample = db.sample_rows(t.name, sample_rows)
            if sample.rows:
                parts.append(f"-- sample rows from {t.name} ({', '.join(sample.columns)}):")
                parts.extend(f"--   {row!r}" for row in sample.rows)
        parts.append("")
    if other_tables:
        parts.append("-- Other tables in this database (columns not shown): "
                     + ", ".join(other_tables))
    return "\n".join(parts).strip()


def _tokens(text: str) -> set[str]:
    words = set()
    for w in _WORD.findall(text.lower().replace("_", " ")):
        words.add(w)
        if len(w) > 3 and w.endswith("s"):
            words.add(w[:-1])  # crude singular: orders -> order
    return words


def select_tables(tables: list[TableInfo], question: str, max_tables: int = 12) -> list[TableInfo]:
    """Rank tables by word overlap with the question, then add their foreign-key neighbours.

    Cheap and dependency-free; good enough to cut a few hundred tables down to a handful.
    Falls back to the first `max_tables` tables when nothing matches.
    """
    q = _tokens(question)
    scored = []
    for t in tables:
        name_hits = len(q & _tokens(t.name))
        col_hits = len(q & set().union(*(_tokens(c) for c in t.columns))) if t.columns else 0
        score = 3 * name_hits + col_hits
        if score:
            scored.append((score, t.name))

    if not scored:
        return tables[:max_tables]

    by_name = {t.name: t for t in tables}
    chosen = [name for _, name in sorted(scored, key=lambda s: (-s[0], s[1]))[:max_tables]]

    # Join partners: tables the chosen ones reference, and tables that reference them.
    chosen_set = set(chosen)
    neighbours = {r for name in chosen for r in by_name[name].references}
    neighbours |= {t.name for t in tables if chosen_set & set(t.references)}
    for name in sorted(neighbours - chosen_set):
        if len(chosen) >= max_tables:
            break
        if name in by_name:
            chosen.append(name)
    return [by_name[n] for n in sorted(chosen)]
