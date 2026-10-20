"""统一配置（从 .env 读取，所有服务复用）"""
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础设施 ----
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="agent_platform")
    postgres_user: str = Field(default="agent")
    postgres_password: str = Field(default="agent_pwd")
    database_url: Optional[str] = None

    redis_url: str = Field(default="redis://redis:6379/0")
    rabbitmq_url: str = Field(default="amqp://guest:guest@rabbitmq:5672/")

    # ---- 模型网关 / 上游 ----
    openai_api_base: str = Field(default="https://open.bigmodel.cn/api/paas")
    openai_api_key: str = Field(default="")
    model_default_alias: str = Field(default="chat-default")
    zhipu_default_model: str = Field(default="glm-4-flash")
    zhipu_embedding_model: str = Field(default="embedding-2")

    # ---- LangSmith ----
    langchain_tracing_v2: bool = Field(default=True)
    langchain_endpoint: str = Field(default="https://api.smith.langchain.com")
    langchain_api_key: str = Field(default="")
    langchain_project: str = Field(default="agent-platform-dev")

    # ---- 管理 Token ----
    manager_admin_token: str = Field(default="admin-token-replace-me")

    # ---- 端口 ----
    api_gateway_port: int = Field(default=8000)
    agent_manager_port: int = Field(default=8001)
    agent_runtime_port: int = Field(default=8002)
    knowledge_port: int = Field(default=8003)
    tool_port: int = Field(default=8004)
    model_port: int = Field(default=8005)

    def sync_db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def async_db_url(self) -> str:
        if self.database_url:
            return self.database_url.replace("psycopg2", "asyncpg")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()