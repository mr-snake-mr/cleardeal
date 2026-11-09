"""M1 brief analysis, end to end through the API.

Written before the implementation, per the TDD skill: the first run is expected
to fail on a missing module, which is the point — it proves the test is wired to
something that does not exist yet rather than to code that already passes.

The transport is faked (docs/12 §1), so what is tested is the contract: what the
endpoint accepts, what it refuses, and what it leaves behind when the provider
misbehaves.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.ai.client import GeminiClient
from app.database import SessionLocal
from app.models import AiRun, AuditEvent, Project, User

PASSWORD = "correct horse battery staple"

GOOD_BRIEF = (
    "Build a five page marketing site with a blog, a contact form, and a "
    "newsletter signup. Copy is provided by the client."
)

ANALYSIS = {
    "requirements": [
        {
            "text": "Five page marketing site",
            "source": "explicit",
            "quote": "five page marketing site",
        },
        {
            "text": "Blog with an index and post pages",
            "source": "inferred",
            "quote": None,
        },
    ],
    "ambiguities": [
        {"text": "Who supplies hosting?", "why_it_matters": "It changes the handover step."}
    ],
    "risks": [{"text": "Copy delivery could block the build", "severity": "medium"}],
    "assumptions": [{"text": "The client has a domain already"}],
}


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
        session.execute(delete(AuditEvent))
        session.execute(delete(AiRun))
        session.execute(delete(Project))
        session.execute(delete(User))
        session.commit()
    finally:
        session.close()
    yield


def _ok(payload: dict) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
        "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 300},
    }


class FakeTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url, headers, payload):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_ai(monkeypatch):
    """One seam: the router builds its client through `app.ai.client.get_client`."""
    from app.ai import client as client_module

    state = {"transport": FakeTransport(_ok(ANALYSIS))}

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return GeminiClient(transport=state["transport"], **kwargs)

    monkeypatch.setattr(client_module, "get_client", factory)
    monkeypatch.setattr("app.routers.projects.get_ai_client", factory, raising=False)
    return state


def _signup(client, email="freelancer@example.test"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response


def _project(client, **overrides):
    body = {
        "name": "Marketing site",
        "client_email": "client@example.test",
        "brief": GOOD_BRIEF,
        "budget_amount": "300.00",
    } | overrides
    response = client.post("/api/v1/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ------------------------------------------------------------------- success


def test_analysis_returns_requirements_with_their_source(client, fake_ai, db):
    _signup(client)
    project_id = _project(client)

    r = client.post(f"/api/v1/projects/{project_id}/analyse")

    assert r.status_code == 200, r.text
    body = r.json()
    assert [item["source"] for item in body["requirements"]] == ["explicit", "inferred"]
    assert body["ambiguities"][0]["why_it_matters"]


def test_a_successful_analysis_moves_the_project_to_analysed(client, fake_ai, db):
    _signup(client)
    project_id = _project(client)

    client.post(f"/api/v1/projects/{project_id}/analyse")

    row = db.get(Project, uuid.UUID(project_id))
    assert row.status == "ANALYSED"


def test_a_successful_analysis_writes_an_audit_event(client, fake_ai, db):
    _signup(client)
    project_id = _project(client)

    client.post(f"/api/v1/projects/{project_id}/analyse")

    events = db.execute(
        select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id))
    ).scalars().all()
    # Exactly one ANALYSIS_COMPLETED, not "contains": a duplicate written by a
    # retried request would pass a set-membership assertion. Creation events
    # are a different concern and are filtered out rather than asserted around.
    completed = [event for event in events if event.event_type == "ANALYSIS_COMPLETED"]
    assert len(completed) == 1


def test_the_run_is_recorded_under_the_module_name(client, fake_ai, db):
    _signup(client)
    project_id = _project(client)

    client.post(f"/api/v1/projects/{project_id}/analyse")

    run = db.execute(select(AiRun)).scalars().one()
    assert run.module == "brief_analysis"
    assert run.validation_status == "ok"
    assert run.input_tokens == 900


# ------------------------------------------------------------------ failures


def test_an_explicit_requirement_without_a_quote_is_rejected(client, fake_ai, db):
    """The product's one rule: an explicit claim must be traceable to the brief."""
    unquoted = json.loads(json.dumps(ANALYSIS))
    unquoted["requirements"][0]["quote"] = None
    # One outcome: a validation failure is not retried, so a second outcome
    # would be dead code that hides a future change in the retry policy.
    fake_ai["transport"] = FakeTransport(_ok(unquoted))

    _signup(client)
    project_id = _project(client)

    r = client.post(f"/api/v1/projects/{project_id}/analyse")

    assert r.status_code == 502
    assert r.json()["code"] == "AI_INVALID_OUTPUT"
    assert fake_ai["transport"].calls == 1
    assert db.get(Project, uuid.UUID(project_id)).status == "DRAFT"


