"""智能体 CRUD / 版本 / 发布 / API Key"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from packages.common.db import get_db
from packages.common.schemas import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    AgentVersionOut,
    APIKeyCreate,
    APIKeyOut,
)

from ..models import Agent, AgentVersion, APIKey


router = APIRouter(prefix="/api/v1")


# ---- 鉴权 ----
def require_admin(authorization: Optional[str] = Header(None)) -> None:
    from packages.common.config import get_settings
    s = get_settings()
    if not authorization or authorization != f"Bearer {s.manager_admin_token}":
        raise HTTPException(401, "invalid admin token")


def _to_agent_out(
    a: Agent,
    latest_published_version: Optional[int] = None,
    config: Optional[dict] = None,
) -> AgentOut:
    return AgentOut(
        id=str(a.id),
        tenant_id=str(a.tenant_id),
        name=a.name,
        description=a.description,
        status=a.status,
        current_draft_id=str(a.current_draft_id) if a.current_draft_id else None,
        created_at=a.created_at.isoformat() if a.created_at else "",
        latest_published_version=latest_published_version,
        config=config,
    )


# ===== 智能体 CRUD =====
@router.post("/agents", response_model=AgentOut, dependencies=[])
def create_agent(payload: AgentCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    # 校验 DAG
    if payload.config.dag:
        try:
            payload.config.dag.validate_dag()
        except ValueError as e:
            raise HTTPException(400, f"DAG invalid: {e}")

    # 简化：取一个默认 tenant（或从 token 解析）
    from packages.common.config import get_settings
    import os
    default_tenant = os.getenv("DEFAULT_TENANT_ID")
    if default_tenant:
        tenant_id = uuid.UUID(default_tenant)
    else:
        # 自动创建一个 demo tenant（首次启动）
        row = db.execute(text("SELECT id FROM tenant LIMIT 1")).fetchone()
        if row:
            tenant_id = row[0]
        else:
            tenant_id = uuid.uuid4()
            db.execute(text("INSERT INTO tenant (id, name) VALUES (:id, :n)"),
                       {"id": tenant_id, "n": "demo"})
            db.commit()

    agent = Agent(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        status="draft",
    )
    db.add(agent)
    db.flush()

    # 初始 draft version
    draft = AgentVersion(
        agent_id=agent.id,
        version=1,
        config=payload.config.model_dump(mode="json"),
        status="draft",
    )
    db.add(draft)
    db.flush()
    agent.current_draft_id = draft.id
    db.commit()
    db.refresh(agent)

    return _to_agent_out(agent, None, draft.config)


@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.execute(text("""
        SELECT a.*, MAX(av.version) FILTER (WHERE av.status='published') AS latest_pub
        FROM agent a
        LEFT JOIN agent_version av ON av.agent_id = a.id
        GROUP BY a.id
        ORDER BY a.created_at DESC
    """)).fetchall()
    return [
        AgentOut(
            id=str(r.id),
            tenant_id=str(r.tenant_id),
            name=r.name,
            description=r.description,
            status=r.status,
            current_draft_id=str(r.current_draft_id) if r.current_draft_id else None,
            created_at=r.created_at.isoformat() if r.created_at else "",
            latest_published_version=int(r.latest_pub) if r.latest_pub else None,
        )
        for r in rows
    ]


@router.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    a = db.get(Agent, uuid.UUID(agent_id))
    if not a:
        raise HTTPException(404, "agent not found")

    latest = db.execute(
        select(AgentVersion.version).where(
            AgentVersion.agent_id == a.id, AgentVersion.status == "published"
        ).order_by(AgentVersion.version.desc()).limit(1)
    ).scalar()

    # 带出草稿配置，前端编辑页用来回显 prompt / model / temperature
    draft_config = None
    if a.current_draft_id:
        draft = db.get(AgentVersion, a.current_draft_id)
        if draft is not None:
            draft_config = draft.config

    return _to_agent_out(a, latest, draft_config)


@router.patch("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: str, payload: AgentUpdate, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    a = db.get(Agent, uuid.UUID(agent_id))
    if not a:
        raise HTTPException(404, "agent not found")

    if payload.name is not None:
        a.name = payload.name
    if payload.description is not None:
        a.description = payload.description

    if payload.config is not None:
        if payload.config.dag:
            try:
                payload.config.dag.validate_dag()
            except ValueError as e:
                raise HTTPException(400, f"DAG invalid: {e}")

        draft = db.get(AgentVersion, a.current_draft_id)
        if draft is None:
            raise HTTPException(500, "draft missing")
        draft.config = payload.config.model_dump(mode="json")

    db.commit()
    db.refresh(a)

    # 回填最新发布版本号和草稿配置，否则前端拿到的会是 null
    latest = db.execute(
        select(AgentVersion.version).where(
            AgentVersion.agent_id == a.id, AgentVersion.status == "published"
        ).order_by(AgentVersion.version.desc()).limit(1)
    ).scalar()
    draft_config = None
    if a.current_draft_id:
        draft = db.get(AgentVersion, a.current_draft_id)
        if draft is not None:
            draft_config = draft.config
    return _to_agent_out(a, latest, draft_config)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    a = db.get(Agent, uuid.UUID(agent_id))
    if not a:
        raise HTTPException(404, "agent not found")

    # api_key.agent_version_id 是 NOT NULL，不能让外键级联置 NULL，
    # 必须按依赖顺序手动清理：api_key -> agent_version -> agent
    version_ids = db.execute(
        select(AgentVersion.id).where(AgentVersion.agent_id == a.id)
    ).scalars().all()

    if version_ids:
        for k in db.execute(select(APIKey).where(APIKey.agent_version_id.in_(version_ids))).scalars().all():
            db.delete(k)
        db.flush()
        for v in db.execute(select(AgentVersion).where(AgentVersion.id.in_(version_ids))).scalars().all():
            db.delete(v)
        db.flush()

    db.delete(a)
    db.commit()
    return {"ok": True}


# ===== 版本 / 发布 / 回滚 =====
@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionOut])
def list_versions(agent_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.execute(
        select(AgentVersion).where(AgentVersion.agent_id == uuid.UUID(agent_id)).order_by(AgentVersion.version.desc())
    ).scalars().all()
    return [
        AgentVersionOut(
            id=str(v.id),
            agent_id=str(v.agent_id),
            version=v.version,
            config=v.config,
            status=v.status,
            published_at=v.published_at.isoformat() if v.published_at else None,
        )
        for v in rows
    ]


@router.post("/agents/{agent_id}/publish", response_model=AgentVersionOut)
def publish(agent_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    a = db.get(Agent, uuid.UUID(agent_id))
    if not a:
        raise HTTPException(404, "agent not found")

    draft = db.get(AgentVersion, a.current_draft_id)
    if not draft:
        raise HTTPException(400, "no draft to publish")

    # 把当前 published 全部置 archived
    db.execute(
        text("UPDATE agent_version SET status='archived' WHERE agent_id=:a AND status='published'"),
        {"a": a.id},
    )

    # 草稿固化为新 published（version 自增）
    next_ver = (db.execute(
        select(AgentVersion.version).where(AgentVersion.agent_id == a.id).order_by(AgentVersion.version.desc()).limit(1)
    ).scalar() or 0) + 1

    new_version = AgentVersion(
        agent_id=a.id,
        version=next_ver,
        config=draft.config,                              # 不可变快照
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(new_version)
    db.flush()

    # 清空草稿（创建新 draft 给下次迭代）
    new_draft = AgentVersion(
        agent_id=a.id,
        version=next_ver + 1,
        config=draft.config,                              # 复制为新草稿基底
        status="draft",
    )
    db.add(new_draft)
    db.flush()
    a.current_draft_id = new_draft.id
    a.status = "published"
    db.commit()

    # 通知 runtime 失效缓存
    try:
        import httpx
        httpx.post(
            f"http://agent-runtime:8002/internal/cache/invalidate",
            json={"agent_id": str(a.id), "version": next_ver},
            timeout=2.0,
        )
    except Exception:
        pass

    return AgentVersionOut(
        id=str(new_version.id),
        agent_id=str(new_version.agent_id),
        version=new_version.version,
        config=new_version.config,
        status=new_version.status,
        published_at=new_version.published_at.isoformat(),
    )


@router.post("/versions/{version_id}/rollback")
def rollback(version_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    v = db.get(AgentVersion, uuid.UUID(version_id))
    if not v:
        raise HTTPException(404, "version not found")

    db.execute(
        text("UPDATE agent_version SET status='archived' WHERE agent_id=:a AND status='published'"),
        {"a": v.agent_id},
    )
    v.status = "published"
    v.published_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "version": v.version}


# ===== API Key =====
@router.post("/apikeys", response_model=APIKeyOut)
def create_apikey(payload: APIKeyCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    # 取最新 published version
    row = db.execute(
        text("""
            SELECT av.id FROM agent_version av
            WHERE av.agent_id = :a AND av.status = 'published'
            ORDER BY av.version DESC LIMIT 1
        """),
        {"a": payload.agent_id},
    ).fetchone()
    if not row:
        raise HTTPException(400, "agent has no published version")

    av_id = row[0]
    raw_key = "sk-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:7] + "****"

    new_key = APIKey(
        agent_version_id=av_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        quota_rpm=payload.quota_rpm,
        quota_tpm=payload.quota_tpm,
        expires_at=datetime.fromisoformat(payload.expires_at) if payload.expires_at else None,
        status="active",
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return APIKeyOut(
        id=str(new_key.id),
        agent_id=payload.agent_id,
        agent_version_id=str(new_key.agent_version_id),
        key=raw_key,
        quota_rpm=new_key.quota_rpm,
        quota_tpm=new_key.quota_tpm,
        expires_at=new_key.expires_at.isoformat() if new_key.expires_at else None,
        status=new_key.status,
    )


@router.delete("/apikeys/{key_id}")
def revoke_apikey(key_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    k = db.get(APIKey, uuid.UUID(key_id))
    if not k:
        raise HTTPException(404, "api key not found")
    k.status = "revoked"
    db.commit()
    return {"ok": True}