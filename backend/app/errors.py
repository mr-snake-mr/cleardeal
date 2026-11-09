"""One error shape for the whole API.

docs/08 §1 specifies `{code, message, request_id, details?}` at the top level.
FastAPI's defaults are not that shape: `HTTPException` produces `{"detail": ...}`
and a Pydantic failure produces `{"detail": [{"loc": ...}]}`. A contract that
only the happy path follows is not a contract, so both are translated here, once.

The translation lives in an exception handler rather than in every router
because the alternative — every call site building the envelope by hand — is a
guarantee that one of them will forget.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# HTTP status -> stable code, for the cases where a router raises a bare
# exception or a framework default fires.
_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def _envelope(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # A router may raise with a ready-made envelope in `detail`; honour it
        # rather than re-deriving a code from the status.
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return _envelope(
                exc.status_code,
                str(exc.detail.get("code")),
                str(exc.detail.get("message", "")),
                exc.detail.get("details"),
            )
        return _envelope(
            exc.status_code,
            _STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field-keyed, because a client needs to know which input was wrong and
        # a list of {loc: [...]} tuples is a parser problem dressed as an error.
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", ()) if part != "body"]
            key = ".".join(location) or "body"
            fields[key] = error.get("msg", "invalid")
        return _envelope(
            422,
            "VALIDATION_ERROR",
            "One or more fields are invalid.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # No exception text and no traceback in the body: a stack trace is an
        # information disclosure, and the details belong in the log.
        return _envelope(500, "INTERNAL_ERROR", "Internal server error.")
