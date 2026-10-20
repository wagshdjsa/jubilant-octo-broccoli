"""knowledge-svc HTTP 入口"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from packages.common.config import get_settings
from packages.common.langsmith_utils import configure_langsmith
from packages.common.schemas import KBCreate, KBDocumentOut, KBQueryRequest

from . import ingest, retriever


configure_langsmith()


app = FastAPI(title="knowledge-svc", version="0.1.0")


@app.get("/health")
async def health():
    return {"ok": True}


# 内存存储（骨架）
_kbs: dict[str, dict] = {}
_docs: dict[str, KBDocumentOut] = {}


@app.post("/api/v1/knowledge-bases")
async def create_kb(payload: KBCreate):
    kb_id = str(uuid.uuid4())
    _kbs[kb_id] = {"id": kb_id, "name": payload.name, "embedding_alias": payload.embedding_alias}
    return _kbs[kb_id]


@app.get("/api/v1/knowledge-bases")
async def list_kbs():
    return list(_kbs.values())


@app.post("/api/v1/kb/{kb_id}/documents")
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    if kb_id not in _kbs:
        raise HTTPException(404, "kb not found")

    # 骨架：直接占位，等 MQ 消费
    doc_id = str(uuid.uuid4())
    object_key = f"{kb_id}/{doc_id}-{file.filename}"
    _docs[doc_id] = KBDocumentOut(
        id=doc_id,
        kb_id=kb_id,
        object_key=object_key,
        status="pending",
        chunk_count=0,
    )

    # 同步触发摄取（骨架）；生产用 MQ
    result = await ingest.ingest_document(doc_id, object_key, _kbs[kb_id]["embedding_alias"])
    _docs[doc_id].status = "ready"                    # type: ignore
    _docs[doc_id].chunk_count = int(result.get("chunks", 0))  # type: ignore

    return _docs[doc_id]


@app.post("/api/v1/kb/{kb_id}/query")
async def query(kb_id: str, req: KBQueryRequest):
    if kb_id not in _kbs:
        raise HTTPException(404, "kb not found")
    return await retriever.retrieve(
        kb_id=kb_id,
        query=req.query,
        top_k=req.top_k,
        score_threshold=req.score_threshold,
        rerank=req.rerank,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().knowledge_port, reload=False)