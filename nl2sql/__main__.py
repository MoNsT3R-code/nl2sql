"""CLI: python -m nl2sql [--db sample.db | --db postgresql://...] ["question"]  (no question -> interactive)."""

import argparse
import sys
from pathlib import Path

from .db import Database, format_table, open_database
from .generator import DEFAULT_MODEL, NL2SQL, NL2SQLError
from .sample_db import build


def answer(engine: NL2SQL, question: str) -> None:
    try:
        res = engine.ask(question)
    except NL2SQLError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return
    if not res.answer.answerable:
        print(f"Can't answer from this database: {res.answer.explanation}")
        return
    print(f"\nSQL:\n  {res.answer.sql}\n")
    print(format_table(res.result))
    print(f"\n{res.answer.explanation}")
    if res.errors:
        print(f"(repaired after: {'; '.join(res.errors)})")


def open_db(target: str) -> Database:
    """Open a SQLite path or database URL. sample.db and bank.db are created on first use."""
    if "://" not in target and not Path(target).exists():
        name = Path(target).name
        if name == "sample.db":
            build(target)
        elif name == "bank.db":
            from .bank_db import build_sqlite

            build_sqlite(target)
        else:
            raise SystemExit(f"{target} does not exist")
        print(f"created database {target}")
    return open_database(target)


def add_db_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="sample.db",
                        help="SQLite file or database URL, e.g. postgresql+psycopg://user@host/bank "
                             "(default: sample.db; sample.db and bank.db are created if missing)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sample-rows", type=int, default=0, metavar="N",
                        help="also send the AI N example rows per table (default 0: privacy mode, "
                             "only the question and the table structure are sent)")
    parser.add_argument("--allow-personal-data", action="store_true",
                        help="show CNICs, mobiles, emails, IBANs etc. unmasked (default: masked)")
    parser.add_argument("--audit", default="nl2sql-audit.jsonl", metavar="FILE",
                        help="where to record every question, query and decision (default nl2sql-audit.jsonl)")


def make_engine(args) -> NL2SQL:
    from .security import SecurityLayer, SecurityPolicy

    db = open_db(args.db)
    security = SecurityLayer(db, SecurityPolicy(mask_personal_data=not args.allow_personal_data))
    return NL2SQL(db, model=args.model, sample_rows=args.sample_rows, security=security, audit_log=args.audit)


def main() -> None:
    parser = argparse.ArgumentParser(prog="nl2sql", description=__doc__)
    parser.add_argument("question", nargs="?", help="question in plain English")
    add_db_args(parser)
    args = parser.parse_args()

    engine = make_engine(args)
    if args.question:
        answer(engine, args.question)
        return

    print("Ask a question (empty line to quit).")
    while question := input("\n> ").strip():
        answer(engine, question)


if __name__ == "__main__":
    main()
