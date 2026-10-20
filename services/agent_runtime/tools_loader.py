"""tools_loader: 动态从 tool-svc 加载工具 + 调用 model-svc"""
from __future__ import annotations

import os
from typing import Any

import httpx


MODEL_SVC_URL = os.getenv("MODEL_SVC_URL", "http://model-svc:8005")
TOOL_SVC_URL = os.getenv("TOOL_SVC_URL", "http://tool-svc:8004")
KNOWLEDGE_SVC_URL = os.getenv("KNOWLEDGE_SVC_URL", "http://knowledge-svc:8003")


async def call_model_chat(
    alias: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    agent_id: str = "",
    version: int = 0,
    api_key_id: str | None = None,
) -> dict[str, Any]:
    """经 model-svc 调 LLM（不直连）"""
    payload = {
        "model": alias,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "metadata": {"agent_id": agent_id, "version": version},
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    headers = {}
    if api_key_id:
        headers["X-API-Key-Id"] = api_key_id

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{MODEL_SVC_URL}/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


async def call_knowledge(kb_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
    """调 knowledge-svc 检索"""
    payload = {"query": query, "top_k": top_k, "score_threshold": 0.7}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{KNOWLEDGE_SVC_URL}/api/v1/kb/{kb_id}/query", json=payload)
        r.raise_for_status()
        return r.json()


async def list_tools(tool_names: list[str]) -> list[dict[str, Any]]:
    """从 tool-svc 取工具元数据"""
    if not tool_names:
        return []
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{TOOL_SVC_URL}/api/v1/tools/lookup", json={"names": tool_names})
        r.raise_for_status()
        return r.json()["tools"]


async def execute_tool_calls(tool_calls: list[dict[str, Any]], allowed_tools: list[str]) -> list[dict[str, Any]]:
    """执行 LLM 上一步返回的 tool_calls"""
    results = []
    for tc in tool_calls:
        fn = tc.get("function", tc)
        name = fn.get("name", "")
        if name not in allowed_tools:
            results.append({
                "tool_call_id": tc.get("id"),
                "content": f"error: tool '{name}' not allowed",
            })
            continue
        try:
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json
                args = json.loads(args) if args else {}
            r = await execute_tool_call_by_name(name, args)
            results.append({"tool_call_id": tc.get("id"), "content": str(r)})
        except Exception as e:
            results.append({"tool_call_id": tc.get("id"), "content": f"error: {e}"})
    return results


async def execute_tool_call_by_name(name: str, args: dict[str, Any]) -> Any:
    """转发到 tool-svc 执行"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{TOOL_SVC_URL}/api/v1/tools/{name}/invoke", json=args)
        r.raise_for_status()
        return r.json().get("result")