"""Run: uv run uvicorn backend.main:app --reload --port 8001"""

from fastapi import FastAPI

from backend.routers import deployment, documentation, qa, releases, security
from backend.store import ReleaseStore


def create_app() -> FastAPI:
    app = FastAPI(
        title="Shopflow release-readiness demo",
        description="Fictional QA, bug, security, documentation, and deployment-policy evidence. No agents or deployments.",
        version="0.1.0",
    )
    app.state.store = ReleaseStore()
    for router in (releases.router, qa.router, security.router, documentation.router, deployment.router):
        app.include_router(router)

    @app.get("/health", tags=["Health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
