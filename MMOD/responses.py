"""HTTP helpers and typed errors. Payload shapes stay identical to the original app."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

try:
    import orjson  # noqa: F401
    from fastapi.responses import ORJSONResponse as _JSONResponse
    HAS_ORJSON = True
except ImportError:
    from fastapi.responses import JSONResponse as _JSONResponse
    HAS_ORJSON = False


class WindowError(ValueError):
    """Request time-window failed validation; message is returned as-is."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def json_response(content: dict[str, Any]) -> JSONResponse:
    if HAS_ORJSON:
        return _JSONResponse(content=content)
    return JSONResponse(content=content)


def api_error_response(exc: ApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message, exc.details)
