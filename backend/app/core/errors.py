from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class OrbitServiceError(Exception):
    status_code = 500
    code = "ORBIT_SERVICE_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SourceUnavailableError(OrbitServiceError):
    status_code = 503
    code = "SOURCE_UNAVAILABLE"


class OrbitalElementsError(OrbitServiceError):
    status_code = 502
    code = "ORBITAL_ELEMENTS_ERROR"


class StaleElementsError(OrbitServiceError):
    status_code = 422
    code = "STALE_ORBITAL_ELEMENTS"


class PropagationError(OrbitServiceError):
    status_code = 422
    code = "PROPAGATION_ERROR"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OrbitServiceError)
    async def orbit_error_handler(_request: Request, exc: OrbitServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"errors": errors},
                }
            },
        )
