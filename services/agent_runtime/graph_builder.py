"""
graph_builder: 配置驱动的 LangGraph StateGraph 编译

支持两种模式:
1. 简单模式（config.dag 为空）→ ReAct 模板: retrieve → llm ⇄ tool → end
2. DAG 模式（config.dag 非空）→ 通用 DAG 编译

核心:
- 缓存: {agent_id}@{version} -> compiled graph（LRU + Redis 配置哈希校验）
- checkpointer: PostgresSaver（多轮会话状态持久化）
- 模型调用: 全部经 model-svc（不直连 LLM）
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from packages.common.langsmith_utils import configure_langsmith
from packages.common.schemas import AgentConfig, DAGConfig


configure_langsmith()


# ============== AgentState ==============
class AgentState(TypedDict):
    """LangGraph 状态"""
    messages: Annotated[list, add_messages]
    rag_context: str
    agent_id: str
    version: int
    metadata: dict[str, Any]


# ============== 节点函数（可注入，便于测试替换） ==============
async def retrieve_node(state: AgentState, *, config: AgentConfig) -> AgentState:
    """RAG 检索：调 knowledge-svc"""
    if not config.knowledge_bases:
        return state

    from services.agent_runtime.tools_loader import call_knowledge
    last_user = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if not last_user:
        return state
    query = last_user.content
    chunks: list[str] = []
    for kb_id in config.knowledge_bases:
        try:
            res = await call_knowledge(kb_id, query, top_k=5)
            chunks.extend(c["text"] for c in res.get("chunks", []))
        except Exception:
            pass
    state["rag_context"] = "\n\n".join(chunks)
    return state


_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "AIMessageChunk": "assistant",
    "system": "system",
    "tool": "tool",
}


def _to_openai_role(msg: Any) -> str:
    """LangChain 消息角色 -> OpenAI 兼容角色

    LangChain 用 human/ai，OpenAI 兼容协议（含智谱 GLM）只认 user/assistant，
    直接透传会被上游以「角色信息不正确」(code 1214) 拒绝。
    """
    raw = getattr(msg, "type", None) or getattr(msg, "role", None) or "user"
    return _ROLE_MAP.get(str(raw), "user")


async def llm_node(state: AgentState, *, config: AgentConfig) -> AgentState:
    """调 LLM：经 model-svc（统一协议 + 别名路由 + failover）"""
    from services.agent_runtime.tools_loader import call_model_chat

    msgs: list[dict[str, Any]] = []
    sys_content = config.prompt
    if state.get("rag_context"):
        sys_content += f"\n\n## 参考知识\n{state['rag_context']}"
    msgs.append({"role": "system", "content": sys_content})

    for m in state["messages"]:
        msgs.append({"role": _to_openai_role(m), "content": m.content})

    response = await call_model_chat(
        alias=config.model,
        messages=msgs,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        agent_id=state["agent_id"],
        version=state["version"],
    )

    msg = response["choices"][0]["message"]
    # tool_calls 为 None 时不能传给 AIMessage（pydantic v1 只接受 list 或缺省）
    ai_kwargs: dict[str, Any] = {"content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        ai_kwargs["tool_calls"] = msg["tool_calls"]
    state["messages"] = state["messages"] + [AIMessage(**ai_kwargs)]
    return state


async def tool_node(state: AgentState, *, config: AgentConfig) -> AgentState:
    """执行 LLM 上一步决定的 tool_calls"""
    from services.agent_runtime.tools_loader import execute_tool_calls
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return state
    results = await execute_tool_calls(last.tool_calls, config.tools)
    new_msgs = [
        ToolMessage(content=r["content"], tool_call_id=r["tool_call_id"])
        for r in results
    ]
    state["messages"] = state["messages"] + new_msgs
    return state


def should_continue(state: AgentState) -> str:
    """ReAct 路由：是否还有 tool_calls 未处理"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "end"


