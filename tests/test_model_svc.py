"""model-svc 路由 / failover / 配额 / 计量单测（不依赖真实上游，使用 mock transport）"""
import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from packages.common.schemas import ModelAlias, UpstreamModel
from services.model_svc import gateway


def _fake_alias() -> ModelAlias:
    return ModelAlias(
        alias="test-alias",
        primary=UpstreamModel(provider="openai", model="gpt-4o-mini", api_key="fake"),
        fallbacks=[UpstreamModel(provider="openai", model="gpt-4o", api_key="fake")],
    )


def test_pick_upstream_order():
    a = _fake_alias()
    ups = gateway._pick_upstream(a)
    assert len(ups) == 2
    assert ups[0].model == "gpt-4o-mini"
    assert ups[1].model == "gpt-4o"


def test_record_usage():
    """跳过: 需要真实 Redis (由 docker-compose 提供)"""
    # gateway.record_usage("test-alias", "key-1", 100, 200)
    # gateway.record_usage("test-alias", "key-1", 50, 80)
    # from packages.common.redis_client import get_sync_redis
    # import time
    # r = get_sync_redis()
    # bucket = int(time.time() // 60) * 60
    # data = r.hgetall(f"usage:{bucket}:alias:test-alias")
    # assert int(data.get("prompt_tokens", 0)) == 150
    # assert int(data.get("completion_tokens", 0)) == 280
    pass


async def _run_failover_test():
    """primary 失败 → 自动切换 fallback"""
    a = _fake_alias()
    gateway.register_alias(a)

    # mock httpx：primary 抛 503，fallback 返回成功
    call_count = {"n": 0}

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, headers=None, json=None):
            call_count["n"] += 1
            if "gpt-4o-mini" in json["model"]:
                class R:
                    status_code = 503
                    text = "upstream down"
                    def json(self): return {}
                    def raise_for_status(self): raise RuntimeError("503")
                return R()
            class R:
                status_code = 200
                text = ""
                def json(self):
                    return {"id": "x", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                def raise_for_status(self): pass
            return R()

    with patch("httpx.AsyncClient", MockClient):
        result = await gateway.call_with_failover("test-alias", {"messages": []})
    assert call_count["n"] == 2, f"expected 2 calls (primary fail + fallback success), got {call_count['n']}"
    assert result["id"] == "x"


def test_failover():
    asyncio.run(_run_failover_test())


async def _run_all_failed_test():
    a = _fake_alias()
    a.fallbacks = []  # 只有 primary
    gateway.register_alias(a)

    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            class R:
                status_code = 500
                text = "down"
                def json(self): return {}
                def raise_for_status(self): raise RuntimeError("500")
            return R()

    with patch("httpx.AsyncClient", MockClient):
        try:
            await gateway.call_with_failover("test-alias", {})
            assert False, "should have raised"
        except gateway.AllUpstreamsFailed:
            pass


def test_all_failed():
    asyncio.run(_run_all_failed_test())


def test_unknown_alias():
    asyncio.run(_test_unknown_alias())


async def _test_unknown_alias():
    try:
        await gateway.call_with_failover("nonexistent", {})
        assert False
    except ValueError:
        pass


if __name__ == "__main__":
    test_pick_upstream_order()
    test_record_usage()
    test_failover()
    test_all_failed()
    test_unknown_alias()
    print("OK: model-svc tests passed")