def test_an_explicit_requirement_quoting_text_not_in_the_brief_is_rejected(client, fake_ai, db):
    """The central rule, tested where it can actually fail.

    The schema can only see that a quote is non-empty. A plausible sentence the
    client never wrote passes it. Only the service holds the brief, so only a
    service-level check turns a fabricated citation into a 502.
    """
    fabricated = json.loads(json.dumps(ANALYSIS))
    fabricated["requirements"][0]["quote"] = "the client approved a second phase"
    fake_ai["transport"] = FakeTransport(_ok(fabricated))

    _signup(client)
    project_id = _project(client)

    r = client.post(f"/api/v1/projects/{project_id}/analyse")

    assert r.status_code == 502
    assert r.json()["code"] == "AI_INVALID_OUTPUT"
    assert db.get(Project, uuid.UUID(project_id)).status == "DRAFT"
    assert db.execute(select(AiRun)).scalars().one().validation_status == "semantic_error"


def test_a_quote_with_different_whitespace_still_matches(client, fake_ai):
    """The inverse failure: over-strict matching rejects a real quotation.

    Without this, `assert_quotes_come_from_the_brief` could be implemented as
    "always raise" and every other test here would pass.
    """
    spaced = json.loads(json.dumps(ANALYSIS))
    spaced["requirements"][0]["quote"] = "five  page\nmarketing site"
    fake_ai["transport"] = FakeTransport(_ok(spaced))

    _signup(client)
    project_id = _project(client)

    assert client.post(f"/api/v1/projects/{project_id}/analyse").status_code == 200


def test_a_provider_outage_leaves_the_project_untouched(client, fake_ai, db):
    import httpx

    request = httpx.Request("POST", "https://example.test")
    boom = httpx.HTTPStatusError(
        "down", request=request, response=httpx.Response(503, request=request)
    )
    fake_ai["transport"] = FakeTransport(boom, boom)

    _signup(client)
    project_id = _project(client)

    r = client.post(f"/api/v1/projects/{project_id}/analyse")

    assert r.status_code == 503
    assert r.json()["code"] == "AI_UNAVAILABLE"
    assert db.get(Project, uuid.UUID(project_id)).status == "DRAFT"
    assert db.execute(select(AiRun)).scalars().one().validation_status == "transport_error"
    # No ANALYSIS_COMPLETED on a failure. Project creation legitimately wrote
    # one event, so the assertion is about this endpoint, not the whole table.
    kinds = [e.event_type for e in db.execute(select(AuditEvent)).scalars().all()]
    assert "ANALYSIS_COMPLETED" not in kinds


def test_no_api_key_returns_503_and_does_not_mutate_the_project(client, monkeypatch, db):
    _signup(client)
    project_id = _project(client)
    monkeypatch.setattr("app.config.settings.gemini_api_key", "")

    r = client.post(f"/api/v1/projects/{project_id}/analyse")

    assert r.status_code == 503
    assert db.get(Project, uuid.UUID(project_id)).status == "DRAFT"


def test_another_users_project_is_404(client, fake_ai):
    _signup(client, "a@example.test")
    project_id = _project(client)

    client.cookies.clear()
    _signup(client, "b@example.test")

    assert client.post(f"/api/v1/projects/{project_id}/analyse").status_code == 404


def test_analysis_requires_a_session(client, fake_ai):
    _signup(client)
    project_id = _project(client)
    client.cookies.clear()

    assert client.post(f"/api/v1/projects/{project_id}/analyse").status_code == 401


# -------------------------------------------------------------- prompt shape


def test_the_prompt_carries_the_brief_budget_and_deadline(client, fake_ai, monkeypatch):
    """Catches a prompt rendered without the project's own inputs."""
    captured = {}

    from app.ai import client as client_module

    real_generate = client_module.GeminiClient.generate

    def spy(self, *, module, prompt, schema, project_id=None, validator=None):
        captured["prompt"] = prompt
        return real_generate(
            self,
            module=module,
            prompt=prompt,
            schema=schema,
            project_id=project_id,
            validator=validator,
        )

    monkeypatch.setattr(client_module.GeminiClient, "generate", spy)

    _signup(client)
    project_id = _project(client, deadline="2026-12-01")

    client.post(f"/api/v1/projects/{project_id}/analyse")

    prompt = captured["prompt"]
    assert "five page marketing site" in prompt
    assert "300.00" in prompt
    assert "2026-12-01" in prompt
    # Was `"explicit" in prompt.lower()`, which the template satisfies on its
    # own: deleting every rule from the prompt left it green. These two bite.
    assert "verbatim" in prompt
    assert "{{" not in prompt, "a placeholder reached the model unrendered"
