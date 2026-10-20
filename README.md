# Agent Platform — 智能体中台

> 任务书：智能体中台微服务系统 — 总体设计与实施计划  
> 文档：见 [`docs/architecture.md`](docs/architecture.md)

## 1. 系统组成

```
agent-platform/
├── docker-compose.yml          # postgres+pgvector / redis / rabbitmq / 全部服务
├── docs/architecture.md        # 完整设计文档（交付物）
├── packages/common/            # 共享: config / db / schemas / langsmith
├── services/
│   ├── api-gateway/            # 鉴权 / 限流 / 审计
│   ├── agent-runtime/          # ★ 配置驱动 LangGraph 引擎 + OpenAI 兼容接口
│   ├── agent-manager/          # ★ 智能体 CRUD / 版本 / 发布 / API Key
│   ├── knowledge-svc/          # RAG: 解析 / Embedding / 检索
│   ├── tool-svc/               # 工具注册与执行
│   └── model-svc/              # ★ 模型网关: 路由 / failover / 配额 / 计量
├── web/console/                # 管理台 + DAG 可视化（React Flow）
└── tests/                      # graph_builder / openai 契约 / model-svc 路由
```

## 2. 启动方式

### 2.1 一键启动（Docker Compose）

```bash
cd agent-platform
cp .env.example .env
docker compose up -d
# 等待 postgres/redis/rabbitmq 就绪（≈30s）
docker compose logs -f model-svc agent-runtime agent-manager
```

服务启动后访问：

| 服务                      | 地址                                          |
| ----------------------- | ------------------------------------------- |
| API Gateway             | <http://localhost:8000>                     |
| agent-manager 控制台 API   | <http://localhost:8001/docs>                |
| agent-runtime OpenAI 兼容 | <http://localhost:8002/v1/chat/completions> |
| knowledge-svc           | <http://localhost:8003/docs>                |
| tool-svc                | <http://localhost:8004/docs>                |
| model-svc               | <http://localhost:8005/docs>                |

### 2.2 本地开发（Poetry venv）

```bash
cd agent-platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 启动基础设施
docker compose up -d postgres redis rabbitmq
# 启动任一服务
cd services/agent-runtime && uvicorn main:app --reload --port 8002
```

## 3. 端到端示例：创建→发布→调用

```bash
# ① 创建草稿
AGENT_ID=$(curl -s -X POST http://localhost:8001/api/v1/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${MANAGER_TOKEN}" \
  -d '{
        "name":"客服助手",
        "config":{
          "model":"chat-default",
          "prompt":"你是专业客服，使用中文礼貌回答",
          "tools":[],
          "knowledge_bases":[]
        }
      }' | jq -r .id)

# ② 调试
curl -X POST http://localhost:8001/api/v1/agents/${AGENT_ID}/debug/chat \
  -H "Authorization: Bearer ${MANAGER_TOKEN}" \
  -d '{"messages":[{"role":"user","content":"你好"}]}'

# ③ 发布
curl -X POST http://localhost:8001/api/v1/agents/${AGENT_ID}/publish \
  -H "Authorization: Bearer ${MANAGER_TOKEN}"

# ④ 签发 API Key
KEY=$(curl -X POST http://localhost:8001/api/v1/apikeys \
  -H "Authorization: Bearer ${MANAGER_TOKEN}" \
  -d "{\"agent_id\":\"${AGENT_ID}\"}" | jq -r .key)

# ⑤ 通过 OpenAI 兼容协议调用
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Authorization: Bearer ${KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"agent:${AGENT_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"
```

## 4. 关键设计原则

1. **配置驱动**：智能体的全部能力由 `agent_version.config` 描述；切换模型/工具/知识库无需改代码
2. **模型网关隔离**：runtime / knowledge-svc 一律经 model-svc，不直连 LLM
3. **可观测内置**：默认集成 LangSmith，trace metadata 携带 tenant/agent/version
4. **图缓存**：相同 `{agent_id}@{version}` 编译结果 LRU + Redis 缓存
5. **DAG 双模式**：简单配置走 ReAct 模板；含 `dag` 字段走通用 DAG 编译

## 5. 后续里程碑

- M2：管理台 React Flow 画布落地（节点：llm/tool/retrieval/condition/loop/code/subgraph）
- M3：模型灰度权重分流 + 智能体版本灰度叠加
- M4：LangSmith 评估数据集 / 在线 feedback 闭环
- M5：K8s 化（每服务独立 Deployment + HPA，runtime 水平扩展）
