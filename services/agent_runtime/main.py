"""agent-runtime HTTP 入口"""
from __future__ import annotations

from fastapi import FastAPI

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith

configure_langsmith()


def create_app() -> FastAPI:
    app = FastAPI(title="agent-runtime", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"ok": True}

    from .openai_router import router as openai_router
    app.include_router(openai_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().agent_runtime_port, reload=False)