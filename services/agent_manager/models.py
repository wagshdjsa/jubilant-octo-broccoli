"""SQLAlchemy 模型: agent / agent_version / api_key / knowledge_base / kb_document / tool"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from packages.common.db import Base


def _uuid():
    return uuid.uuid4()


def _utcnow():
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenant"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Agent(Base):
    __tablename__ = "agent"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, default="draft", index=True)            # draft | published
    current_draft_id = Column(UUID(as_uuid=True))
    created_by = Column(UUID(as_uuid=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    versions = relationship("AgentVersion", back_populates="agent", cascade="all, delete-orphan")


class AgentVersion(Base):
    __tablename__ = "agent_version"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agent.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    config = Column(JSONB, nullable=False)              # 不可变快照
    status = Column(String, default="draft", index=True) # draft | published | archived
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    agent = relationship("Agent", back_populates="versions")
    api_keys = relationship("APIKey", back_populates="agent_version")

    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_version"),)


class APIKey(Base):
    __tablename__ = "api_key"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_version_id = Column(UUID(as_uuid=True), ForeignKey("agent_version.id"), nullable=False, index=True)
    key_hash = Column(String, nullable=False, index=True)
    key_prefix = Column(String)                          # 用于日志识别（sk-xxx****）
    quota_rpm = Column(Integer)
    quota_tpm = Column(Integer)
    status = Column(String, default="active")             # active | revoked
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    agent_version = relationship("AgentVersion", back_populates="api_keys")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    embedding_alias = Column(String, default="embedding-default")
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    documents = relationship("KBDocument", back_populates="kb", cascade="all, delete-orphan")


class KBDocument(Base):
    __tablename__ = "kb_document"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_base.id"), nullable=False, index=True)
    object_key = Column(String, nullable=False)
    filename = Column(String)
    status = Column(String, default="pending")           # pending | parsing | ready | failed
    chunk_count = Column(Integer, default=0)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    kb = relationship("KnowledgeBase", back_populates="documents")


class Tool(Base):
    __tablename__ = "tool"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String, nullable=False, unique=True)
    type = Column(String, nullable=False)                # builtin | http | mcp
    description = Column(Text, default="")
    schema = Column(JSONB, default=dict)
    endpoint = Column(String)
    auth_config = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class UsageRecord(Base):
    """用量明细（PG 持久化）"""
    __tablename__ = "usage_record"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    api_key_id = Column(UUID(as_uuid=True), index=True)
    alias = Column(String, index=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    ts = Column(DateTime(timezone=True), default=_utcnow, index=True)


class ModelAlias(Base):
    """模型目录（持久化）"""
    __tablename__ = "model_alias"

    alias = Column(String, primary_key=True)
    primary = Column(JSONB, nullable=False)
    fallbacks = Column(JSONB, default=list)
    params = Column(JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)