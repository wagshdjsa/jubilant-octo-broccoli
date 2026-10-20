# 智能体中台 — 架构设计文档

> 版本: v0.1 · 2026-10
> 对应任务书: `blazing-beacon-turing-IXCjtGFl(1).md`

---

## 目录

1. [总体架构](#1-总体架构)
2. [服务清单与职责](#2-服务清单与职责)
3. [agent-runtime：LangGraph 动态编排引擎](#3-agent-runtimelanggraph-动态编排引擎)
4. [agent-manager：配置与发布](#4-agent-manager配置与发布)
5. [knowledge-svc：RAG 管线](#5-knowledge-svcrag-管线)
6. [model-svc：模型网关](#6-model-svc模型网关)
7. [DAG 可视化编排](#7-dag-可视化编排)
8. [端到端流程：创建→发布→调用](#8-端到端流程创建发布调用)
9. [对外接口契约](#9-对外接口契约)
10. [LangSmith 集成与可观测性](#10-langsmith-集成与可观测性)
11. [部署与扩展](#11-部署与扩展)
12. [数据模型与表结构](#12-数据模型与表结构)
13. [安全与限流](#13-安全与限流)
14. [后续里程碑](#14-后续里程碑)

---

## 1. 总体架构

### 1.1 分层视图（自外向内）

```
外部系统 / 前端
    │  HTTPS (OpenAI 兼容 / 管理 API)
┌───▼─────────────────────────────────────────────┐
│ 接入层: API Gateway (鉴权、限流、路由、审计日志)    │
├───▼───────────┬───────────────┬─────────────────┤
│ agent-runtime │ agent-manager │ knowledge-svc   │
│ (执行引擎)    │ (配置/发布)   │ (RAG)           │
│ LangGraph    │ CRUD/版本/发布 │ 解析/Embedding  │
├───────────────┼───────────────┴─────────────────┤
│ tool-svc        │ msg-queue (RabbitMQ: 文档解析等) │
├──────────────────────────────────────────────────┤
│ model-svc (模型网关：路由/failover/配额/计量)     │
├──────────────────────────────────────────────────┤
│ 数据层: PostgreSQL(pgvector) / Redis / 对象存储   │
├──────────────────────────────────────────────────┤
│ 观测层: LangSmith (Tracing / Evaluation / Feedback)│
└─────────────────────────────────────────────────┘
```

### 1.2 设计原则

1. **配置驱动**：智能体的全部能力（模型/工具/知识库/提示词/守卫）由 `agent_version.config` 描述；切换能力不改代码。
2. **模型网关隔离**：runtime / knowledge-svc 一律经 model-svc，不直连任何 LLM 供应商，便于统一配额/计量/failover。
3. **快照不可变**：已发布的 `agent_version` 为不可变 JSONB 快照，回滚 = 重新发布旧版本。
4. **API Key 绑定版本**：每个 API Key 绑定到具体 `agent_version`，可独立设置配额。
5. **可观测内置**：LangSmith 通过环境变量自动接入；trace metadata 携带 tenant/agent/version。
6. **DAG 双模式**：简单配置 → ReAct 模板；含 `dag` 字段 → 通用 DAG 编译。

---

## 2. 服务清单与职责

| 服务 | 职责 | 关键依赖 |
| --- | --- | --- |
| **api-gateway** | 统一入口；API Key 鉴权、限流（Redis token bucket）、请求审计 | Redis |
| **agent-runtime** | 配置驱动编译 StateGraph；OpenAI 兼容 `/v1/chat/completions`（同步+SSE） | LangGraph / PG Checkpointer / model-svc |
| **agent-manager** | 智能体 CRUD、版本/发布、API Key 签发、模型目录、Playground 转发 | SQLAlchemy / PG |
| **knowledge-svc** | 文档上传→MQ 异步解析→Embedding→pgvector；检索 API（向量+rerank） | LangChain loader / pgvector / model-svc |
| **tool-svc** | 工具注册中心（builtin/http/mcp），运行时被调用时执行 | LangChain Tools / MCP client |
| **model-svc** | 上游适配（OpenAI/百炼/方舟/vLLM）、别名路由、failover、TPM/RPM 配额、token 计量 | Redis |
| **web/console** | 管理台：智能体/版本/模型管理 + DAG 可视化画布（React Flow） | agent-manager API |
| **观测集成（非独立服务）** | 各服务 LangSmith SDK / 环境变量上报 | langsmith SDK |

### 2.1 通信方式

- **同步 REST**：服务间调用走 OpenAPI 契约（agent-manager ↔ runtime, runtime ↔ tool-svc, runtime ↔ knowledge-svc, all ↔ model-svc）。
- **异步 MQ (RabbitMQ)**：文档解析、批量评估等长任务。
- **数据库共享**：元数据统一在 PG，向量在 pgvector（与元数据同库，运维成本最低，可平滑迁移到 Qdrant/Milvus）。

---

## 3. agent-runtime：LangGraph 动态编排引擎

### 3.1 设计要点

不硬编码图，而是按 `AgentConfig` 动态装配 `StateGraph`：

```
AgentConfig(model, prompt, tools[], knowledge_bases[], memory, guardrails)
        │
        ▼ GraphBuilder.build(config) -> CompiledGraph
┌──────────────────────────────────────────────┐
│ StateGraph(AgentState: messages, rag_context)│
│  ├─ [条件] retrieve_node ── 若配置了知识库：     │
│  │     调 knowledge-svc 检索 → 注入 context    │
│  ├─ llm_node (ChatModel + system prompt)     │
│  ├─ [条件] tool_node ── 若 tools 非空：         │
│  │     ToolNode(动态加载 tool-svc 工具)        │
│  └─ ReAct 循环: llm ⇄ tool 直至无 tool_calls   │
│ checkpointer = PostgresSaver (会话/多轮记忆)    │
└──────────────────────────────────────────────┘
```

### 3.2 模型引用

- 配置中的 `model` 是**模型别名**（如 `chat-default`），由 model-svc 解析为具体供应商/模型/参数；
- 切换上游（OpenAI ↔ 百炼 ↔ 自部署 vLLM）不动智能体配置；
- 同一别名支持 primary + fallback 链、权重分流（灰度）。

### 3.3 DAG 编排模式（v2）

通用 DAG schema，前端可视化画布（React Flow）产出，节点类型：

| 节点 | 含义 |
| --- | --- |
| `llm` | 调用 LLM（节点参数：alias / prompt_id / output_keys） |
| `tool` | 调用工具（节点参数：tool_name / args_template） |
| `retrieval` | 知识库检索（节点参数：kb_id / top_k / score_threshold） |
| `condition` | 条件分支（节点参数：expr；边：when true/false 各自跳向） |
| `loop` | 循环体（节点参数：max_iter / exit_condition） |
| `code` | 轻量表达式 / 模板渲染（节点参数：expr） |
| `subgraph` | 嵌套子图（节点参数：ref_agent_id / ref_version） |

`GraphBuilder` 双模式：
- **简单模式**（无 `dag` 字段）→ ReAct 模板：retrieve → llm ⇄ tool → end。
- **DAG 模式**（含 `dag.nodes[] / dag.edges[]`）→ 按节点类型编译，分支→条件边、循环→回边+退出条件、子图→嵌套 graph 节点。

### 3.4 图缓存

```
key = f"{agent_id}@{version}"
cache: LRU(内存) + Redis(配置快照哈希校验)
```

避免每次重建图；配置变更（发布新版本）自动失效。

### 3.5 会话状态

- `thread_id = "{app_id}:{conversation_id}"` 由 PG checkpointer 持久化多轮上下文；
- 通过 `metadata.conversation_id` 映射；前端管理 `conversation_id`。

### 3.6 流式输出

```python
async for event in graph.astream_events(inputs, version="v2"):
    if event["event"] == "on_chat_model_stream":
        # 转 OpenAI chunk: {"choices":[{"delta":{"content":"..."}}]}
    elif event["event"] == "on_tool_start":
        # tool_calls chunk
```

---

## 4. agent-manager：配置与发布

### 4.1 核心数据模型

```sql
CREATE TABLE agent (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT,                     -- draft | published
  current_draft_id UUID,           -- 指向 agent_version(id)
  created_by UUID,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE agent_version (
  id UUID PRIMARY KEY,
  agent_id UUID REFERENCES agent(id),
  version INT NOT NULL,
  config JSONB NOT NULL,           -- 不可变快照
  status TEXT,                     -- draft | published | archived
  published_at TIMESTAMPTZ,
  UNIQUE(agent_id, version)
);

CREATE TABLE api_key (
  id UUID PRIMARY KEY,
  agent_version_id UUID REFERENCES agent_version(id),
  key_hash TEXT NOT NULL,          -- 仅存哈希
  quota_rpm INT,
  quota_tpm INT,
  status TEXT,                     -- active | revoked
  expires_at TIMESTAMPTZ
);

CREATE TABLE knowledge_base (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  name TEXT,
  embedding_alias TEXT
);

CREATE TABLE kb_document (
  id UUID PRIMARY KEY,
  kb_id UUID REFERENCES knowledge_base(id),
  object_key TEXT,                -- 对象存储路径
  status TEXT,                    -- pending | parsing | ready | failed
  chunk_count INT
);

CREATE TABLE tool (
  name TEXT PRIMARY KEY,
  type TEXT,                      -- builtin | http | mcp
  schema JSONB,
  endpoint TEXT,
  auth_config JSONB
);
```

### 4.2 草稿 / 发布 / 回滚

- **草稿**：每个 `agent` 有且仅有一个 `agent_version.status='draft'`，所有 PATCH 落到该草稿。
- **发布**：草稿 → 新 `agent_version`（自增 version，config 拷贝为不可变 JSONB）→ `status='published'` → 同时将旧 published 版本置为 archived。
- **回滚**：`POST /versions/{vid}/rollback` → 把目标 version 重新置为 published。
- **复制**：从已发布版本复制为新草稿 → 进入下一轮迭代。

---

## 5. knowledge-svc：RAG 管线

### 5.1 异步摄取

```
客户端上传文件
  → POST /api/v1/kb/{id}/documents (multipart)
  → 写对象存储 + 落 kb_document(status=pending)
  → MQ 任务: ingest.run(document_id)
  → LangChain loader 解析 (pdf/docx/md/...)
  → RecursiveCharacterTextSplitter 分块
  → 调 model-svc(/v1/embeddings, alias=embedding-default)
  → 写入 pgvector(embedding vector(1536))
  → kb_document.status=ready
```

### 5.2 检索

```
POST /api/v1/kb/{id}/query
{ "query": "...", "top_k": 5, "score_threshold": 0.7, "rerank": true }
```

- 内部：Embedding(query) → pgvector ANN → 阈值过滤 → 可选 rerank（经 model-svc `/v1/rerank`）→ 返回 chunks。

---

## 6. model-svc：模型网关

### 6.1 定位

对标商业 MaaS 平台模型层，补齐"模型是配置字符串"的缺口。

```
runtime / knowledge-svc / tool-svc
        │  统一 OpenAI 兼容协议 (chat / embedding / rerank)
        ▼
┌───────────────────────────────────────────────┐
│ model-svc                                      │
│  ├─ 模型目录: alias → {provider, model, params} │
│  ├─ 路由策略: primary + fallback 链、权重分流    │
│  ├─ 配额限流: per tenant/key TPM/RPM (Redis)   │
│  ├─ 计量: token 用量按 租户/Key/模型 聚合出账    │
│  └─ 上游适配: OpenAI 兼容 / 百炼 / 方舟 / vLLM  │
└───────────────────────────────────────────────┘
```

### 6.2 接口

| 路径 | 说明 |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI 兼容（请求体携带 `metadata.alias`） |
| `POST /v1/embeddings` | OpenAI 兼容（请求体携带 `metadata.alias`） |
| `POST /v1/rerank` | 内部协议（query + docs + top_n） |
| `GET /admin/aliases` | 列出别名与路由策略 |
| `PATCH /admin/aliases/{alias}/route` | 改路由/权重 |
| `GET /admin/usage` | 用量聚合（按 alias/key/tenant） |

### 6.3 Failover

```python
async def call_with_failover(alias, request):
    route = await get_route(alias)         # [primary, fallback1, fallback2]
    last_err = None
    for upstream in route:
        try:
            return await invoke(upstream, request)
        except (Timeout, RateLimit, 5xx) as e:
            last_err = e
            continue    # 自动沿 fallback 链切换
    raise AllUpstreamsFailed(last_err)
```

### 6.4 配额

- Redis token bucket：key = `quota:{api_key_id}:{window_min}`，按 TPM/RPM 扣减。
- 触发限流时返回 `429` + `Retry-After`，同时计入计量（失败不计费）。

### 6.5 计量

- 每次成功调用：写 Redis 计数 + 异步刷 PG `usage_record`；
- trace metadata 携带 alias → LangSmith 可按 alias 聚合成本。

---

## 7. DAG 可视化编排

### 7.1 画布

`web/console` 基于 **React Flow** 实现拖拽编排：

- 左侧节点面板：llm / tool / retrieval / condition / loop / code / subgraph
- 中间画布：拖拽连线生成 DAG
- 右侧属性面板：节点参数 + 输出 schema
- 顶部工具栏：保存为 draft / 从已发布版本复制 / 校验 DAG

### 7.2 与代码的等价关系

画布产出 = DAG JSON，存入 `agent_version.config.dag`：

```json
{
  "dag": {
    "nodes": [
      {"id": "n1", "type": "retrieval", "params": {"kb_id": "...", "top_k": 5}},
      {"id": "n2", "type": "llm", "params": {"alias": "chat-default", "prompt_id": "..."}},
      {"id": "n3", "type": "condition", "params": {"expr": "len(state.rag_context) > 0"}}
    ],
    "edges": [
      {"from": "n1", "to": "n2"},
      {"from": "n2", "to": "n3"},
      {"from": "n3", "to": "END", "when": "true"},
      {"from": "n3", "to": "n1", "when": "false"}   // 回边 = 循环
    ]
  }
}
```

无 DAG 字段 → 自动套 ReAct 模板（retrieve → llm ⇄ tool → end）。

### 7.3 调试链路

- 画布选中节点 → 调 `GET /agents/{id}/debug/runs?node=n1` → 返回该节点最近 N 次 trace 片段；
- 节点 ↔ trace run 映射（通过 LangSmith run name = `{agent_id}.{node_id}`）。

---

## 8. 端到端流程：创建→发布→调用

```
① 创建草稿   POST /agents                   → agent + draft version
② 配置迭代   PATCH /agents/{id}/draft       → 模型/工具/知识库/提示词/DAG
③ 调试       POST /agents/{id}/debug/chat   → runtime 执行，trace 上报 LangSmith
                                              （metadata: tenant/agent/version）
                                              响应头: X-LangSmith-Trace-URL
④ 发布       POST /agents/{id}/publish      → 草稿固化为 version 快照(不可变)
                                              → 签发 API Key(绑定版本、配额)
⑤ 外部调用   POST /v1/chat/completions      → 网关鉴权/限流 → runtime 执行
                                              → trace + token 用量上报 LangSmith
⑥ 观测闭环   LangSmith: trace 检索 / 在线 feedback 回流 / 离线评估集评估
⑦ 版本迭代   基于已发布版本复制为新草稿 → 重复 ②~④
            支持回滚（重新发布旧版本）
```

### 8.1 调用时序

```
客户端                 网关               runtime             model-svc           LLM
 │  POST /v1/chat      │                    │                    │                │
 │ ───────────────────►│                    │                    │                │
 │                     │ 鉴权/限流/路由      │                    │                │
 │                     │───────────────────►│                    │                │
 │                     │                    │ GraphBuilder.build │                │
 │                     │                    │ astream_events     │                │
 │                     │                    │───────────────────►│                │
 │                     │                    │                    │ invoke upstream│
 │                     │                    │                    │───────────────►│
 │                     │                    │                    │◄────chunk─────│
 │                     │                    │◄────SSE chunk─────│                │
 │ ◄────SSE chunk──────│◄──────SSE chunk────│                    │                │
```

---

## 9. 对外接口契约

### 9.1 OpenAI 兼容（运行时）

`POST /v1/chat/completions`
Header: `Authorization: Bearer <api_key>`

```jsonc
{
  "model": "agent:6f3c...e21",          // 或 "agent:6f3c...e21@v3"
  "messages": [{"role":"user","content":"你好"}],
  "temperature": 0.7,
  "stream": true,
  "user": "end-user-123",
  "metadata": {"conversation_id": "conv-9f8a"}
}
```

- `stream=true` → SSE，`on_chat_model_stream` 事件 → `chat.completion.chunk`；tool 调用 → `tool_calls` chunk。
- 非流式 → 聚合最终 state 输出标准响应，含 `usage`（从 trace run 统计回填）。

### 9.2 管理侧 API（agent-manager）

| 方法/路径 | 说明 |
| --- | --- |
| `POST/GET/PATCH/DELETE /api/v1/agents` | 智能体 CRUD |
| `GET /api/v1/agents/{id}/versions` | 版本列表 |
| `POST /api/v1/agents/{id}/publish` | 发布草稿 |
| `POST /api/v1/versions/{vid}/rollback` | 回滚 |
| `POST /api/v1/agents/{id}/debug/chat` | Playground 调试 |
| `POST/DELETE /api/v1/apikeys` | API Key 签发/吊销 |
| `GET/POST /api/v1/models` · `PATCH /api/v1/models/{alias}/route` · `GET /api/v1/models/usage` | 模型目录/路由/用量 |
| `POST /api/v1/knowledge-bases` · `POST /api/v1/kb/{id}/documents` · `POST /api/v1/kb/{id}/query` | 知识库 |
| `POST /api/v1/tools` · `GET /api/v1/tools` | 工具注册 |
| `POST /api/v1/evaluations` · `GET /api/v1/evaluations/{id}` | 触发/查询评估 |

---

## 10. LangSmith 集成与可观测性

1. **Tracing**：
   - 各服务设置 `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_PROJECT=agent-platform-{env}`；
   - 每次执行注入 `metadata={tenant_id, agent_id, version, api_key_id}` 与 `tags=["agent:{id}", "v{n}"]`。
2. **调试链路**：`debug/chat` 响应头返回 `X-LangSmith-Trace-URL`，前端跳转。
3. **反馈回流**：`POST /runs/{run_id}/feedback` 写回 LangSmith（在线评分）。
4. **评估闭环**：管理台创建评估数据集（从历史 trace 筛选入集）→ 后台 MQ 任务 → 调 `langsmith.evaluate()` 对比版本质量 → 辅助发布决策。
5. **成本监控**：trace 携带 token 用量，按 API Key / 租户聚合出账。

---

## 11. 部署与扩展

### 11.1 Docker Compose（骨架演示）

参见 [`docker-compose.yml`](../docker-compose.yml)。一键启动全部服务。

### 11.2 K8s（生产）

- 每服务独立 `Deployment` + `Service`；
- `agent-runtime` HPA：CPU + 自定义指标（QPS）；
- `model-svc`：上游稳定性差，独立扩缩容 + 慢启动；
- 配置文件走 `ConfigMap` + `Secret`；
- 数据库/Redis/RabbitMQ：托管服务（RDS / ElastiCache / 自托管）。

### 11.3 容量与扩展点

- **runtime** 横向扩展（无状态 + PG checkpointer 共享状态）；
- **model-svc** 限流应放网关侧 + 自身双层（粗粒度网关 / 细粒度网关），避免上游被打挂；
- **knowledge-svc** 摄取与检索可分服务（ingest-svc / retrieve-svc），摄取走 MQ，检索无状态可无限扩展；
- **agent-manager** 偏管理流量，单实例即可。

---

## 12. 数据模型与表结构

参见 §4.1 完整 DDL。关键点：

- 所有元数据 `tenant_id` 必填（多租户隔离）；
- `agent_version.config` 不可变（发布后 UPDATE 触发审计告警）；
- `kb_document.chunk_count` 冗余用于快速展示；
- `usage_record (api_key_id, alias, prompt_tokens, completion_tokens, ts)` 用量明细。

---

## 13. 安全与限流

| 维度 | 措施 |
| --- | --- |
| 鉴权 | 网关：API Key 哈希校验；管理 API：Bearer Admin Token |
| 限流 | Redis token bucket：per API Key TPM/RPM |
| 审计 | 网关写 `audit_log (api_key_id, path, status, ts)` |
| 多租户 | 所有表 `tenant_id` 过滤；RLS（Row Level Security）可启用 |
| 密钥管理 | API Key 仅存 `key_hash`；上游 LLM Key 走 K8s Secret |
| 输入校验 | Pydantic v2 严格模式；DAG JSON schema 校验 |

---

## 14. 后续里程碑

| 版本 | 范围 |
| --- | --- |
| **M0（本次）** | 完整设计文档 + 可运行骨架（model-svc / agent-runtime / agent-manager / knowledge-svc / tool-svc / api-gateway）+ Docker Compose |
| **M1** | 管理台基础 CRUD（智能体/版本/模型/API Key） |
| **M2** | DAG 可视化画布（React Flow）落地 |
| **M3** | 模型灰度权重分流 + 智能体版本灰度叠加 |
| **M4** | LangSmith 评估数据集 / 在线 feedback 闭环 |
| **M5** | K8s 化、HPA、多租户 RLS |
| **M6** | 替换向量库到 Qdrant / Milvus（按规模） |

---

> 附录 A：[`README.md`](../README.md) — 启动与端到端示例
> 附录 B：源码骨架见各 `services/*` 目录