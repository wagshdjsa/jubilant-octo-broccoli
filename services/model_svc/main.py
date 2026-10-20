"""model-svc HTTP 入口（FastAPI）"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith
from packages.common.schemas import ModelAlias
from . import gateway


configure_langsmith()


class ChatRequest(BaseModel):
    model: str                                # 模型别名 or 上游模型名
    messages: list[dict[str, Any]]
    temperature: Optional[float] = 0.7
    stream: bool = False
    metadata: dict[str, Any] = {}


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


class RerankRequest(BaseModel):
    model: str = "rerank-default"
    query: str
    documents: list[str]
    top_n: int = 5


app = FastAPI(title="model-svc", version="0.1.0")


@app.get("/health")
async def health():
    return {"ok": True}


# ===== 统一协议 =====
@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, x_api_key_id: Optional[str] = Header(None)):
    try:
        result = await gateway.chat_completion(
            alias=req.model,
            request_body=req.model_dump(exclude={"metadata"}),
            api_key_id=x_api_key_id,
        )
        return result
    except PermissionError:
        raise HTTPException(status_code=429, detail="quota exceeded")
    except gateway.AllUpstreamsFailed as e:
        import logging
        logging.exception("all upstreams failed")
        raise HTTPException(status_code=502, detail=f"all upstreams failed: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import logging
        logging.exception("chat completion error")
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingRequest, x_api_key_id: Optional[str] = Header(None)):
    try:
        vecs = await gateway.embedding(alias=req.model, input_=req.input, api_key_id=x_api_key_id)
        return {"object": "list", "data": [{"object": "embedding", "embedding": v, "index": i} for i, v in enumerate(vecs)]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/v1/rerank")
async def rerank(req: RerankRequest):
    return {"results": await gateway.rerank(req.query, req.documents, req.top_n)}


# ===== 管理 =====
@app.get("/admin/aliases")
async def list_aliases():
    return [a.model_dump() for a in gateway.list_aliases()]


@app.post("/admin/aliases")
async def create_alias(alias: ModelAlias):
    gateway.register_alias(alias)
    return {"ok": True, "alias": alias.alias}


@app.patch("/admin/aliases/{alias_name}/route")
async def patch_route(alias_name: str, route: dict[str, Any]):
    a = gateway.get_alias(alias_name)
    if not a:
        raise HTTPException(404, "alias not found")
    if "primary" in route:
        a.primary = route["primary"]
    if "fallbacks" in route:
        a.fallbacks = route["fallbacks"]
    gateway.register_alias(a)
    return {"ok": True}


@app.get("/admin/usage")
async def usage(alias: Optional[str] = None, api_key_id: Optional[str] = None, minutes: int = 60):
    """按时间桶聚合最近 minutes 分钟用量"""
    from packages.common.redis_client import get_sync_redis
    import time

    r = get_sync_redis()
    now = int(time.time() // 60) * 60
    buckets = [now - 60 * i for i in range(minutes)]

    total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    for b in buckets:
        if alias:
            key = f"usage:{b}:alias:{alias}"
        elif api_key_id:
            key = f"usage:{b}:key:{api_key_id}"
        else:
            continue
        data = r.hgetall(key)
        for k in total:
            total[k] += int(data.get(k, 0))
    return total


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().model_port, reload=False)