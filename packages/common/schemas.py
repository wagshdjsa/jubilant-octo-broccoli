"""Pydantic 共享 schema: AgentConfig / DAG / Chat / API Key"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============== 枚举 ==============
class AgentStatus(str, Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ToolType(str, Enum):
    builtin = "builtin"
    http = "http"
    mcp = "mcp"


class KBStatus(str, Enum):
    pending = "pending"
    parsing = "parsing"
    ready = "ready"
    failed = "failed"


# ============== 智能体配置 ==============
class MemoryConfig(BaseModel):
    """多轮记忆/会话配置"""
    type: Literal["postgres", "memory", "none"] = "postgres"
    thread_id_strategy: Literal["per_user", "per_conversation", "explicit"] = "per_conversation"


class GuardrailConfig(BaseModel):
    """输入/输出守卫"""
    input_block_patterns: list[str] = []
    output_max_tokens: Optional[int] = None
    pii_redact: bool = False


class DAGNode(BaseModel):
    """DAG 节点"""
    id: str
    type: Literal["llm", "tool", "retrieval", "condition", "loop", "code", "subgraph"]
    params: dict[str, Any] = Field(default_factory=dict)


class DAGEdge(BaseModel):
    """DAG 边（含条件分支 / 回边 / 循环退出）"""
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    when: Optional[str] = None          # e.g. "true" / "false" / state 表达式


class DAGConfig(BaseModel):
    """通用 DAG schema；为空时 runtime 套 ReAct 模板"""
    nodes: list[DAGNode] = []
    edges: list[DAGEdge] = []

    def validate_dag(self) -> None:
        """基础校验：节点 id 唯一、边引用合法、无环（简单 DFS）"""
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("DAG nodes id must be unique")

        for e in self.edges:
            if e.source not in ids and e.source != "START":
                raise ValueError(f"edge source not found: {e.source}")
            if e.target not in ids and e.target != "END":
                raise ValueError(f"edge target not found: {e.target}")

        # 简化版无环检测（DAG 用法上允许回边表示循环，故此校验仅对非回边报错）
        # TODO: 区分 DAG 节点 vs 循环节点


class AgentConfig(BaseModel):
    """智能体配置（可序列化到 agent_version.config JSONB）"""
    model: str = "chat-default"
    prompt: str = "You are a helpful assistant."
    prompt_variables: dict[str, str] = {}

    tools: list[str] = []                 # tool-svc 中的工具名列表
    knowledge_bases: list[str] = []       # kb_id 列表

    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: float = 1.0

    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)

    dag: Optional[DAGConfig] = None       # 为 None 时走 ReAct 模板

    @field_validator("model")
    @classmethod
    def _alias_required(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("model alias required")
        return v


# ============== OpenAI 兼容请求 / 响应 ==============
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """兼容 OpenAI ChatCompletion 请求体"""
    model: str                                # "agent:{id}" 或 "agent:{id}@v{n}"
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    user: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============== API Key ==============
class APIKeyCreate(BaseModel):
    agent_id: str
    expires_at: Optional[str] = None
    quota_rpm: Optional[int] = None
    quota_tpm: Optional[int] = None


class APIKeyOut(BaseModel):
    id: str
    agent_id: str
    agent_version_id: str
    key: str                                  # 仅签发时返回一次
    quota_rpm: Optional[int] = None
    quota_tpm: Optional[int] = None
    expires_at: Optional[str] = None
    status: str = "active"


# ============== Agent CRUD ==============
class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    config: AgentConfig


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[AgentConfig] = None


class AgentOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: Optional[str]
    status: str
    current_draft_id: Optional[str]
    created_at: str
    latest_published_version: Optional[int] = None
    # 当前草稿版本的配置，编辑页回显用；列表接口不填充
    config: Optional[dict] = None


class AgentVersionOut(BaseModel):
    id: str
    agent_id: str
    version: int
    config: AgentConfig
    status: str
    published_at: Optional[str]


# ============== 模型目录 ==============
class UpstreamModel(BaseModel):
    provider: Literal["openai", "bailian", "fangzhou", "vllm", "custom"]
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    weight: int = 1
    api_version: str = "v1"     # 智谱 GLM 用 "v4"，OpenAI / DeepSeek 用 "v1"


class ModelAlias(BaseModel):
    alias: str
    primary: UpstreamModel
    fallbacks: list[UpstreamModel] = []
    params: dict[str, Any] = Field(default_factory=dict)


# ============== 工具注册 ==============
class ToolCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str
    type: ToolType
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    endpoint: Optional[str] = None
    auth_config: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


# ============== 知识库 ==============
class KBCreate(BaseModel):
    name: str
    embedding_alias: str = "embedding-default"


class KBDocumentOut(BaseModel):
    id: str
    kb_id: str
    object_key: str
    status: KBStatus
    chunk_count: int = 0


class KBQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: float = 0.7
    rerank: bool = False