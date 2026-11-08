"""Error conventions (spec §8.2).

Every error response has exactly one shape::

    { "error": { "code": "no_data", "message": "No data has been imported yet." } }

Raise :class:`ApiError` (or a subclass) from routes and dependencies; never return
error dicts by hand. Validation errors (422), unknown routes (404) and other HTTP
errors are converted to the same shape by :func:`install_error_handlers`.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("instagraph")


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Body of every error response (use in ``responses=`` for OpenAPI docs)."""

    error: ErrorDetail


class ApiError(Exception):
    """An error that becomes ``{"error": {"code", "message"}}`` with HTTP ``status``.

    ``code`` is a stable snake_case identifier the frontend may switch on;
    ``message`` is a human-readable English sentence.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class NoDataError(ApiError):
    """Raised when an endpoint needs data but nothing has been imported (HTTP 409)."""

    def __init__(self, message: str = "No data has been imported yet.") -> None:
        super().__init__("no_data", message, 409)


class NotFoundError(ApiError):
    """A requested entity (chat, person, message, …) does not exist (HTTP 404)."""

    def __init__(self, message: str = "Not found.") -> None:
        super().__init__("not_found", message, 404)


class ValidationApiError(ApiError):
    """A semantically invalid parameter (HTTP 422), e.g. an unknown time zone."""

    def __init__(self, message: str) -> None:
        super().__init__("validation_error", message, 422)


#: Documented error responses to pass as ``responses=`` on routers.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "No data imported yet"},
    422: {"model": ErrorResponse, "description": "Invalid parameters"},
}

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    500: "internal_error",
}


def error_response(code: str, message: str, status: int) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status)


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.code, exc.message, exc.status)


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return error_response(code, message, exc.status_code)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("query", "body", "path"))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg")))
    return error_response("validation_error", "; ".join(parts) or "Invalid request.", 422)


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return error_response("internal_error", "Internal server error.", 500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
