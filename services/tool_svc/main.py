"""tool-svc HTTP 入口"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith
from packages.common.schemas import ToolCreate

from . import registry


configure_langsmith()


app = FastAPI(title="tool-svc", version="0.1.0")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/v1/tools")
async def list_tools():
    return [
        {"name": n, **{k: v for k, v in e.items() if k != "handler"}}
        for n, e in registry.TOOL_REGISTRY.items()
    ]


@app.post("/api/v1/tools")
async def create_tool(payload: ToolCreate):
    if payload.name in registry.TOOL_REGISTRY:
        raise HTTPException(409, "tool already exists")
    registry.register_tool(
        name=payload.name,
        type_=payload.type.value,
        description=payload.description,
        schema=payload.schema,
        handler=None,
        endpoint=payload.endpoint,
        auth_config=payload.auth_config,
    )
    return {"ok": True}


class LookupRequest(BaseModel):
    names: list[str]


@app.post("/api/v1/tools/lookup")
async def lookup(req: LookupRequest):
    return {"tools": registry.lookup(req.names)}


@app.post("/api/v1/tools/{name}/invoke")
async def invoke(name: str, args: dict[str, Any]):
    result = await registry.invoke(name, args)
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().tool_port, reload=False)