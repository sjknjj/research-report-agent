"""文本向量化：优先调用真实 embedding API，失败回退本地哈希向量。

支持任意 OpenAI 兼容的 /embeddings 端点（OpenAI、DeepSeek、豆包、通义等）。
配置项：
    EMBED_BASE_URL   缺省复用 LLM_BASE_URL
    EMBED_API_KEY    缺省复用 LLM_API_KEY
    EMBED_MODEL      缺省 text-embedding-3-small（可改为 bge-m3 等）

未配置或调用失败时，自动降级为本地 n-gram 哈希向量，保证 RAG 功能可用。
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


def _hash_vec(text: str) -> np.ndarray:
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    text = text.lower().strip()
    if not text:
        return vec
    for i in range(len(text) - 1):
        h = int(hashlib.md5(text[i:i+2].encode()).hexdigest(), 16)
        idx = h % HASH_DIM
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
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
        self._mode_checked = False
        self._use_api = False

    def _try_api(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        if not self.api_key or not self.base_url:
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
            if not self._mode_checked:
                logger.info("embedding API 不可用（%s），回退本地哈希向量", type(exc).__name__)
                self._mode_checked = True
            return None

    def embed(self, texts: List[str]) -> List[np.ndarray]:
        if isinstance(texts, str):
            texts = [texts]
        vectors = self._try_api(texts)
        if vectors is not None:
            self._use_api = True
            out = []
            for v in vectors:
                n = np.linalg.norm(v)
                out.append(v / n if n > 0 else v)
            return out
        return [_hash_vec(t) for t in texts]

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    @property
    def dim(self) -> int:
        return 1536 if self._use_api else HASH_DIM
