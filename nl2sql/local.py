"""Answering without a paid API: a model running locally in Ollama, and rehearsed questions.

Both classes stand in for `anthropic.Anthropic()`: they expose `client.messages.parse(...)` and
return an object with `stop_reason`, `parsed_output` and `content`, so NL2SQL uses them unchanged,
including its read-only guard, self-repair retry and privacy mode. Each response also carries a
`source` label ("rehearsed" or "local") that the web UI shows next to the answer.

    client = RehearsedClient(load_rehearsed("questions.json"), fallback=OllamaClient())
    engine = NL2SQL(db, client=client)
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from .generator import NL2SQLError

DEFAULT_LOCAL_MODEL = "qwen2.5-coder:3b"
OLLAMA_URL = "http://127.0.0.1:11434"


class LocalAIError(NL2SQLError):
    """Shown to the user like any other answer error (e.g. Ollama isn't running)."""


def _text(content) -> str:
    """Message content as plain text (NL2SQL passes our own earlier replies back on a retry)."""
    if isinstance(content, str):
        return content
    return "\n".join(getattr(b, "text", None) or (b.get("text", "") if isinstance(b, dict) else str(b)) for b in content)


class OllamaClient:
    """Asks a model served by Ollama on this computer. Nothing leaves the machine."""

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, url: str = OLLAMA_URL, timeout: float = 300,
                 context_tokens: int = 8192):
        self.model, self.url, self.timeout, self.context_tokens = model, url.rstrip("/"), timeout, context_tokens
        self.messages = SimpleNamespace(parse=self._parse)

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise LocalAIError(f"Ollama returned {exc.code}: {exc.read().decode(errors='replace')[:200]}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise LocalAIError("The local AI isn't running. Start Ollama, then try again.") from exc

    def available(self) -> tuple[bool, str]:
        """(ready, message): whether Ollama is running and has the model downloaded."""
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3) as resp:
                names = {m["name"] for m in json.loads(resp.read()).get("models", [])}
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
            return False, "Ollama isn't running."
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"Ollama is running but the model isn't downloaded: run  ollama pull {self.model}"
        return True, "ready"

    def _chat(self, system: str, messages: list[dict], schema: dict | None) -> str:
        body = {"model": self.model, "stream": False, "keep_alive": "30m",
                "messages": [{"role": "system", "content": system}] + messages,
                "options": {"temperature": 0, "num_ctx": self.context_tokens}}
        if schema:
            body["format"] = schema
        return self._post("/api/chat", body)["message"]["content"]

    def warm_up(self, system) -> None:
        """Load the model and process the (fixed) system prompt once, so Ollama can reuse it and the
        first real question isn't slow."""
        self._chat(_text(system), [{"role": "user", "content": "Reply with the word ready."}], None)

    def _parse(self, *, system, messages, output_format, **_ignored):
        instruction = ("\n\nReply with one JSON object with the keys answerable (true or false), "
                       "sql (a single SELECT query, or an empty string) and explanation (one or two sentences).")
        msgs = [{"role": m["role"], "content": _text(m["content"])} for m in messages]
        raw = self._chat(_text(system) + instruction, msgs, output_format.model_json_schema())
        try:
            parsed = output_format.model_validate_json(raw)
        except ValueError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)  # small models sometimes wrap the JSON in text
            if not match:
                raise LocalAIError(f"The local AI didn't return an answer in the expected form: {raw[:120]}")
            parsed = output_format.model_validate_json(match.group(0))
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, content=raw,
                               source=f"local:{self.model}")


# ---------------------------------------------------------------------------------------------
# Rehearsed questions
# ---------------------------------------------------------------------------------------------
_WORDS = re.compile(r"[a-z0-9]+")
_FILLER = {"the", "a", "an", "of", "in", "on", "for", "to", "is", "are", "do", "does", "we", "our", "us",
           "me", "show", "list", "give", "tell", "what", "which", "who", "please", "and", "with", "by", "at"}


def _words(text: str) -> set[str]:
    out = set()
    for w in _WORDS.findall(text.lower()):
        if w in _FILLER:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def load_rehearsed(path: str | Path) -> list[dict]:
    """A JSON list of {"question", "sql" (null if it can't be answered), "explanation"}."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


class RehearsedClient:
    """Answers questions it has been prepared for instantly; hands anything else to `fallback`."""

    def __init__(self, questions: list[dict], fallback=None, threshold: float = 0.75):
        self.questions = [(q, _words(q["question"])) for q in questions]
        self.fallback, self.threshold = fallback, threshold
        self.messages = SimpleNamespace(parse=self._parse)

    def match(self, question: str):
        asked = _words(question)
        best, score = None, 0.0
        for q, words in self.questions:
            s = len(asked & words) / len(asked | words) if asked | words else 0
            if s > score:
                best, score = q, s
        return best if score >= self.threshold else None

    def _parse(self, *, messages, output_format, **kwargs):
        # Only the first attempt can be rehearsed; a repair after a failed query goes to the AI.
        hit = self.match(_text(messages[0]["content"])) if len(messages) == 1 else None
        if hit:
            answer = output_format(answerable=hit["sql"] is not None, sql=hit["sql"] or "",
                                   explanation=hit["explanation"])
            return SimpleNamespace(stop_reason="end_turn", parsed_output=answer,
                                   content=answer.model_dump_json(), source="rehearsed")
        if self.fallback is None:
            answer = output_format(answerable=False, sql="", explanation=(
                "This question isn't one of the rehearsed ones, and no AI model is connected. "
                "Start Ollama for the local AI, or set ANTHROPIC_API_KEY for Claude."))
            return SimpleNamespace(stop_reason="end_turn", parsed_output=answer,
                                   content=answer.model_dump_json(), source=None)  # nobody answered
        return self.fallback.messages.parse(messages=messages, output_format=output_format, **kwargs)
