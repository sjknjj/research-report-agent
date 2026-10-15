"""文本向量化：优先调用真实 embedding API，失败回退本地哈希向量。

支持任意 OpenAI 兼容的 /embeddings 端点（OpenAI、DeepSeek、豆包、通义等）。
配置项：
    EMBED_BASE_URL   缺省复用 LLM_BASE_URL
    EMBED_API_KEY    缺省复用 LLM_API_KEY
    EMBED_MODEL      缺省 text-embedding-3-small（可改为 bge-m3 等）

维度一致性保证：
- 后端在**首次调用时一次性确定**（API 或本地哈希），之后不再来回切换；
- 一旦确定使用 API，中途失败会抛出 :class:`EmbeddingError`，
  绝不退回 512 维哈希向量去和已存的 1536 维向量做点积（那会直接打断流水线）；
- ``dim`` 以真实向量长度为准。
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.request
from typing import List, Optional

import numpy as np

logger = logging.getLogger("rag.embedding")

HASH_DIM = 512


class EmbeddingError(RuntimeError):
    """向量化失败（通常是维度不一致或 API 不可用）。"""


def _hash_vec(text: str) -> np.ndarray:
    """本地 n-gram 哈希向量（确定性、非零、已归一化）。"""
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    text = text.lower().strip()
    if not text:
        # 空文本也要给确定性的非零向量，避免相似度恒为 0
        vec[0] = 1.0
        return vec
    # 单字符输入：退化为字符自身，避免 bigram 循环为空导致零向量
    grams = [text] if len(text) == 1 else [text[i:i + 2] for i in range(len(text) - 1)]
    for gram in grams:
        h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
        idx = h % HASH_DIM
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm == 0:
        # 罕见哈希抵消：给一个确定性非零分量兜底
        vec[int(hashlib.md5(text.encode()).hexdigest(), 16) % HASH_DIM] = 1.0
        norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


class Embedder:
    def __init__(self, base_url: str = "", api_key: str = "", model: str = "",
                 timeout: int = 30):
        import os
        self.base_url = (base_url or os.environ.get("EMBED_BASE_URL")
                         or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = (api_key or os.environ.get("EMBED_API_KEY")
                        or os.environ.get("LLM_API_KEY", ""))
        self.model = (model or os.environ.get("EMBED_MODEL", "")
                      or "text-embedding-3-small")
        self.timeout = timeout
        # 首次调用后后端与维度就固定下来，保证同一 Embedder 维度始终一致
        self._mode_resolved = False
        self._use_api = False
        self._dim: Optional[int] = None

    # -- 后端锁定（供 VectorStore 从持久化索引恢复时调用） ------------------
    def lock_mode(self, use_api: bool, dim: int) -> None:
        """锁定后端与维度，避免从中途切换导致维度不一致。"""
        self._mode_resolved = True
        self._use_api = bool(use_api)
        self._dim = int(dim)

    @property
    def api_ready(self) -> bool:
        """是否具备调用 embedding API 的必要配置。"""
        return bool(self.api_key and self.base_url)

    def _try_api(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        if not self.api_ready:
            return None
        url = self.base_url + "/embeddings"
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            rows = sorted(data["data"], key=lambda x: x["index"])
            return [np.array(r["embedding"], dtype=np.float32) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.info("embedding API 调用失败（%s）", type(exc).__name__)
            return None

    def _normalize(self, vectors: List[np.ndarray]) -> List[np.ndarray]:
        out: List[np.ndarray] = []
        for vector in vectors:
            vector = np.asarray(vector, dtype=np.float32)
            if vector.size == 0:
                raise EmbeddingError("embedding API 返回了空向量")
            if self._dim is None:
                self._dim = int(vector.size)
            elif vector.size != self._dim:
                raise EmbeddingError(
                    f"embedding 维度不一致：期望 {self._dim}，实际 {vector.size}"
                )
            norm = np.linalg.norm(vector)
            out.append(vector / norm if norm > 0 else vector)
        return out

    def _hash_batch(self, texts: List[str]) -> List[np.ndarray]:
        vectors = [_hash_vec(t) for t in texts]
        if vectors and self._dim is None:
            self._dim = int(vectors[0].size)
        return vectors

    def embed(self, texts: List[str]) -> List[np.ndarray]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []

        if not self._mode_resolved:
            # 首次调用：一次性决定后端，之后不再切换，保证维度自洽
            vectors = self._try_api(texts)
            if vectors is not None:
                self._use_api = True
                self._mode_resolved = True
                return self._normalize(vectors)
            self._use_api = False
            self._mode_resolved = True
            logger.info("已固定使用本地哈希向量（维度 %d）", HASH_DIM)
            return self._hash_batch(texts)

        if self._use_api:
            vectors = self._try_api(texts)
            if vectors is None:
                # 已固定为 API 维度：绝不能返回哈希向量（维度不一致会打断整条流水线）
                raise EmbeddingError(
                    "embedding API 已不可用，为保证向量维度一致（"
                    f"{self._dim or '未知'}）拒绝回退到 {HASH_DIM} 维哈希向量；"
                    "请检查 EMBED_BASE_URL / EMBED_API_KEY，或删除索引后重建。"
                )
            return self._normalize(vectors)
        return self._hash_batch(texts)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    @property
    def dim(self) -> int:
        """真实向量维度（未知时按本地哈希维度返回）。"""
        return self._dim if self._dim is not None else HASH_DIM
