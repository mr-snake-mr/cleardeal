"""The AI client, with no network.

Every test drives a fake transport. What is being tested is the envelope around
the provider — retry policy, validation, and the record — not Gemini's ability to
return JSON.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.ai.client import AiInvalidOutput, AiUnavailable, GeminiClient, prompt_hash
from app.database import SessionLocal
from app.models import AiRun
from app.services.ai_runs import make_recorder

SCHEMA_JSON = {
    "properties": {"answer": {"title": "Answer", "type": "string"}},
    "required": ["answer"],
    "title": "Answer",
    "type": "object",
}


class Answer(BaseModel):
    answer: str


class RequiredField(BaseModel):
    answer: str
    confidence: str


def _ok(text: str = '{"answer": "yes"}', *, prompt_tokens: int = 100, out_tokens: int = 20) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": out_tokens,
        },
    }


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class FakeTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url, headers, payload):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else _ok()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean():
    session = SessionLocal()
    try:
        session.execute(delete(AiRun))
        session.commit()
    finally:
        session.close()
    yield


def _client(db, *outcomes, api_key="test-key"):
    return GeminiClient(
        api_key=api_key,
        model="test-model",
        transport=FakeTransport(*outcomes),
        record=make_recorder(db),
    )


# ------------------------------------------------------------------- success


def test_a_valid_response_is_returned_as_a_validated_model(db):
    result = _client(db).generate(module="m1", prompt="p", schema=Answer)
    assert isinstance(result, Answer)
    assert result.answer == "yes"


def test_a_successful_call_is_recorded_with_tokens_and_latency(db):
    _client(db).generate(module="m1", prompt="p", schema=Answer)

    row = db.execute(select(AiRun)).scalar_one()

    assert row.validation_status == "ok"
    assert row.module == "m1"
    assert row.model == "test-model"
    assert row.input_tokens == 100
    assert row.output_tokens == 20
    assert row.latency_ms >= 0


def test_the_prompt_hash_is_stable_and_sensitive(db):
    a = prompt_hash("same", SCHEMA_JSON)
    b = prompt_hash("same", SCHEMA_JSON)
    c = prompt_hash("different", SCHEMA_JSON)

    assert a == b
    assert a != c


def test_changing_only_the_schema_changes_the_hash():
    """A prompt hash that ignores the schema cannot explain a behaviour change."""
    other = json.loads(json.dumps(SCHEMA_JSON))
    other["required"] = ["answer", "confidence"]
    assert prompt_hash("same", SCHEMA_JSON) != prompt_hash("same", other)


# -------------------------------------------------------------------- retry


def test_a_transient_failure_is_retried_once(db):
    transport = FakeTransport(_status(503), _ok())
    client = GeminiClient(
        api_key="k", model="m", transport=transport, record=make_recorder(db)
    )

    client.generate(module="m1", prompt="p", schema=Answer)

    assert transport.calls == 2


def test_two_transient_failures_raise_and_do_not_loop(db):
    transport = FakeTransport(_status(500), _status(500))
    client = GeminiClient(
        api_key="k", model="m", transport=transport, record=make_recorder(db)
    )

    with pytest.raises(AiUnavailable):
        client.generate(module="m1", prompt="p", schema=Answer)

    assert transport.calls == 2
    assert db.execute(select(AiRun)).scalar_one().validation_status == "transport_error"


def test_a_client_error_is_not_retried(db):
    """A 400 from a bad schema will be a 400 again; retrying burns the demo's time."""
    transport = FakeTransport(_status(400), _ok())
    client = GeminiClient(
        api_key="k", model="m", transport=transport, record=make_recorder(db)
    )

    with pytest.raises(AiUnavailable):
        client.generate(module="m1", prompt="p", schema=Answer)

    assert transport.calls == 1
    assert db.execute(select(AiRun)).scalar_one().validation_status == "client_error"


def test_a_connection_error_is_retried_then_raises(db):
    transport = FakeTransport(httpx.ConnectError("nope"), httpx.ConnectError("nope"))
    client = GeminiClient(
        api_key="k", model="m", transport=transport, record=make_recorder(db)
    )

    with pytest.raises(AiUnavailable):
        client.generate(module="m1", prompt="p", schema=Answer)
    assert transport.calls == 2


# --------------------------------------------------------------- validation


def test_unparseable_output_raises_and_is_recorded(db):
    with pytest.raises(AiInvalidOutput):
        _client(db, _ok("not json at all")).generate(module="m1", prompt="p", schema=Answer)

    assert db.execute(select(AiRun)).scalar_one().validation_status == "parse_error"


def test_output_missing_a_field_raises_and_is_recorded(db):
    with pytest.raises(AiInvalidOutput):
        _client(db, _ok('{"answer": "yes"}')).generate(
            module="m3", prompt="p", schema=RequiredField
        )

    assert db.execute(select(AiRun)).scalar_one().validation_status == "schema_error"


def test_a_response_with_no_candidate_text_raises(db):
    with pytest.raises(AiInvalidOutput):
        _client(db, {"candidates": []}).generate(module="m1", prompt="p", schema=Answer)


def test_a_missing_api_key_fails_before_calling_the_provider(db):
    transport = FakeTransport(_ok())
    client = GeminiClient(
        api_key="", model="m", transport=transport, record=make_recorder(db)
    )

    with pytest.raises(AiUnavailable):
        client.generate(module="m1", prompt="p", schema=Answer)

    assert transport.calls == 0
    assert db.execute(select(AiRun)).scalar_one().validation_status == "not_configured"


# --------------------------------------------------------------------- cost


def test_the_estimated_cost_uses_the_documented_prices():
    from app.ai.client import AiCall

    call = AiCall(
        module="m",
        model="x",
        prompt_hash="h",
        status="ok",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert round(call.estimated_cost_usd, 4) == round(0.75 + 3.75, 4)
