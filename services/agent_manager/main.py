"""agent-manager HTTP 入口"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith
from packages.common.db import Base, get_engine

configure_langsmith()
log = logging.getLogger("agent-manager")


def create_app() -> FastAPI:
    @property
    def _(): pass  # noqa

    app = FastAPI(title="agent-manager", version="0.1.0")

    @app.on_event("startup")
    def _init_db():
        # 触发表创建（开发环境；生产应改 Alembic 迁移）
        try:
            engine = get_engine()
            from services.agent_manager import models  # noqa: F401  注册 ORM 模型
            Base.metadata.create_all(bind=engine)
            log.info("DB schema initialized")
        except Exception as e:
            log.warning(f"DB init skipped: {e}")

    @app.get("/health")
    async def health():
        return {"ok": True}

    from .routers import agents, debug, models
    app.include_router(agents.router)
    app.include_router(debug.router)
    app.include_router(models.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().agent_manager_port, reload=False)