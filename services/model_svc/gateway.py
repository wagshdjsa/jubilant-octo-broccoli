"""模型目录 + 路由策略 + failover + 配额 + 计量

设计:
- 别名 alias -> 上游链 (primary + fallbacks)
- failover: 超时/限流/5xx 自动沿链切换
- 配额: Redis token bucket (TPM/RPM)
- 计量: 调用成功后写入 usage_record
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from packages.common.config import get_settings
from packages.common.redis_client import TokenBucket
from packages.common.schemas import ModelAlias, UpstreamModel


@dataclass
class RouteDecision:
    upstream: UpstreamModel
    alias: str


class AllUpstreamsFailed(Exception):
    pass


# ---- 模型目录（内存 + 可持久化到 DB） ----
_aliases: dict[str, ModelAlias] = {}


def register_alias(alias: ModelAlias) -> None:
    _aliases[alias.alias] = alias


def get_alias(name: str) -> Optional[ModelAlias]:
    if name in _aliases:
        return _aliases[name]
    # 默认别名兜底：读取 settings 里配的供应商 + 模型
    settings = get_settings()
    if name == settings.model_default_alias:
        default = ModelAlias(
            alias=name,
            primary=UpstreamModel(
                provider="custom",                              # 智谱归到 custom（OpenAI 兼容）
                model=settings.zhipu_default_model,             # glm-4-flash
                base_url=settings.openai_api_base,
                api_key=settings.openai_api_key,
                api_version="v4",                               # 智谱 chat 端点用 /v4/
            ),
        )
        _aliases[name] = default
        return default
    return None


def list_aliases() -> list[ModelAlias]:
    return list(_aliases.values())


# ---- 配额 ----
def check_quota(api_key_id: str, rpm_limit: Optional[int], tpm_limit: Optional[int], tokens_estimated: int) -> bool:
    if rpm_limit:
        ok = TokenBucket.check_and_consume(
            key=f"quota:rpm:{api_key_id}",
            capacity=rpm_limit,
            refill_per_sec=rpm_limit / 60.0,
        )
        if not ok:
            return False
    if tpm_limit:
        ok = TokenBucket.check_and_consume(
            key=f"quota:tpm:{api_key_id}",
            capacity=tpm_limit,
            refill_per_sec=tpm_limit / 60.0,
            cost=tokens_estimated,
        )
        if not ok:
            return False
    return True


# ---- 计量 ----
def record_usage(alias: str, api_key_id: Optional[str], prompt_tokens: int, completion_tokens: int) -> None:
    """写入 Redis 聚合 + 异步刷 PG（此处只做 Redis 部分）"""
    from packages.common.redis_client import get_sync_redis

    r = get_sync_redis()
    bucket_now = int(time.time() // 60) * 60
    pipe = r.pipeline()
    pipe.hincrby(f"usage:{bucket_now}:alias:{alias}", "prompt_tokens", prompt_tokens)
    pipe.hincrby(f"usage:{bucket_now}:alias:{alias}", "completion_tokens", completion_tokens)
    pipe.hincrby(f"usage:{bucket_now}:alias:{alias}", "calls", 1)
    if api_key_id:
        pipe.hincrby(f"usage:{bucket_now}:key:{api_key_id}", "prompt_tokens", prompt_tokens)
        pipe.hincrby(f"usage:{bucket_now}:key:{api_key_id}", "completion_tokens", completion_tokens)
        pipe.hincrby(f"usage:{bucket_now}:key:{api_key_id}", "calls", 1)
    pipe.expire(f"usage:{bucket_now}:alias:{alias}", 60 * 60 * 24 * 30)
    pipe.expire(f"usage:{bucket_now}:key:{api_key_id}", 60 * 60 * 24 * 30)
    pipe.execute()


# ---- 路由 + failover ----
def _pick_upstream(alias: ModelAlias) -> list[UpstreamModel]:
    """primary 在前，fallback 链在后"""
    return [alias.primary, *alias.fallbacks]


async def _invoke_upstream(upstream: UpstreamModel, payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """按 provider 适配；本骨架统一为 OpenAI 兼容协议

    endpoint 形如 "v1/chat/completions" / "v1/embeddings"
    如果 upstream.api_version 与 endpoint 的版本前缀不一致，按 upstream.api_version 替换
    """
    base = upstream.base_url or get_settings().openai_api_base
    base = base.rstrip("/")
    endpoint = endpoint.lstrip("/")

    # 把 endpoint 的版本号替换成 upstream.api_version
    parts = endpoint.split("/", 1)
    if parts and parts[0] in ("v1", "v2", "v3", "v4", "v5"):
        endpoint = f"{upstream.api_version}/{parts[1]}"
    else:
        endpoint = f"{upstream.api_version}/{endpoint}"

    url = f"{base}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {upstream.api_key or get_settings().openai_api_key}",
        "Content-Type": "application/json",
    }
    # 模型名替换
    body = {**payload, "model": upstream.model}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code in (429, 500, 502, 503, 504):
            raise RuntimeError(f"upstream {upstream.model} {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()


async def call_with_failover(alias_name: str, payload: dict[str, Any], endpoint: str = "v1/chat/completions") -> dict[str, Any]:
    """沿 fallback 链切换直到成功"""
    alias = get_alias(alias_name)
    if not alias:
        raise ValueError(f"unknown alias: {alias_name}")

    last_err: Optional[Exception] = None
    for upstream in _pick_upstream(alias):
        try:
            return await _invoke_upstream(upstream, payload, endpoint)
        except Exception as e:
            last_err = e
            continue
    raise AllUpstreamsFailed(f"all upstreams failed for alias {alias_name}: {last_err}")


# ---- 便捷接口：chat / embedding / rerank ----
async def chat_completion(alias: str, request_body: dict[str, Any], api_key_id: Optional[str] = None,
                          rpm_limit: Optional[int] = None, tpm_limit: Optional[int] = None) -> dict[str, Any]:
    estimated = len(str(request_body.get("messages", []))) // 2  # 粗估
    if api_key_id and not check_quota(api_key_id, rpm_limit, tpm_limit, estimated):
        raise PermissionError("quota exceeded")

    response = await call_with_failover(alias, request_body, "v1/chat/completions")
    usage = response.get("usage", {})
    if usage:
        record_usage(
            alias,
            api_key_id,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )
    return response


async def embedding(alias: str, input_: str | list[str], api_key_id: Optional[str] = None) -> list[list[float]]:
    payload = {"input": input_}
    response = await call_with_failover(alias, payload, "v1/embeddings")
    return [d["embedding"] for d in response["data"]]


async def rerank(query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    """简化版 rerank：可替换为 bge-reranker / cohere"""
    # 占位实现：按 query 在 doc 中出现频次排序
    scored = []
    q = query.lower()
    for i, doc in enumerate(documents):
        score = doc.lower().count(q) / max(len(doc), 1)
        scored.append({"index": i, "score": score, "document": doc})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]