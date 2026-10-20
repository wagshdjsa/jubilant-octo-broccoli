"""内置工具示例 + HTTP/MCP 远程工具 stub"""
from __future__ import annotations

import os
from typing import Any


# ===== 内置工具 =====
async def calculator(expression: str) -> str:
    """简单计算器（仅允许数字和基本运算符）"""
    import re
    if not re.match(r"^[\d+\-*/().\s]+$", expression):
        return "error: invalid expression"
    try:
        return str(eval(expression))
    except Exception as e:
        return f"error: {e}"


async def http_request(url: str, method: str = "GET", headers: dict | None = None, body: Any = None) -> dict[str, Any]:
    """HTTP 调用（受白名单域名限制）"""
    import httpx
    whitelist = os.getenv("TOOL_HTTP_WHITELIST", "").split(",")
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    if whitelist and whitelist != [""] and domain not in whitelist:
        return {"error": f"domain {domain} not in whitelist"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.request(method, url, headers=headers or {}, json=body)
        return {"status": r.status_code, "text": r.text[:5000]}


async def current_time(_: str = "") -> str:
    """返回当前时间"""
    from datetime import datetime
    return datetime.now().isoformat()


# ===== 注册表 =====
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "calculator": {
        "type": "builtin",
        "description": "Evaluate a math expression",
        "schema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. '1 + 2 * 3'"}},
            "required": ["expression"],
        },
        "handler": calculator,
    },
    "http_request": {
        "type": "builtin",
        "description": "Make an HTTP request (whitelisted domains only)",
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string", "default": "GET"},
                "headers": {"type": "object"},
                "body": {},
            },
            "required": ["url"],
        },
        "handler": http_request,
    },
    "current_time": {
        "type": "builtin",
        "description": "Return current ISO time",
        "schema": {"type": "object", "properties": {}},
        "handler": current_time,
    },
}


def register_tool(name: str, type_: str, description: str, schema: dict, handler=None, endpoint: str | None = None, auth_config: dict | None = None) -> None:
    TOOL_REGISTRY[name] = {
        "type": type_,
        "description": description,
        "schema": schema,
        "handler": handler,
        "endpoint": endpoint,
        "auth_config": auth_config or {},
    }


def lookup(names: list[str]) -> list[dict[str, Any]]:
    """按名称取工具元数据（不含 handler）"""
    return [
        {"name": n, **{k: v for k, v in TOOL_REGISTRY[n].items() if k != "handler"}}
        for n in names
        if n in TOOL_REGISTRY
    ]


async def invoke(name: str, args: dict[str, Any]) -> Any:
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return {"error": f"tool '{name}' not found"}

    if entry["type"] == "builtin":
        handler = entry["handler"]
        try:
            return await handler(**args)
        except TypeError:
            # 兼容位置参数
            return await handler(args)
    elif entry["type"] == "http":
        # 转发到外部 HTTP endpoint
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(entry["endpoint"], json=args)
            return r.json()
    elif entry["type"] == "mcp":
        # TODO: MCP client
        return {"error": "MCP not yet implemented"}
    else:
        return {"error": f"unknown tool type: {entry['type']}"}