"""Execution-accuracy eval: run each question through NL2SQL and compare results with a gold query.

    python -m nl2sql.eval                       # demo set against sample.db (calls the API)
    python -m nl2sql.eval --check-gold          # only check that the gold queries run (free)
    python -m nl2sql.eval --file my_cases.jsonl --db postgresql://... --out results.json

Each line of the cases file: {"id", "question", "gold_sql" (null = should be unanswerable),
"ordered" (optional, row order must match)}.

Matching is deliberately lenient about presentation: column order and extra columns are
ignored (a predicted row passes if it contains every value of its gold row), numbers are
compared rounded to 2 decimals. Row count must match.
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .db import Database, QueryResult
from .generator import NL2SQL, NL2SQLError

DEMO_CASES = Path(__file__).parent / "data" / "demo_eval.jsonl"


@dataclass
class Case:
    id: str
    question: str
    gold_sql: str | None
    ordered: bool = False


@dataclass
class CaseResult:
    id: str
    question: str
    passed: bool
    reason: str
    predicted_sql: str | None = None
    attempts: int = 0


def load_cases(path: str | Path) -> list[Case]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [Case(**json.loads(line)) for line in lines if line.strip()]


def _norm(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    try:  # Decimal from Postgres/MySQL
        return round(float(value), 2) if type(value).__name__ == "Decimal" else str(value)
    except (TypeError, ValueError):
        return str(value)


def _contains(pred: Counter, gold: Counter) -> bool:
    return all(pred[k] >= n for k, n in gold.items())


def results_match(gold: QueryResult, pred: QueryResult, ordered: bool = False) -> bool:
    if len(gold.rows) != len(pred.rows):
        return False
    g = [Counter(_norm(v) for v in row) for row in gold.rows]
    p = [Counter(_norm(v) for v in row) for row in pred.rows]
    if ordered:
        return all(_contains(pr, gr) for pr, gr in zip(p, g))
    unused = list(range(len(p)))
    for gr in g:
        match = next((i for i in unused if _contains(p[i], gr)), None)
        if match is None:
            return False
        unused.remove(match)
    return True


def check_gold(db: Database, cases: list[Case]) -> list[str]:
    """Return problems with gold queries (empty list = all fine)."""
    problems = []
    for case in cases:
        if case.gold_sql is None:
            continue
        try:
            db.run(case.gold_sql, max_rows=10_000)
        except db.errors as exc:
            problems.append(f"{case.id}: {exc}")
    return problems


def run_case(engine: NL2SQL, case: Case) -> CaseResult:
    try:
        res = engine.ask(case.question)
    except NL2SQLError as exc:
        return CaseResult(case.id, case.question, False, f"error: {exc}")

    sql, attempts = res.answer.sql or None, res.attempts
    if case.gold_sql is None:
        ok = not res.answer.answerable
        return CaseResult(case.id, case.question, ok,
                          "correctly unanswerable" if ok else "answered an unanswerable question",
                          sql, attempts)
    if not res.answer.answerable:
        return CaseResult(case.id, case.question, False,
                          f"said unanswerable: {res.answer.explanation}", sql, attempts)

    gold = engine.db.run(case.gold_sql, max_rows=10_000)
    pred = res.result
    if pred.truncated:
        pred = engine.db.run(res.answer.sql, max_rows=10_000)
    ok = results_match(gold, pred, case.ordered)
    return CaseResult(case.id, case.question, ok, "match" if ok else "results differ", sql, attempts)


def run_eval(engine: NL2SQL, cases: list[Case], progress=None) -> list[CaseResult]:
    results = []
    for case in cases:
        result = run_case(engine, case)
        results.append(result)
        if progress:
            progress(result)
    return results


def main() -> None:
    from .__main__ import add_db_args, open_db

    parser = argparse.ArgumentParser(prog="nl2sql.eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_db_args(parser)
    parser.add_argument("--file", default=str(DEMO_CASES), help="cases file (.jsonl)")
    parser.add_argument("--out", help="write per-case results to this JSON file")
    parser.add_argument("--check-gold", action="store_true",
                        help="only check that gold queries run; makes no API calls")
    args = parser.parse_args()

    db = open_db(args.db)
    cases = load_cases(args.file)
    problems = check_gold(db, cases)
    if problems:
        print("Gold queries that fail:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)
    if args.check_gold:
        print(f"All {len(cases)} gold queries run.")
        return

    engine = NL2SQL(db, model=args.model)
    print(f"Running {len(cases)} cases with {args.model}...\n")
    results = run_eval(engine, cases, progress=lambda r: print(
        f"  {'PASS' if r.passed else 'FAIL'}  {r.id:<24} {'' if r.passed else r.reason}"))

    passed = sum(r.passed for r in results)
    print(f"\nAccuracy: {passed}/{len(results)} ({passed / len(results):.0%})")
    if args.out:
        Path(args.out).write_text(json.dumps([asdict(r) for r in results], indent=2))
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
