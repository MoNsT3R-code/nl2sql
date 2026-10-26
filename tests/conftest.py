from types import SimpleNamespace

import pytest

from nl2sql.db import SQLAlchemyDatabase, SQLiteDatabase
from nl2sql.sample_db import build


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued SQLAnswers from messages.parse."""

    def __init__(self, *answers, stop_reason="end_turn"):
        self.answers = list(answers)
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)
        self.stop_reason = stop_reason

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if callable(answer):
            answer = answer(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=answer,
                               content=[{"type": "text", "text": answer.model_dump_json()}])


@pytest.fixture
def db_path(tmp_path):
    return build(tmp_path / "store.db")


@pytest.fixture(params=["sqlite", "sqlalchemy"])
def db(request, db_path):
    """The demo database through both backends (SQLAlchemy is exercised via a sqlite:// URL)."""
    if request.param == "sqlite":
        database = SQLiteDatabase(db_path)
    else:
        pytest.importorskip("sqlalchemy")
        database = SQLAlchemyDatabase(f"sqlite:///{db_path.as_posix()}")
    yield database
    database.close()
