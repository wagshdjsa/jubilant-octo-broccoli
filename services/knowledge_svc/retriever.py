"""
retriever: 向量检索
- embedding(query) → pgvector ANN → 阈值过滤 → 可选 rerank
"""
from __future__ import annotations

import os
from typing import Any


MODEL_SVC_URL = os.getenv("MODEL_SVC_URL", "http://model-svc:8005")


async def retrieve(kb_id: str, query: str, top_k: int = 5, score_threshold: float = 0.7, rerank: bool = False) -> dict[str, Any]:
    """骨架：返回空结果"""
    import httpx

    # 1. embedding
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{MODEL_SVC_URL}/v1/embeddings",
            json={"model": "embedding-default", "input": query},
        )
        r.raise_for_status()
        _emb = r.json()["data"][0]["embedding"]

    # 2. pgvector ANN（骨架阶段不连真实库）
    chunks: list[dict[str, Any]] = []

    # 3. rerank
    if rerank and chunks:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{MODEL_SVC_URL}/v1/rerank",
                json={"query": query, "documents": [c["text"] for c in chunks], "top_n": top_k},
            )
            r.raise_for_status()
            results = r.json()["results"]
            chunks = [chunks[r["index"]] | {"score": r["score"]} for r in results]

    return {"chunks": chunks[:top_k]}