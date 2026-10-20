"""LangSmith 封装：注入 metadata / tags，trace URL 回填"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import get_settings


def configure_langsmith() -> None:
    """把环境变量写到 os.environ，langsmith SDK 自动读取"""
    s = get_settings()
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true" if s.langchain_tracing_v2 else "false")
    if s.langchain_endpoint:
        os.environ["LANGCHAIN_ENDPOINT"] = s.langchain_endpoint
    if s.langchain_api_key:
        os.environ["LANGCHAIN_API_KEY"] = s.langchain_api_key
    if s.langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = s.langchain_project


@contextmanager
def trace_run(
    name: str,
    *,
    run_type: str = "chain",
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> Iterator[dict[str, Any]]:
    """
    LangSmith trace 上下文。
    简化版：直接调用 langsmith SDK 的 traceable 装饰即可；
    这里给出一份可独立调用的封装，供不依赖装饰器的场景使用。
    """
    info: dict[str, Any] = {"name": name, "url": None}
    try:
        from langsmith import traceable  # noqa: F401

        # 真实使用应通过 @traceable 装饰器或 Client.run_on_patch
        # 此处仅占位，业务侧在 agent-runtime 用 @traceable
        yield info
    except ImportError:
        yield info
    finally:
        # TODO: 真实写入 trace URL（langsmith Client 返回 run.url）
        pass


def make_metadata(tenant_id: str, agent_id: str, version: int, api_key_id: Optional[str] = None) -> dict[str, Any]:
    """统一构造 trace metadata"""
    md = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "version": version,
    }
    if api_key_id:
        md["api_key_id"] = api_key_id
    return md


def make_tags(agent_id: str, version: int) -> list[str]:
    return [f"agent:{agent_id}", f"v{version}"]