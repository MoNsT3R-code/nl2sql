"""CLI: python -m text2sql [--db sample.db] ["question"]  (no question -> interactive)."""

import argparse
import sys
from pathlib import Path

from .db import connect_readonly, format_table
from .generator import DEFAULT_MODEL, Text2SQL, Text2SQLError
from .sample_db import build


def answer(engine: Text2SQL, question: str) -> None:
    try:
        res = engine.ask(question)
    except Text2SQLError as exc:
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="text2sql", description=__doc__)
    parser.add_argument("question", nargs="?", help="question in plain English")
    parser.add_argument("--db", default="sample.db", help="SQLite file (default: sample.db)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if not Path(args.db).exists():
        if args.db != "sample.db":
            parser.error(f"{args.db} does not exist")
        build(args.db)
        print(f"created demo database {args.db}")

    engine = Text2SQL(connect_readonly(args.db), model=args.model)
    if args.question:
        answer(engine, args.question)
        return

    print("Ask a question (empty line to quit).")
    while question := input("\n> ").strip():
        answer(engine, question)


if __name__ == "__main__":
    main()
