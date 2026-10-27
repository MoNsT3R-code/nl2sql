"""Security layer: strict checks on every question, every query and every result.

Layers, each independent of the others (defence in depth):

1. Question screening: prompt-injection phrases, requests to change data, requests for
   credentials and control characters are refused before any AI sees the question.
2. SQL inspection with a real parser (sqlglot), on top of the keyword guard in guard.py:
   one read-only SELECT; no write, DDL, COPY, SET, ATTACH, PRAGMA, SELECT INTO or FOR UPDATE
   anywhere in the tree (including inside CTEs and subqueries); only the application's own
   tables (no system catalogs, no table functions such as generate_series); recursive CTEs
   refused; unknown functions refused unless allowlisted.
3. Personal data: identifiers such as CNIC, mobile, email and IBAN are masked in results. They
   may be selected only as plain columns (which get masked) or counted; anything that could
   reassemble them (substring, concatenation, selecting them in a subquery) is refused. A final
   scan masks any CNIC/IBAN/mobile/email-shaped value that still reaches the output.
4. Cost: on PostgreSQL the planner's estimate (EXPLAIN) must stay under a limit, so runaway
   joins are refused before they run.

A violation raises SecurityError. NL2SQL never retries after one, so the model can't probe for
a way round the rules.
"""

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
from sqlglot import exp

from .db import Database, QueryResult
from .generator import NL2SQLError

DIALECTS = {"SQLite": "sqlite", "PostgreSQL": "postgres", "MySQL": "mysql", "MariaDB": "mysql"}

DEFAULT_SENSITIVE = frozenset({"cnic", "mobile", "phone", "email", "iban", "account_number", "raast_alias",
                               "raast_id", "date_of_birth", "address", "document_number"})

# Statement and clause types that must not appear anywhere in a query.
_FORBIDDEN = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable", "TruncateTable", "Command",
    "Copy", "Set", "Pragma", "Attach", "Detach", "Transaction", "Commit", "Rollback", "Use", "Into", "Lock",
    "Grant", "Revoke", "LoadData", "Analyze", "Describe", "Cache", "Uncache") if hasattr(exp, name))

_BLOCKED_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast", "mysql", "performance_schema", "sys"}

# Built-in function types that reveal server details or generate unbounded rows.
_BLOCKED_FUNCTION_TYPES = {"CurrentUser", "CurrentVersion", "CurrentSchema", "CurrentCatalog", "SessionUser",
                           "ExplodingGenerateSeries", "GenerateSeries", "GenerateDateArray"}
# Functions sqlglot doesn't model are refused unless listed here (all side-effect free).
_ALLOWED_UNKNOWN_FUNCTIONS = {"to_char", "age", "initcap", "split_part", "btrim", "date_part", "strftime",
                              "julianday", "printf", "instr", "ifnull", "nullif", "greatest", "least",
                              "justify_days", "make_date", "date_format", "datediff", "char_length",
                              "regexp_replace", "trim", "ltrim", "rtrim", "lpad", "rpad", "reverse", "md5"}

_QUESTION_RULES = [  # (pattern, message)
    (r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|rules?|prompts?|guard|restrictions?|polic(y|ies))\b",
     "This looks like an attempt to override the assistant's rules."),
    (r"\b(system prompt|developer (message|mode)|jailbreak|dan mode|you are now|pretend (to be|you are)|act as (an?|the) )",
     "This looks like an attempt to change how the assistant behaves."),
    (r"\b(drop|truncate|alter|grant|revoke)\b.{0,30}\b(table|database|schema|column|privileges?|role|user)\b|"
     r"\b(delete|remove)\b.{0,30}\b(from|records?|rows?|accounts?|customers?|transactions?|data)\b|"
     r"\b(insert|add)\b.{0,15}\binto\b|\bupdate\b.{0,40}\bset\b|\b(change|modify|edit)\b.{0,30}\b(balance|record|row|account)s?\b",
     "This assistant can only read data. It can't add, change or delete records."),
    (r"\b(passwords?|passwd|credentials?|api[ _-]?keys?|secret keys?|access tokens?|private keys?|connection string)\b",
     "This assistant doesn't answer questions about passwords, keys or credentials."),
    (r"(;\s*--|/\*|\*/|\bunion\b\s+\bselect\b|'\s*or\s*'?\d'?\s*=\s*'?\d)",
     "This looks like SQL injection rather than a question."),
]

