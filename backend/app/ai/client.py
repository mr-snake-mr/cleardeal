"""Gemini client.

Boundary note: this module may not import `app.models` (tests/test_boundaries.py).
The call log is written through an injected `record` callable, and the writer
lives in `app/services/ai_runs.py`. The AI layer stays pure: text in, validated
object out, no database handle.

Three decisions, each from a rule rather than taste:

- **One retry, transient only.** `cost-aware-llm-pipeline`: "Never retry on
  authentication or validation errors." A 400 from a bad schema will be a 400
  again, and retrying it burns the demo's time budget.
- **Output is untrusted.** `security-review`: model output is input. It is
  parsed and validated against a Pydantic model before any caller sees it.
- **The model name is a setting, not a literal**, so a swap appears in `ai_runs`
  rather than silently in a diff.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.schema_tools import generation_schema
from app.config import settings

# Verified 2026-11-09 (docs/05 §7). Used only to report an estimate; nothing
# bills against it.
INPUT_USD_PER_1M = 0.75
OUTPUT_USD_PER_1M = 3.75

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

LOGGER = logging.getLogger(__name__)


class AiUnavailable(RuntimeError):
    """Transport failure, 5xx after one retry, or no API key configured."""


class AiInvalidRequest(RuntimeError):
    """A 4xx: bad key, bad schema, bad request. Not retryable, not an outage."""


class AiInvalidOutput(RuntimeError):
    """Unparseable JSON, or JSON that is not the shape the caller asked for."""


@dataclass(frozen=True, slots=True)
class AiCall:
    """One attempt, as recorded. Frozen: a log entry that can be edited is not a log."""

    module: str
    model: str
    prompt_hash: str
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self.input_tokens / 1_000_000 * INPUT_USD_PER_1M
            + self.output_tokens / 1_000_000 * OUTPUT_USD_PER_1M
        )


class Recorder(Protocol):
    """Injected by the service layer, which is allowed to import models."""

    def __call__(self, call: AiCall, project_id: uuid.UUID | None) -> None: ...


class Transport(Protocol):
    def __call__(self, url: str, headers: dict, payload: dict) -> dict: ...


def prompt_hash(prompt: str, schema: dict) -> str:
    """Stable across calls, changes when either half changes.

    Hashing the schema as well as the prompt is deliberate: changing a field name
    changes the behaviour, and a log that cannot see that is a log that lies.
    """
    canonical = json.dumps({"prompt": prompt, "schema": schema}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _default_transport(url: str, headers: dict, payload: dict) -> dict:
    response = httpx.post(
        url, headers=headers, json=payload, timeout=settings.ai_timeout_seconds
    )
    response.raise_for_status()
    return response.json()


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        transport: Transport | None = None,
        record: Recorder | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.gemini_api_key
        self._model = model or settings.gemini_model
        self._transport = transport or _default_transport
        self._record = record

    @property
    def model(self) -> str:
        return self._model

    def generate(
        self,
        *,
        module: str,
        prompt: str,
        schema: type[BaseModel],
        project_id: uuid.UUID | None = None,
        validator: Callable[[BaseModel], None] | None = None,
    ) -> BaseModel:
        if not self._api_key:
            # Fail before spending anything, and record it: a missing key is an
            # operator error, and it should be visible in the log rather than
            # inferred from a wall of 503s.
            self._emit(
                AiCall(
                    module=module,
                    model=self._model,
                    prompt_hash="",
                    status="not_configured",
                ),
                project_id,
            )
            raise AiUnavailable("GEMINI_API_KEY is not configured")

        # Not `model_json_schema()`: Gemini rejects deep unions and
        # wants the documented null idiom (app/ai/schema_tools.py).
        schema_json = generation_schema(schema)
        digest = prompt_hash(prompt, schema_json)
        payload = self._payload(prompt, schema_json)
        # The key travels in a header, not the query string. In the URL it
        # lands in httpx's INFO log and inside HTTPStatusError.__str__, which
        # means the first failed call prints the credential.
        url = f"{BASE_URL}/{self._model}:generateContent"

        started = time.monotonic()
        body = self._call_with_one_retry(url, payload, module, digest, project_id)
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = body.get("usageMetadata") or {}
        tokens = {
            "input_tokens": int(usage.get("promptTokenCount") or 0),
            "output_tokens": int(usage.get("candidatesTokenCount") or 0),
        }

        try:
            text = self._extract_text(body)
        except AiInvalidOutput:
            # A response with no candidate text is still an attempt that cost
            # money. Recording nothing leaves a hole exactly where the
            # failures are.
            self._emit(
                AiCall(
                    module=module,
                    model=self._model,
                    prompt_hash=digest,
                    status="parse_error",
                    latency_ms=latency_ms,
                    **tokens,
                ),
                project_id,
            )
            raise

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._emit(
                AiCall(
                    module=module,
                    model=self._model,
                    prompt_hash=digest,
                    status="parse_error",
                    input_tokens=int(usage.get("promptTokenCount") or 0),
                    output_tokens=int(usage.get("candidatesTokenCount") or 0),
                    latency_ms=latency_ms,
                ),
                project_id,
            )
            raise AiInvalidOutput("model returned unparseable JSON") from exc

        try:
            result = schema.model_validate(parsed)
        except ValidationError as exc:
            self._emit(
                AiCall(
                    module=module,
                    model=self._model,
                    prompt_hash=digest,
                    status="schema_error",
                    input_tokens=int(usage.get("promptTokenCount") or 0),
                    output_tokens=int(usage.get("candidatesTokenCount") or 0),
                    latency_ms=latency_ms,
                ),
                project_id,
            )
            raise AiInvalidOutput("model output failed schema validation") from exc

        # A semantic check the caller supplies — "the quote is in the brief",
        # "the milestone sum equals the budget". It runs BEFORE the ok row is
        # written, so a rejected result is never logged as a clean run.
        if validator is not None:
            try:
                validator(result)
            except Exception as exc:  # noqa: BLE001 - any rejection must be logged
                # Deliberately broad. The contract is "a rejected result is never
                # logged as a clean run", and a validator written against a
                # duck-typed object raises `AttributeError`, not `ValueError`.
                # Narrowing this to `ValueError` left that path invisible in
                # `ai_runs` — a paid call with no row.
                self._emit(
                    AiCall(
                        module=module,
                        model=self._model,
                        prompt_hash=digest,
                        status="semantic_error",
                        latency_ms=latency_ms,
                        **tokens,
                    ),
                    project_id,
                )
                raise AiInvalidOutput(str(exc)) from exc

        self._emit(
            AiCall(
                module=module,
                model=self._model,
                prompt_hash=digest,
                status="ok",
                input_tokens=int(usage.get("promptTokenCount") or 0),
                output_tokens=int(usage.get("candidatesTokenCount") or 0),
                latency_ms=latency_ms,
            ),
            project_id,
        )
        return result

    # ---------------------------------------------------------------- internals

    def _payload(self, prompt: str, schema_json: dict) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # docs/05 §3.2: only minItems/maxItems survive; every other
            # constraint is silently stripped, which is why Pydantic re-validates.
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema_json,
                "temperature": 0.2,
            },
        }

    def _call_with_one_retry(
        self, url: str, payload: dict, module: str, digest: str, project_id: uuid.UUID | None
    ) -> dict:
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._transport(url, headers, payload)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                last = exc
                if 400 <= status_code < 500:
                    # The provider says *why* in the body, and it is the only
                    # thing that separates a rejected schema from a rejected
                    # key. Log it; do not put it in the message, where it would
                    # travel to the browser.
                    LOGGER.warning(
                        "provider rejected %s (%s): %s",
                        module,
                        status_code,
                        exc.response.text[:600],
                    )
                    # Not retryable: a bad request stays bad, and a bad key is
                    # not fixed by asking twice.
                    self._emit(
                        AiCall(
                            module=module,
                            model=self._model,
                            prompt_hash=digest,
                            # The code, not just the category. "client_error"
                            # cannot separate a rejected key (403) from a rate
                            # limit (429), which are opposite problems.
                            status=f"client_error:{status_code}",
                        ),
                        project_id,
                    )
                    # `from None`: the chained exception carries the URL, and
                    # the URL is not something an error body should ever reach.
                    raise AiInvalidRequest(
                        f"provider rejected the request ({status_code})"
                    ) from None
            except (httpx.RequestError, TimeoutError, json.JSONDecodeError) as exc:
                # A 200 whose body is not JSON is what a captive portal or a
                # proxy error page looks like. `response.json()` raises
                # `JSONDecodeError`, which is not an `httpx` error, so without
                # this the retry loop missed it and the caller saw a 500.
                last = exc
            if attempt == 1:
                continue
        self._emit(
            AiCall(module=module, model=self._model, prompt_hash=digest, status="transport_error"),
            project_id,
        )
        raise AiUnavailable("provider unavailable after one retry") from last

    @staticmethod
    def _extract_text(body: dict) -> str:
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiInvalidOutput("response contained no candidate text") from exc

    def _emit(self, call: AiCall, project_id: uuid.UUID | None) -> None:
        if self._record is not None:
            self._record(call, project_id)


def get_client(record: Recorder | None = None, transport: Transport | None = None) -> GeminiClient:
    """One place that builds a client, so tests have one seam to replace."""
    return GeminiClient(record=record, transport=transport)


