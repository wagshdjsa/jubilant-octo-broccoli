"""OpenAI 兼容契约测试: 请求/响应字段一致"""
from packages.common.schemas import (
    AgentConfig,
    ChatCompletionRequest,
    ChatMessage,
)


def test_chat_request_minimal():
    req = ChatCompletionRequest(
        model="agent:abc-123",
        messages=[ChatMessage(role="user", content="hi")],
    )
    assert req.model == "agent:abc-123"
    assert req.stream is False


def test_chat_request_with_version():
    req = ChatCompletionRequest(
        model="agent:abc-123@v3",
        messages=[ChatMessage(role="user", content="hi")],
    )
    assert "@v3" in req.model


def test_agent_config_serialize_roundtrip():
    c = AgentConfig(
        model="chat-default",
        prompt="You are X.",
        tools=["calculator"],
        knowledge_bases=["kb-1"],
    )
    j = c.model_dump(mode="json")
    c2 = AgentConfig.model_validate(j)
    assert c2.model == c.model
    assert c2.tools == c.tools


if __name__ == "__main__":
    test_chat_request_minimal()
    test_chat_request_with_version()
    test_agent_config_serialize_roundtrip()
    print("OK: openai compat tests passed")