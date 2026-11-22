"""Start with: uv run uvicorn backend.main:app --reload"""

from fastapi import FastAPI

from backend.routers import availability, budget, catalog
from backend.store import TravelStore


def create_app() -> FastAPI:
    # A factory gives tests their own fresh store, just like a server restart.
    app = FastAPI(
        title="Itinerary Guide — Travel Demo",
        description="Fictional INR prices, in-memory availability, and a simple trip budget.",
        version="0.1.0",
    )
    app.state.store = TravelStore()
    app.include_router(catalog.router)
    app.include_router(availability.router)
    app.include_router(budget.router)

    @app.get("/health", tags=["Health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
