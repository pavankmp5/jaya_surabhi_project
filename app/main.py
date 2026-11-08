"""HTTP application entry point."""

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path

from app.routers.auth import admin_router, router as auth_router
from app.routers.products import router as product_router
from app.routers.product_imports import router as product_import_router
from app.routers.sales import router as sales_router
from app.routers.financials import router as financials_router

app = FastAPI(
    title="Jaya Surabhi Super Market POS",
    version="0.1.0",
    description="Local-first point of sale service.",
)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(product_router)
app.include_router(product_import_router)
app.include_router(sales_router)
app.include_router(financials_router)


@app.get("/operations", include_in_schema=False)
def operations_ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "operations.html")


@app.get("/", tags=["operations"])
def root() -> dict[str, str]:
    """Provide a response at the service base URL."""
    return {"status": "ok", "health": "/health", "documentation": "/docs"}


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    """Report that the application process is serving requests."""
    return {"status": "ok"}
