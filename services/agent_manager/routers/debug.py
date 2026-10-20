"""Playground 调试: 转发到 agent-runtime"""
from __future__ import annotations

import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from packages.common.config import get_settings
from packages.common.schemas import ChatCompletionRequest


router = APIRouter(prefix="/api/v1")


def require_admin(authorization: Optional[str] = Header(None)):
    s = get_settings()
    if not authorization or authorization != f"Bearer {s.manager_admin_token}":
        raise HTTPException(401, "invalid admin token")


class DebugChatRequest(BaseModel):
    messages: list[dict]
    temperature: Optional[float] = 0.7
    stream: bool = False
    metadata: dict = {}


@router.post("/agents/{agent_id}/debug/chat")
async def debug_chat(agent_id: str, req: DebugChatRequest, _: None = Depends(require_admin)):
    """转发到 runtime 的内部调试通道（用 admin token 鉴权，不走 api_key）

    runtime 侧识别出 admin token 后跳过 api_key 校验，并优先加载草稿版本，
    这样 Playground 能即时验证未发布的修改。
    """
    s = get_settings()
    runtime_url = f"http://agent-runtime:{s.agent_runtime_port}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {s.manager_admin_token}"}
    payload = {
        "model": f"agent:{agent_id}",
        "messages": req.messages,
        "temperature": req.temperature,
        "stream": req.stream,
        "metadata": {**req.metadata, "_debug": True},
    }

    if req.stream:
        async def _proxy():
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("POST", runtime_url, json=payload, headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line:
                            yield line + "\n\n"
        from fastapi.responses import StreamingResponse
        return StreamingResponse(_proxy(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(runtime_url, json=payload, headers=headers)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"runtime error: {r.text[:300]}")
        return r.json()