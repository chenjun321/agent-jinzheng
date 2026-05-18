import hashlib
from typing import Iterable, List

import numpy as np
from openai import OpenAI

from app.core.config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.dashscope_api_key:
            self.client = OpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.effective_dashscope_base_url,
            )

    def embed_texts(self, texts: Iterable[str]) -> List[list[float]]:
        texts = [text[:6000] for text in texts]
        if not texts:
            return []
        if self.client:
            try:
                resp = self.client.embeddings.create(
                    model=self.settings.effective_embedding_model,
                    input=texts,
                    dimensions=self.settings.effective_embedding_dim,
                    encoding_format="float",
                )
                return [item.embedding for item in resp.data]
            except Exception:
                pass
        return [self._hash_embedding(text) for text in texts]

    def _hash_embedding(self, text: str) -> list[float]:
        dim = self.settings.effective_embedding_dim
        vec = np.zeros(dim, dtype=np.float32)
        normalized = "".join(text.split())
        grams = []
        for n in (1, 2, 3):
            grams.extend(normalized[i : i + n] for i in range(max(0, len(normalized) - n + 1)))
        if not grams:
            grams = [text or "empty"]
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        if norm:
            vec /= norm
        return vec.astype(float).tolist()
