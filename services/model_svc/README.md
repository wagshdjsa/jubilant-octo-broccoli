# model-svc

> 任务书标注 ★: 模型网关 — 统一上游适配、别名路由、failover、配额、计量

## 职责

- 对外暴露 OpenAI 兼容协议：`/v1/chat/completions`、`/v1/embeddings`、`/v1/rerank`
- 维护模型目录（alias → {provider, model, params}）
- 沿 fallback 链自动 failover（超时 / 限流 / 5xx）
- Redis token bucket 限流（TPM/RPM，按 api_key_id）
- 调用成功后聚合 token 用量

## 入口

`main.py` 注册路由；`gateway.py` 实现核心逻辑。

## 测试

```
cd services/model-svc
pytest tests/  # failover / 配额 / 计量单测
```

详见仓库根目录 `tests/`。