_PII_PATTERNS = [  # values that look like personal identifiers, wherever they appear in output
    (re.compile(r"\b(\d{5})-\d{7}-(\d)\b"), lambda m: f"{m.group(1)}-*******-{m.group(2)}"),            # CNIC
    (re.compile(r"\b(PK\d{2}[A-Z]{4})\d{12}(\d{4})\b"), lambda m: f"{m.group(1)}************{m.group(2)}"),  # IBAN
    (re.compile(r"\b(03\d{2})\d{4}(\d{3})\b"), lambda m: f"{m.group(1)}****{m.group(2)}"),               # mobile
    (re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), lambda m: f"{m.group(1)}***@{m.group(2)}"),
]


class SecurityError(NL2SQLError):
    """A request or query broke a security rule. `rule` names which one, for the audit log."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


@dataclass
class SecurityPolicy:
    mask_personal_data: bool = True
    sensitive_columns: frozenset = DEFAULT_SENSITIVE
    max_question_chars: int = 500
    max_query_cost: float = 5_000_000  # PostgreSQL planner cost units
    allowed_tables: set | None = None  # None = every table in the database


@dataclass
class Inspection:
    """What the checks learned about an allowed query, used to mask its result."""
    masked_positions: dict = field(default_factory=dict)  # output column index -> sensitive column name
    star: bool = False


def mask_value(column: str, value):
    if value is None:
        return None
    s = str(value)
    if column == "cnic" and re.fullmatch(r"\d{5}-\d{7}-\d", s):
        return f"{s[:5]}-*******-{s[-1]}"
    if column == "email" and "@" in s:
        local, _, domain = s.partition("@")
        return f"{local[:1]}***@{domain}"
    if column in ("mobile", "phone", "raast_alias", "raast_id") and len(s) >= 7:
        return f"{s[:4]}****{s[-3:]}"
    if column == "iban" and len(s) >= 12:
        return f"{s[:8]}{'*' * (len(s) - 12)}{s[-4:]}"
    if column in ("account_number", "document_number") and len(s) > 4:
        return "*" * (len(s) - 4) + s[-4:]
    if column == "date_of_birth":
        return f"{s[:4]}-**-**"
    return "(hidden)"


def scrub(value):
    """Mask identifier-shaped text anywhere in a value (the last line of defence)."""
    if not isinstance(value, str):
        return value
    for pattern, repl in _PII_PATTERNS:
        value = pattern.sub(repl, value)
    return value


class SecurityLayer:
    def __init__(self, db: Database, policy: SecurityPolicy | None = None):
        self.db = db
        self.policy = policy or SecurityPolicy()
        self.dialect = DIALECTS.get(db.dialect, None)
        names = {t.name.lower() for t in db.tables()}
        self.allowed_tables = {t.lower() for t in self.policy.allowed_tables} if self.policy.allowed_tables else names

    # -- 1. the question ---------------------------------------------------------------------
    def check_question(self, question: str) -> None:
        if len(question) > self.policy.max_question_chars:
            raise SecurityError("question_too_long", f"Questions are limited to {self.policy.max_question_chars} characters.")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]", question):
            raise SecurityError("hidden_characters", "The question contains hidden control characters.")
        lowered = question.lower()
        for i, (pattern, message) in enumerate(_QUESTION_RULES):
            if re.search(pattern, lowered):
                rule = ["prompt_injection", "prompt_injection", "write_intent", "credentials", "sql_injection"][i]
                raise SecurityError(rule, message)

    # -- 2 and 3. the SQL ---------------------------------------------------------------------
    def check_sql(self, sql: str) -> Inspection:
        try:
            trees = [t for t in sqlglot.parse(sql, read=self.dialect) if t is not None]
        except sqlglot.errors.ParseError:
            raise SecurityError("unparseable_sql", "The query couldn't be verified as safe, so it wasn't run.") from None
        if len(trees) != 1:
            raise SecurityError("multiple_statements", "Only one query can run at a time.")
        root = trees[0]
        if not isinstance(root, (exp.Select, exp.SetOperation)):
            raise SecurityError("not_a_select", "Only read-only SELECT queries are allowed.")

        for node in root.walk():
            if isinstance(node, _FORBIDDEN):
                raise SecurityError("forbidden_statement",
                                    f"The query contains a {type(node).__name__.upper()} operation, which isn't allowed.")
        # Any WITH RECURSIVE, at the top or nested in a subquery, can loop without end.
        if any(w.args.get("recursive") for w in root.find_all(exp.With)):
            raise SecurityError("recursive_query", "Recursive queries aren't allowed.")

        cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
        for table in root.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise SecurityError("table_function", "Queries can only read the bank's tables, not generated rows.")
            name, schema = table.name.lower(), (table.db or "").lower()
            if schema in _BLOCKED_SCHEMAS or name.startswith(("pg_", "sqlite_")):
                raise SecurityError("system_table", "System tables are off limits.")
            if name not in self.allowed_tables and name not in cte_names:
                raise SecurityError("unknown_table", f"The table '{table.name}' isn't one this assistant may read.")

        for fn in root.find_all(exp.Func):
            if isinstance(fn, exp.Anonymous):
                fname = fn.name.lower()
                if fname not in _ALLOWED_UNKNOWN_FUNCTIONS:
                    raise SecurityError("blocked_function", f"The function {fn.name}() isn't allowed.")
            elif type(fn).__name__ in _BLOCKED_FUNCTION_TYPES:
                raise SecurityError("blocked_function", f"{fn.sql_name()} isn't allowed.")

        return self._inspect_personal_data(root)

    def _inspect_personal_data(self, root) -> Inspection:
        sensitive = self.policy.sensitive_columns
        if not self.policy.mask_personal_data:
            return Inspection()
        inspection = Inspection()
        for select in root.find_all(exp.Select):
            # The SELECTs whose columns become the output: the root, or each branch of a UNION.
            is_top = select is root or (isinstance(root, exp.SetOperation) and select.find_ancestor(exp.Select) is None)
            for i, proj in enumerate(select.expressions):
                if isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)):
                    inspection.star = True
                    continue
                cols = [c for c in proj.find_all(exp.Column) if c.name.lower() in sensitive]
                if not cols:
                    continue
                inner = proj.this if isinstance(proj, exp.Alias) else proj
                if all(c.find_ancestor(exp.Count) is not None for c in cols):
                    continue  # counting identifiers reveals nothing about them
                if not is_top:
                    raise SecurityError("personal_data", "Personal identifiers can't be passed through subqueries.")
                if isinstance(inner, exp.Column):
                    inspection.masked_positions[i] = inner.name.lower()
                else:
                    raise SecurityError("personal_data", f"Personal data ({cols[0].name}) can only be shown masked, not transformed.")
        return inspection

    # -- 4. cost --------------------------------------------------------------------------------
    def check_cost(self, sql: str) -> None:
        if self.dialect != "postgres":
            return
        plan = self.db.run(f"EXPLAIN (FORMAT JSON) {sql}").rows[0][0]
        if isinstance(plan, str):
            plan = json.loads(plan)
        cost = plan[0]["Plan"]["Total Cost"]
        if cost > self.policy.max_query_cost:
            raise SecurityError("too_expensive",
                                f"This query would be too heavy for the database (estimated cost {cost:,.0f}). Try narrowing it down.")

    # -- results --------------------------------------------------------------------------------
    def mask(self, result: QueryResult, inspection: Inspection) -> tuple[QueryResult, list[str]]:
        if not self.policy.mask_personal_data:
            return result, []
        positions = dict(inspection.masked_positions)
        for i, col in enumerate(result.columns):  # SELECT * and plain names
            if col.lower() in self.policy.sensitive_columns:
                positions.setdefault(i, col.lower())
        rows = [tuple(mask_value(positions[i], v) if i in positions else scrub(v) for i, v in enumerate(row))
                for row in result.rows]
        masked = sorted({result.columns[i] for i in positions if i < len(result.columns)})
        return QueryResult(result.columns, rows, result.truncated), masked


class AuditLog:
    """Append-only JSON Lines record of every question, query and decision.

    It stores what was asked, the SQL, who asked and what happened (answered, refused and why,
    columns masked), never the data the query returned."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, **entry):
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 **{k: v for k, v in entry.items() if v not in (None, [], "")}}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
