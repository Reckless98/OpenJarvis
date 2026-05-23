"""Cockpit-only FastAPI app for the Filip Jarvis browser surface."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openjarvis.server.api_routes import cockpit_router


def create_cockpit_app() -> FastAPI:
    """Create a minimal backend for cockpit routing without an inference engine."""
    app = FastAPI(
        title="OpenJarvis Filip Cockpit",
        description="OAuth/CLI-first cockpit backend for local Jarvis routing.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(cockpit_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "cockpit"}

    @app.get("/v1/models")
    async def list_models():
        return {"data": []}

    @app.get("/v1/info")
    async def server_info():
        return {"model": "", "agent": None, "engine": "cockpit"}

    @app.get("/v1/savings")
    async def savings():
        return {
            "total_calls": 0,
            "total_tokens": 0,
            "total_cost": 0,
            "total_energy_wh": 0,
            "per_provider": [],
        }

    @app.get("/v1/managed-agents")
    async def managed_agents():
        return {"agents": []}

    @app.get("/v1/approvals/pending")
    async def pending_approvals():
        return {"actions": []}

    return app


app = create_cockpit_app()


__all__ = ["app", "create_cockpit_app"]
