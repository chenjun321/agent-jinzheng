import json
from pathlib import Path

import numpy as np
from pymilvus import DataType, MilvusClient

from app.core.config import Settings


class VectorStore:
    collection_name = "doc_chunks"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.local_vector_path = Path(settings.effective_processed_dir) / "local_vectors.json"
        self.client = None
        if settings.effective_vector_backend == "milvus":
            try:
                self.client = MilvusClient(settings.effective_milvus_lite_uri)
                self._ensure_collection()
            except Exception:
                self.client = None

    def _ensure_collection(self) -> None:
        if not self.client:
            return
        if self.client.has_collection(self.collection_name):
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=256)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=256)
        schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
        schema.add_field("page_start", DataType.INT64)
        schema.add_field("page_end", DataType.INT64)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.settings.effective_embedding_dim)
        index_params = self.client.prepare_index_params()
        index_params.add_index("embedding", index_type="FLAT", metric_type="COSINE")
        self.client.create_collection(self.collection_name, schema=schema, index_params=index_params)

    def replace_doc_vectors(self, doc_id: str, rows: list[dict]) -> None:
        self._replace_local_vectors(doc_id, rows)
        if not self.client:
            return
        try:
            self.client.delete(self.collection_name, filter=f'doc_id == "{doc_id}"')
            if rows:
                self.client.insert(self.collection_name, rows)
        except Exception:
            self.client = None

    def delete_doc_vectors(self, doc_id: str) -> None:
        self._replace_local_vectors(doc_id, [])
        if not self.client:
            return
        try:
            self.client.delete(self.collection_name, filter=f'doc_id == "{doc_id}"')
        except Exception:
            self.client = None

    def search(self, doc_id: str, vector: list[float], limit: int = 5) -> list[dict]:
        if self.client:
            try:
                results = self.client.search(
                    collection_name=self.collection_name,
                    data=[vector],
                    anns_field="embedding",
                    limit=limit,
                    filter=f'doc_id == "{doc_id}"',
                    output_fields=["chunk_id", "chunk_type", "page_start", "page_end"],
                )
                hits = []
                for item in results[0]:
                    hits.append(
                        {
                            "chunk_id": item["entity"]["chunk_id"],
                            "score": float(item["distance"]),
                            "chunk_type": item["entity"]["chunk_type"],
                            "page_start": item["entity"]["page_start"],
                            "page_end": item["entity"]["page_end"],
                        }
                    )
                return hits
            except Exception:
                self.client = None
        return self._search_local_vectors(doc_id, vector, limit)

    def _replace_local_vectors(self, doc_id: str, rows: list[dict]) -> None:
        self.local_vector_path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        if self.local_vector_path.exists():
            data = json.loads(self.local_vector_path.read_text(encoding="utf-8"))
        data = [row for row in data if row.get("doc_id") != doc_id]
        data.extend(rows)
        self.local_vector_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _search_local_vectors(self, doc_id: str, vector: list[float], limit: int) -> list[dict]:
        if not self.local_vector_path.exists():
            return []
        rows = json.loads(self.local_vector_path.read_text(encoding="utf-8"))
        query = np.array(vector, dtype=np.float32)
        query_norm = float(np.linalg.norm(query)) or 1.0
        hits = []
        for row in rows:
            if row.get("doc_id") != doc_id:
                continue
            candidate = np.array(row["embedding"], dtype=np.float32)
            denom = query_norm * (float(np.linalg.norm(candidate)) or 1.0)
            score = float(np.dot(query, candidate) / denom)
            hits.append(
                {
                    "chunk_id": row["chunk_id"],
                    "score": score,
                    "chunk_type": row["chunk_type"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        return hits[:limit]