# ============== 编译 ==============
def _config_hash(config: AgentConfig) -> str:
    return hashlib.sha256(
        json.dumps(config.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


# 用 hash 字符串作为 lru_cache key（dict 不可哈希）
_graph_cache: dict[str, Any] = {}


def _get_or_build_react(hash_key: str, config: AgentConfig) -> Any:
    if hash_key in _graph_cache:
        return _graph_cache[hash_key]
    graph = _do_build_react(config)
    _graph_cache[hash_key] = graph
    # 简单 LRU：超出 64 清空
    if len(_graph_cache) > 64:
        _graph_cache.pop(next(iter(_graph_cache)))
    return graph


def _do_build_react(config: AgentConfig) -> Any:
    """简单模式编译: ReAct 模板"""
    builder = StateGraph(AgentState)

    # 节点
    async def _retrieve(s): return await retrieve_node(s, config=config)
    async def _llm(s): return await llm_node(s, config=config)
    async def _tools(s): return await tool_node(s, config=config)

    builder.add_node("retrieve", _retrieve)
    builder.add_node("llm", _llm)
    if config.tools:
        builder.add_node("tools", _tools)

    # 边
    if config.knowledge_bases:
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "llm")
    else:
        builder.add_edge(START, "llm")

    if config.tools:
        builder.add_conditional_edges(
            "llm",
            should_continue,
            {"tools": "tools", "end": END},
        )
        builder.add_edge("tools", "llm")
    else:
        builder.add_edge("llm", END)

    # checkpointer: 骨架用内存 checkpointer；生产应接 PostgresSaver
    from langgraph.checkpoint.memory import MemorySaver
    checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)


def _build_dag(config: AgentConfig) -> Any:
    """DAG 模式编译: 通用 DAG"""
    if not config.dag:
        return _get_or_build_react(_config_hash(config), config)

    # 简化实现：把 DAG 的 nodes 顺序串起来当作线性 pipeline。
    # 完整实现应按 type 分发到 retrieve / llm / tool / condition / loop / subgraph 等节点工厂。
    builder = StateGraph(AgentState)

    prev = START
    for n in config.dag.nodes:
        node_name = n.id

        if n.type == "retrieval":
            async def _r(s, _p=n.params): return await _retrieve_with_params(s, _p)
            builder.add_node(node_name, _r)
        elif n.type == "llm":
            async def _l(s, _p=n.params): return await _llm_with_params(s, _p)
            builder.add_node(node_name, _l)
        elif n.type == "tool":
            async def _t(s, _p=n.params): return await _tool_with_params(s, _p)
            builder.add_node(node_name, _t)
        else:
            # TODO: condition / loop / code / subgraph
            async def _stub(s, _n=node_name): return s
            builder.add_node(node_name, _stub)

        builder.add_edge(prev, node_name)
        prev = node_name

    builder.add_edge(prev, END)

    from langgraph.checkpoint.memory import MemorySaver
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


async def _retrieve_with_params(state: AgentState, params: dict[str, Any]) -> AgentState:
    from services.agent_runtime.tools_loader import call_knowledge
    last_user = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if not last_user:
        return state
    try:
        res = await call_knowledge(
            params.get("kb_id"),
            last_user.content,
            top_k=int(params.get("top_k", 5)),
        )
        state["rag_context"] = "\n\n".join(c["text"] for c in res.get("chunks", []))
    except Exception:
        pass
    return state


async def _llm_with_params(state: AgentState, params: dict[str, Any]) -> AgentState:
    from services.agent_runtime.tools_loader import call_model_chat

    msgs = []
    sys = params.get("system", "")
    if state.get("rag_context"):
        sys += f"\n\n## 参考知识\n{state['rag_context']}"
    if sys:
        msgs.append({"role": "system", "content": sys})
    for m in state["messages"]:
        msgs.append({"role": _to_openai_role(m), "content": m.content})

    response = await call_model_chat(
        alias=params.get("alias", "chat-default"),
        messages=msgs,
        temperature=params.get("temperature", 0.7),
        agent_id=state["agent_id"],
        version=state["version"],
    )
    msg = response["choices"][0]["message"]
    state["messages"] = state["messages"] + [AIMessage(content=msg.get("content", ""))]
    return state


async def _tool_with_params(state: AgentState, params: dict[str, Any]) -> AgentState:
    from services.agent_runtime.tools_loader import execute_tool_call_by_name
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return state
    results = []
    for tc in last.tool_calls:
        try:
            r = await execute_tool_call_by_name(params.get("tool_name"), tc.get("args", {}))
            results.append({
                "tool_call_id": tc["id"],
                "content": str(r),
            })
        except Exception as e:
            results.append({"tool_call_id": tc["id"], "content": f"error: {e}"})
    state["messages"] = state["messages"] + [
        ToolMessage(content=r["content"], tool_call_id=r["tool_call_id"]) for r in results
    ]
    return state


# ============== 公开入口 ==============
def build_graph(agent_id: str, version: int, config: AgentConfig) -> Any:
    """按 agent_id@version 编译并缓存"""
    if config.dag:
        return _build_dag(config)
    return _get_or_build_react(_config_hash(config), config)


def invalidate_cache(agent_id: str, version: int) -> None:
    """发布新版本时调用"""
    _graph_cache.clear()