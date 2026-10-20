"""统一入口: 反向代理 + 中间件（鉴权 / 限流 / 审计）"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith
from packages.common.redis_client import TokenBucket


configure_langsmith()


RUNTIME_URL = os.getenv("RUNTIME_URL", f"http://agent-runtime:{get_settings().agent_runtime_port}")
MANAGER_URL = os.getenv("MANAGER_URL", f"http://agent-manager:{get_settings().agent_manager_port}")


app = FastAPI(title="api-gateway", version="0.1.0")


@app.middleware("http")
async def audit_and_ratelimit(request: Request, call_next):
    start = time.time()

    # 1. 限流（按路径粒度）
    if request.url.path.startswith("/v1/"):
        api_key_id = request.headers.get("X-API-Key-Id", "anonymous")
        ok = TokenBucket.check_and_consume(
            key=f"gateway:rl:{api_key_id}",
            capacity=600,             # 默认 600 RPM
            refill_per_sec=10.0,
        )
        if not ok:
            return JSONResponse({"error": "rate limited"}, status_code=429)

    response = await call_next(request)

    # 2. 审计日志
    from packages.common.redis_client import get_sync_redis
    r = get_sync_redis()
    elapsed_ms = int((time.time() - start) * 1000)
    r.hincrby("audit:count", f"{request.method} {request.url.path}", 1)
    r.lpush("audit:log", f"{int(time.time())} {request.method} {request.url.path} {response.status_code} {elapsed_ms}ms")
    r.ltrim("audit:log", 0, 9999)

    return response


# ===== 鉴权依赖（运行时路径使用） =====
async def require_api_key(request: Request, authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing Authorization header")
    return authorization[7:]


# ===== 反向代理 =====
@app.post("/v1/chat/completions")
async def chat_completions_proxy(request: Request, authorization: str | None = Header(None)):
    """透明转发到 agent-runtime"""
    api_key = await require_api_key(request, authorization)
    body = await request.body()

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{RUNTIME_URL}/v1/chat/completions",
            content=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as r:
            if r.headers.get("content-type", "").startswith("text/event-stream"):
                async def _proxy():
                    async for chunk in r.aiter_bytes():
                        yield chunk
                return StreamingResponse(_proxy(), media_type="text/event-stream", headers=dict(r.headers))
            content = await r.aread()
            return JSONResponse(
                content=__import__("json").loads(content),
                status_code=r.status_code,
                headers={k: v for k, v in r.headers.items() if k.lower() in ("x-langsmith-trace-url",)},
            )


# ===== 管理 API 代理 =====
# 逐跳头不能透传：Host/Content-Length 交给 httpx 自己算，
# 否则会出现 "Found multiple Host: headers"（request.headers 里是小写 host，
# 再手动塞一个大写 Host，dict 里就成了两个）。
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding",
    "upgrade", "content-length", "content-encoding", "accept-encoding",
}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "PUT"])
async def manager_proxy(request: Request, path: str, authorization: str | None = Header(None)):
    body = await request.body()
    target_url = f"{MANAGER_URL}/api/{path}"

    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    if authorization:
        fwd_headers["Authorization"] = authorization

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.request(
            method=request.method,
            url=target_url,
            content=body,
            params=request.query_params,
            headers=fwd_headers,
        )

    # 204/304 没有响应体，不能强行 .json()
    if r.status_code in (204, 304) or not r.content:
        return Response(status_code=r.status_code)

    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return JSONResponse(content=r.json(), status_code=r.status_code)
    return Response(content=r.content, status_code=r.status_code, media_type=ct)


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().api_gateway_port, reload=False)