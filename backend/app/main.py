from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse

from app.api.dependencies import get_settings
from app.api.v1 import router as api_v1_router
from app.core.errors import install_error_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CosmoHACK Orbit API",
        version="1.0.0",
        description=(
            "UTC/TEME orbit propagation for ISS (NORAD 25544) and objects screened by "
            "CelesTrak SOCRATES. Proximity thresholds are not collision probabilities."
        ),
    )
    app.add_middleware(GZipMiddleware, minimum_size=settings.gzip_minimum_size)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_v1_router, prefix="/api/v1")
    install_error_handlers(app)
    return app


app = create_app()
