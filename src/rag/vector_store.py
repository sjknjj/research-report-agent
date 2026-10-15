import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .embedding import Embedder


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

    def add(self, text: str, source: str = "", metadata: dict = None):
        self.docs.append(Doc(text=text, source=source, metadata=metadata or {}))
        self.vectors.append(self.embedder.embed_one(text))

    def add_many(self, texts: List[str], sources: List[str] = None):
        if not texts:
            return
        sources = sources or [""] * len(texts)
        vecs = self.embedder.embed(texts)
        for t, s, v in zip(texts, sources, vecs):
            self.docs.append(Doc(text=t, source=s))
            self.vectors.append(v)

    def search(self, query: str, top_k: int = 3) -> List[dict]:
        if not self.vectors:
            return []
        qv = self.embedder.embed_one(query)
        sims = [float(np.dot(qv, v)) for v in self.vectors]
        ranked = np.argsort(sims)[::-1][:top_k]
        return [
            {
                "text": self.docs[idx].text[:500],
                "source": self.docs[idx].source,
                "score": round(sims[idx], 3),
                "metadata": self.docs[idx].metadata,
            }
            for idx in ranked
        ]

    def save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
        data = {"docs": [{"text": d.text, "source": d.source,
                          "metadata": d.metadata} for d in self.docs]}
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self):
        try:
            with open(self.persist_path, encoding="utf-8") as f:
                data = json.load(f)
            docs = [Doc(**d) for d in data.get("docs", [])]
            if docs:
                self.docs = docs
                self.vectors = self.embedder.embed([d.text for d in docs])
        except Exception:
            self.docs = []
            self.vectors = []
