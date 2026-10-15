"""本地向量索引：内存检索 + JSON 持久化（含向量与 embedding 模型标识）。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .embedding import HASH_DIM, Embedder

logger = logging.getLogger("rag.vector_store")

# 索引结构版本：带向量持久化的新格式
INDEX_VERSION = 2


@dataclass
class Doc:
    text: str
    source: str = ""
    metadata: dict = field(default_factory=dict)


class VectorStore:
    def __init__(self, persist_path: str = "", embedder: Optional[Embedder] = None):
        self.docs: List[Doc] = []
        self.vectors: List[np.ndarray] = []
        self.persist_path = persist_path
        self.embedder = embedder or Embedder()
        if persist_path and os.path.exists(persist_path):
            self.load()

    # -- 写入 ---------------------------------------------------------------
    def add(self, text: str, source: str = "", metadata: dict = None):
        vector = self.embedder.embed_one(text)
        self.docs.append(Doc(text=text, source=source, metadata=metadata or {}))
        self.vectors.append(vector)

    def add_many(
        self,
        texts: List[str],
        sources: List[str] = None,
        metadatas: List[dict] = None,
    ):
        """批量写入；``metadatas`` 逐条保留（修复前 metadata 被整批丢弃）。"""
        if not texts:
            return
        sources = sources or [""] * len(texts)
        if metadatas is None:
            metadatas = [{} for _ in texts]
        elif isinstance(metadatas, dict):
            metadatas = [metadatas for _ in texts]
        vectors = self.embedder.embed(texts)
        for text, source, metadata, vector in zip(texts, sources, metadatas, vectors):
            self.docs.append(Doc(text=text, source=source, metadata=metadata or {}))
            self.vectors.append(vector)

    # -- 检索 ---------------------------------------------------------------
    def search(self, query: str, top_k: int = 3) -> List[dict]:
        if not self.vectors or not self.docs:
            return []
        query_vector = self.embedder.embed_one(query)
        query_dim = query_vector.size
        scored = []
        for idx, vector in enumerate(self.vectors):
            if vector.size != query_dim:
                # 维度不一致（索引由别的 embedding 后端生成）：跳过而不是抛 ValueError
                continue
            scored.append((float(np.dot(query_vector, vector)), idx))
        if not scored:
            logger.warning(
                "索引向量维度（示例 %d）与当前查询维度（%d）不一致，已跳过全部向量",
                self.vectors[0].size if self.vectors else 0, query_dim,
            )
            return []
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "text": self.docs[idx].text[:500],
                "source": self.docs[idx].source,
                "score": round(score, 3),
                "metadata": self.docs[idx].metadata,
            }
            for score, idx in scored[:top_k]
        ]

    # -- 持久化 -------------------------------------------------------------
    def _embedder_meta(self) -> dict:
        return {
            "model": getattr(self.embedder, "model", ""),
            "dim": int(self.embedder.dim),
            "mode": "api" if getattr(self.embedder, "_use_api", False) else "hash",
        }

    def save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
        data = {
            "version": INDEX_VERSION,
            "embedder": self._embedder_meta(),
            "docs": [
                {"text": d.text, "source": d.source, "metadata": d.metadata}
                for d in self.docs
            ],
            # 一并持久化向量，避免每次启动都重算
            "vectors": [np.asarray(v, dtype=np.float32).tolist() for v in self.vectors],
        }
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self):
        try:
            with open(self.persist_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:  # noqa: BLE001 - 索引损坏不能让进程起不来
            logger.warning("向量索引读取失败，已重置为空索引：%s", exc)
            self.docs = []
            self.vectors = []
            return

        try:
            docs = [Doc(**d) for d in data.get("docs", [])]
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量索引 docs 结构异常，已重置为空索引：%s", exc)
            self.docs = []
            self.vectors = []
            return

        self.docs = docs
        if not docs:
            self.vectors = []
            return

        meta = data.get("embedder") or {}
        raw_vectors = data.get("vectors")
        stored = self._parse_vectors(raw_vectors) if isinstance(raw_vectors, list) else None
        if stored and len(stored) == len(docs) and self._can_reuse(stored, meta):
            self.vectors = stored
            logger.info("已从磁盘复用 %d 条向量（后端=%s）", len(stored), meta.get("mode"))
            return

        # 无法复用：明确记录原因并重算
        reason = "缺少持久化向量" if not stored else "embedding 模型/后端不一致"
        logger.info("向量索引需重算（%s），共 %d 条", reason, len(docs))
        try:
            self.vectors = self.embedder.embed([d.text for d in docs])
        except Exception as exc:  # noqa: BLE001
            logger.warning("重算向量失败，已重置为空索引：%s", exc)
            self.docs = []
            self.vectors = []

    @staticmethod
    def _parse_vectors(raw_vectors: list) -> Optional[List[np.ndarray]]:
        try:
            vectors = [np.asarray(v, dtype=np.float32) for v in raw_vectors]
        except (TypeError, ValueError) as exc:
            logger.warning("索引向量反序列化失败：%s", exc)
            return None
        dims = {v.size for v in vectors}
        if len(dims) != 1 or 0 in dims:
            logger.warning("索引向量维度不一致：%s", dims)
            return None
        return vectors

    def _can_reuse(self, vectors: List[np.ndarray], meta: dict) -> bool:
        """判断持久化向量能否在当前 Embedder 下复用（模型 + 后端维度一致）。"""
        stored_dim = int(meta.get("dim") or vectors[0].size)
        stored_mode = meta.get("mode")
        if stored_dim != vectors[0].size:
            return False
        if stored_mode == "api":
            if not self.embedder.api_ready:
                return False
            if meta.get("model") != getattr(self.embedder, "model", ""):
                return False
            self.embedder.lock_mode(use_api=True, dim=stored_dim)
            return True
        if stored_mode == "hash":
            if stored_dim != HASH_DIM:
                return False
            self.embedder.lock_mode(use_api=False, dim=stored_dim)
            return True
        return False
