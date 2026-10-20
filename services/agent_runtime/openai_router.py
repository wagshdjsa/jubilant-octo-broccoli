"""
openai_router: /v1/chat/completions 入口

- 鉴权: 从 API Key 解析 agent_id + version
- 同步模式: 聚合最终 state 输出标准响应
- 流式模式: astream_events → SSE chunk
- trace: 注入 LangSmith metadata
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from packages.common.config import get_settings
from packages.common.db import get_db
from packages.common.langsmith_utils import make_metadata, make_tags
from packages.common.schemas import ChatCompletionRequest


router = APIRouter()


def _parse_agent_model(model_str: str) -> tuple[str, Optional[int]]:
    """解析 model 字段: agent:{id} 或 agent:{id}@v{n}"""
    if not model_str.startswith("agent:"):
        raise HTTPException(400, "model must start with 'agent:'")
    body = model_str[len("agent:"):]
    if "@v" in body:
        agent_id, ver = body.split("@v", 1)
        return agent_id, int(ver)
    return body, None


def _resolve_api_key(api_key: str, db: Session) -> dict[str, Any]:
    """查 api_key 表 + 关联 agent_version，校验状态"""
    import hashlib
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    row = db.execute(
        text("""
            SELECT ak.id AS api_key_id, ak.quota_rpm, ak.quota_tpm, ak.expires_at, ak.agent_version_id,
                  av.config, av.version, av.agent_id
            FROM api_key ak
            JOIN agent_version av ON av.id = ak.agent_version_id
            WHERE ak.key_hash = :h AND ak.status = 'active'
        """),
        {"h": key_hash},
    ).fetchone()
    if not row:
        raise HTTPException(401, "invalid api key")
    if row.expires_at and row.expires_at < _utcnow():
        raise HTTPException(401, "api key expired")
    return dict(row._mapping)


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _load_config(av_row: dict[str, Any]) -> Any:
    """从 DB JSONB 反序列化为 AgentConfig"""
    from packages.common.schemas import AgentConfig
    return AgentConfig.model_validate(av_row["config"])


def _load_debug_version(agent_id: str, version: Optional[int], db: Session) -> dict[str, Any]:
    """内部调试通道（管理台 Playground）用：不经 api_key，直接取配置

    不指定 version 时优先取当前草稿，这样 Playground 能即时反映未发布的修改。
    """
    if version:
        row = db.execute(
            text("""
                SELECT av.id AS agent_version_id, av.config, av.version, av.agent_id
                FROM agent_version av
                WHERE av.agent_id = :aid AND av.version = :ver
                ORDER BY av.created_at DESC
                LIMIT 1
            """),
            {"aid": agent_id, "ver": version},
        ).fetchone()
    else:
        row = db.execute(
            text("""
                SELECT av.id AS agent_version_id, av.config, av.version, av.agent_id
                FROM agent a
                JOIN agent_version av ON av.id = a.current_draft_id
                WHERE a.id = :aid
            """),
            {"aid": agent_id},
        ).fetchone()
        if not row:
            # 没有草稿就退回最新已发布版本
            row = db.execute(
                text("""
                    SELECT av.id AS agent_version_id, av.config, av.version, av.agent_id
                    FROM agent_version av
                    WHERE av.agent_id = :aid AND av.status = 'published'
                    ORDER BY av.version DESC
                    LIMIT 1
                """),
                {"aid": agent_id},
            ).fetchone()

    if not row:
        raise HTTPException(404, "agent version not found")

    d = dict(row._mapping)
    d["agent_id"] = str(d["agent_id"])
    d["api_key_id"] = "debug"
    d["quota_rpm"] = None
    d["quota_tpm"] = None
    d["expires_at"] = None
    return d


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing Authorization header")

    api_key = authorization[len("Bearer "):].strip()
    body_dict = await request.json()
    req = ChatCompletionRequest.model_validate(body_dict)

    # 1. 鉴权
    # 管理台 Playground 用 admin token 走内部通道，不校验 api_key、不占配额。
    # 这只在本服务未被公网直接暴露时成立（对外只经 api-gateway）。
    is_internal = bool(api_key) and api_key == get_settings().manager_admin_token

    if is_internal:
        agent_id_from_key, requested_version = _parse_agent_model(req.model)
        api_info = _load_debug_version(agent_id_from_key, requested_version, db)
        version = requested_version or api_info["version"]
    else:
        api_info = _resolve_api_key(api_key, db)
        agent_id_from_key = str(api_info["agent_id"])

        # 2. model 字段校验
        requested_agent_id, requested_version = _parse_agent_model(req.model)
        if requested_agent_id != agent_id_from_key:
            raise HTTPException(403, "api key does not match agent")

        # 3. 装载配置（指定版本 or 最新已发布）
        version = requested_version or api_info["version"]

    config = _load_config(api_info)

    # 4. trace metadata
    trace_md = make_metadata(
        tenant_id="",                                       # TODO: 从 api_key 关联
        agent_id=agent_id_from_key,
        version=version,
        api_key_id=str(api_info["api_key_id"]),
    )

    # 5. 构建图
    from .graph_builder import build_graph
    graph = build_graph(agent_id_from_key, version, config)

    # 6. thread_id
    conversation_id = req.metadata.get("conversation_id") or str(uuid.uuid4())
    thread_id = f"{agent_id_from_key}:{conversation_id}"

    # 7. 执行
    inputs = {
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "rag_context": "",
        "agent_id": agent_id_from_key,
        "version": version,
        "metadata": trace_md,
    }

    if req.stream:
        return StreamingResponse(
            _stream_response(graph, inputs, thread_id, req, api_info, trace_md),
            media_type="text/event-stream",
        )

    # 非流式
    final = None
    trace_url = None
    try:
        from langsmith import traceable

        @traceable(name=f"agent:{agent_id_from_key}@v{version}", metadata=trace_md, tags=make_tags(agent_id_from_key, version))
        async def _run():
            return await graph.ainvoke(inputs, config={"configurable": {"thread_id": thread_id}})

        result = await _run()
        final = result["messages"][-1].content if result.get("messages") else ""
        # TODO: 真实 trace URL 由 langsmith SDK 返回
        trace_url = None
    except ImportError:
        result = await graph.ainvoke(inputs, config={"configurable": {"thread_id": thread_id}})
        final = result["messages"][-1].content if result.get("messages") else ""

    response = {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": final or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,                              # TODO: 从 trace run 统计
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    headers = {}
    if trace_url:
        headers["X-LangSmith-Trace-URL"] = trace_url
    return _JSONResponse(response, headers=headers)


async def _stream_response(graph, inputs, thread_id, req, api_info, trace_md):
    """SSE 流式输出"""
    completion_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    # 首 chunk: role
    yield _sse_chunk({
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": req.model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    })

    async for event in graph.astream_events(inputs, version="v2", config={"configurable": {"thread_id": thread_id}}):
        kind = event.get("event", "")
        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            content = getattr(chunk, "content", "") if chunk else ""
            if content:
                yield _sse_chunk({
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                })
        elif kind == "on_tool_start":
            tool_run = event.get("name", "")
            yield _sse_chunk({
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"name": tool_run, "arguments": ""}}]},
                    "finish_reason": None,
                }],
            })

    # 尾 chunk
    yield _sse_chunk({
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": req.model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    })
    yield "data: [DONE]\n\n"


def _sse_chunk(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


class _JSONResponse(JSONResponse):
    """带响应头的 JSON 响应

    必须真正继承 JSONResponse，否则 FastAPI 不把它当 Response，
    会按普通对象序列化成 {"_resp": {...}} 这种错误结构。
    """
    def __init__(self, content, headers=None):
        super().__init__(content=content, headers=headers)