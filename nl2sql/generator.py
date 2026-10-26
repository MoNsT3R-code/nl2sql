"""Natural language -> SQL with Claude, with one self-repair retry on errors."""

import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .db import QueryResult, run_query
from .guard import UnsafeSQLError, check_sql
from .schema import describe_schema

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You translate questions into a single SQLite SELECT query.

Rules:
- Use only tables and columns that exist in the schema below.
- Produce exactly one read-only query (SELECT or WITH ... SELECT). Never modify data.
- Prefer explicit column names over SELECT *, and give computed columns readable aliases.
- If the question cannot be answered from this schema, set `answerable` to false,
  leave `sql` empty, and explain why in `explanation`.

Schema:
{schema}"""


class SQLAnswer(BaseModel):
    answerable: bool = Field(description="False if the schema cannot answer the question")
    sql: str = Field(description="A single SQLite SELECT query, or empty if unanswerable")
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


class NL2SQL:
    def __init__(self, conn: sqlite3.Connection, client=None, model: str = DEFAULT_MODEL,
                 max_attempts: int = 2):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.conn = conn
        self.client = client
        self.model = model
        self.max_attempts = max_attempts
        self.system = SYSTEM_PROMPT.format(schema=describe_schema(conn))

    def _generate(self, messages: list) -> tuple[SQLAnswer, object]:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            # The schema prompt is identical across questions, so cache it.
            system=[{"type": "text", "text": self.system,
                     "cache_control": {"type": "ephemeral"}}],
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
        messages = [{"role": "user", "content": question}]
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            answer, response = self._generate(messages)
            if not answer.answerable:
                return NL2SQLResult(question, answer, None, attempt, errors)

            try:
                sql = check_sql(answer.sql)
                answer.sql = sql
                return NL2SQLResult(question, answer, run_query(self.conn, sql), attempt, errors)
            except (UnsafeSQLError, sqlite3.Error) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                # Feed the error back so the model can repair its query.
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": f"That query failed with: {exc}\nReturn a corrected query.",
                })

        raise NL2SQLError(f"gave up after {self.max_attempts} attempts: {errors[-1]}")
