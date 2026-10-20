"""模型目录管理: 别名注册 / 路由配置 / 用量查询"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from packages.common.config import get_settings
from packages.common.db import session_scope
from packages.common.schemas import ModelAlias

from ..models import ModelAlias as ModelAliasRow


router = APIRouter(prefix="/api/v1")


def require_admin(authorization: Optional[str] = Header(None)):
    s = get_settings()
    if not authorization or authorization != f"Bearer {s.manager_admin_token}":
        raise HTTPException(401, "invalid admin token")


@router.get("/models")
def list_models(_: None = Depends(require_admin)):
    settings = get_settings()

    with session_scope() as s:
        rows = s.execute(select(ModelAliasRow)).scalars().all()
        out = [
            {"alias": r.alias, "primary": r.primary, "fallbacks": r.fallbacks or [], "params": r.params or {}}
            for r in rows
        ]

    # DB 里通常没有别名（model-svc 有代码级兜底），
    # 这里补上默认别名，避免管理台「模型目录」显示为空。
    existing = {m["alias"] for m in out}
    if settings.model_default_alias not in existing:
        out.insert(
            0,
            {
                "alias": settings.model_default_alias,
                "primary": {
                    "provider": "custom",
                    "model": settings.zhipu_default_model,
                    "base_url": settings.openai_api_base,
                    "api_version": "v4",
                },
                "fallbacks": [],
                "params": {},
                "source": "default",  # 代码兜底，非 DB 显式配置
            },
        )
    return out


@router.post("/models")
def create_model(alias: ModelAlias, _: None = Depends(require_admin)):
    with session_scope() as s:
        existing = s.get(ModelAliasRow, alias.alias)
        if existing:
            existing.primary = alias.primary.model_dump()
            existing.fallbacks = [f.model_dump() for f in alias.fallbacks]
            existing.params = alias.params
        else:
            row = ModelAliasRow(
                alias=alias.alias,
                primary=alias.primary.model_dump(),
                fallbacks=[f.model_dump() for f in alias.fallbacks],
                params=alias.params,
            )
            s.add(row)

    # 同步到 model-svc 内存
    try:
        import httpx
        httpx.post(
            f"http://model-svc:{get_settings().model_port}/admin/aliases",
            json=alias.model_dump(),
            timeout=5.0,
        )
    except Exception:
        pass
    return {"ok": True}


@router.patch("/models/{alias_name}/route")
def patch_route(alias_name: str, route: dict, _: None = Depends(require_admin)):
    with session_scope() as s:
        row = s.get(ModelAliasRow, alias_name)
        if not row:
            raise HTTPException(404, "alias not found")
        if "primary" in route:
            row.primary = route["primary"]
        if "fallbacks" in route:
            row.fallbacks = route["fallbacks"]
        if "params" in route:
            row.params = route["params"]
    return {"ok": True}


@router.get("/models/usage")
def usage(alias: Optional[str] = None, minutes: int = 60, _: None = Depends(require_admin)):
    from packages.common.redis_client import get_sync_redis
    import time

    r = get_sync_redis()
    now = int(time.time() // 60) * 60
    buckets = [now - 60 * i for i in range(minutes)]

    total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    for b in buckets:
        key = f"usage:{b}:alias:{alias}" if alias else f"usage:{b}:total"
        data = r.hgetall(key)
        for k in total:
            total[k] += int(data.get(k, 0))
    return total