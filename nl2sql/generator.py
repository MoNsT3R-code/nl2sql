"""Natural language -> SQL with Claude, with one self-repair retry on errors."""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .db import Database, QueryResult
from .guard import UnsafeSQLError, check_sql
from .schema import describe_schema, select_tables

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You translate questions into a single {dialect} SELECT query.

Rules:
- Use only tables and columns that exist in the schema below, and {dialect} syntax.
- Produce exactly one read-only query (SELECT or WITH ... SELECT). Never modify data.
- Prefer explicit column names over SELECT *, and give computed columns readable aliases.
- If the question cannot be answered from this schema, set `answerable` to false,
  leave `sql` empty, and explain why in `explanation`. If it needs a table whose columns
  are not shown, say which table in `explanation`.

Schema:
{schema}"""


class SQLAnswer(BaseModel):
    answerable: bool = Field(description="False if the schema cannot answer the question")
    sql: str = Field(description="A single SELECT query, or empty if unanswerable")
    explanation: str = Field(description="One or two sentences on how the query answers the question")


class NL2SQLError(RuntimeError):
    pass


@dataclass
class NL2SQLResult:
    question: str
    answer: SQLAnswer
    result: QueryResult | None
    attempts: int
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # tables whose definitions were in the prompt
    source: str = "claude"  # who wrote the SQL: "claude", "local:<model>" or "rehearsed"


class NL2SQL:
    def __init__(self, db: Database, client=None, model: str = DEFAULT_MODEL,
                 max_attempts: int = 2, full_schema_limit: int = 30, max_tables: int = 12,
                 sample_rows: int = 0):
        """Schemas with up to `full_schema_limit` tables go into the prompt whole (and cached).
        Larger ones get a per-question selection of at most `max_tables` tables.

        Privacy: by default the model receives only the question and the database structure
        (table and column definitions), never rows from the database, and it never sees query
        results. `sample_rows` > 0 opts in to sending that many example rows per table, which
        can help accuracy on data that isn't sensitive."""
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.db = db
        self.client = client
        self.model = model
        self.max_attempts = max_attempts
        self.max_tables = max_tables
        self.sample_rows = sample_rows
        self.tables = db.tables()
        self.large_schema = len(self.tables) > full_schema_limit
        self._full_system = None if self.large_schema else self._system(self.tables)

    def _system(self, tables) -> str:
        names = {t.name for t in tables}
        others = [t.name for t in self.tables if t.name not in names]
        schema = describe_schema(self.db, tables, sample_rows=self.sample_rows, other_tables=others)
        return SYSTEM_PROMPT.format(dialect=self.db.dialect, schema=schema)

    def _generate(self, system: str, messages: list) -> tuple[SQLAnswer, object]:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            # The schema prompt repeats across questions (and retries), so cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_format=SQLAnswer,
            # Route safety-classifier refusals to a fallback model server-side.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            raise NL2SQLError("the model declined to answer this question")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise NL2SQLError(f"no structured answer (stop_reason={response.stop_reason})")
        return response.parsed_output, response

    def ask(self, question: str) -> NL2SQLResult:
        if self.large_schema:
            tables = select_tables(self.tables, question, self.max_tables)
            system = self._system(tables)
        else:
            tables, system = self.tables, self._full_system
        table_names = [t.name for t in tables]

        messages = [{"role": "user", "content": question}]
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            answer, response = self._generate(system, messages)
            source = getattr(response, "source", "claude")
            if not answer.answerable:
                return NL2SQLResult(question, answer, None, attempt, errors, table_names, source)

            try:
                sql = check_sql(answer.sql)
                answer.sql = sql
                result = self.db.run(sql)
                return NL2SQLResult(question, answer, result, attempt, errors, table_names, source)
            except (UnsafeSQLError, *self.db.errors) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                # Feed the error back so the model can repair its query.
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": f"That query failed with: {exc}\nReturn a corrected query.",
                })

        raise NL2SQLError(f"gave up after {self.max_attempts} attempts: {errors[-1]}")
