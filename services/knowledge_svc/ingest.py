"""
ingest: 文档摄取管线
- 上传 → 对象存储
- MQ 任务 → LangChain loader 解析
- RecursiveCharacterTextSplitter 分块
- 调 model-svc 拿 embedding
- 写 pgvector
"""
from __future__ import annotations

import os
from typing import Any


async def ingest_document(document_id: str, object_key: str, embedding_alias: str = "embedding-default") -> dict[str, Any]:
    """完整摄取流程（骨架实现）"""
    # 1. 从对象存储下载（生产用 boto3 / minio；骨架跳过）
    # 2. 按文件类型选 loader
    # 3. 分块
    # 4. embedding（经 model-svc）
    # 5. 写 pgvector
    # 6. 更新 kb_document.status = ready / chunk_count
    return {"document_id": document_id, "status": "ready", "chunks": 0}


def chunk_text(text: str, chunk_size: int = 800, chunk_overlap: int = 100) -> list[str]:
    """简化版分块"""
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    return splitter.split_text(text)