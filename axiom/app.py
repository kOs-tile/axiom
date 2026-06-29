"""
AXIOM FastAPI Application

Entry point for the AXIOM skill marketplace service.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from axiom.api.routes import router
from axiom.api.websocket import ws_router
from axiom.config import settings
from axiom.monitor.decay_monitor import decay_monitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    logger.info("AXIOM starting up…")

    # Start decay monitor
    decay_monitor.start()
    logger.info("Skill decay monitor started")

    yield

    # Graceful shutdown
    decay_monitor.stop()
    logger.info("AXIOM shut down cleanly")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AXIOM — Living Skill Marketplace",
        description=(
            "Autonomous skill synthesizer and marketplace for the Hermes/Kavi Claw agent framework. "
            "Resolves, composes, and synthesizes skills on demand."
        ),
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(router, prefix="/api/v1")
    app.include_router(ws_router)  # WebSocket routes at root (no prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "AXIOM",
            "version": settings.app_version,
            "docs": "/docs",
            "ws_synthesize": "/ws/synthesize",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "axiom.app:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
