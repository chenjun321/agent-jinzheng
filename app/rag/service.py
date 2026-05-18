import json
import math
import re
import html as html_lib
from collections import Counter
from difflib import SequenceMatcher

from app.core.config import Settings
from app.core.embedding import EmbeddingClient
from app.rag.vector_store import VectorStore
from app.storage.sqlite_store import SQLiteStore


class RAGService:
    def __init__(self, store: SQLiteStore, embeddings: EmbeddingClient, vector_store: VectorStore, settings: Settings):
        self.store = store
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.settings = settings

    def index_document(self, doc_id: str) -> dict:
        chunks = self.store.get_chunks(doc_id)
        vectors = self.embeddings.embed_texts([self._searchable_text(chunk) for chunk in chunks])
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append(
                {
                    "id": chunk["id"],
                    "doc_id": doc_id,
                    "chunk_id": chunk["id"],
                    "chunk_type": chunk["chunk_type"],
                    "page_start": int(chunk["page_start"]),
                    "page_end": int(chunk["page_end"]),
                    "embedding": vector,
                }
            )
        self.vector_store.replace_doc_vectors(doc_id, rows)
        return {"doc_id": doc_id, "indexed_chunks": len(rows)}

    def retrieve(self, doc_id: str, question: str, top_k: int = 5) -> list[dict]:
        vector_top_k = int(self.settings.cfg("rag.vector_top_k", max(top_k * 2, 8)))
        keyword_top_k = int(self.settings.cfg("rag.keyword_top_k", 20))
        candidate_top_k = int(self.settings.cfg("rag.candidate_top_k", 30))
        final_top_k = top_k or int(self.settings.cfg("rag.final_top_k", 5))
        vector_weight = float(self.settings.cfg("rag.vector_weight", 0.75))
        keyword_weight = float(self.settings.cfg("rag.keyword_weight", 0.35))
        rank_decay_weight = float(self.settings.cfg("rag.rank_decay_weight", 0.05))
        query_vec = self.embeddings.embed_texts([question])[0]
        vector_hits = self.vector_store.search(doc_id, query_vec, limit=vector_top_k)
        chunks = self.store.get_chunks(doc_id)
        keyword_scores = self._keyword_scores(question, chunks)

        score_by_id = {}
        for rank, hit in enumerate(vector_hits):
            score_by_id[hit["chunk_id"]] = score_by_id.get(hit["chunk_id"], 0.0) + hit["score"] * vector_weight + (rank_decay_weight / (rank + 1))
        for chunk_id, score in sorted(keyword_scores.items(), key=lambda item: item[1], reverse=True)[:keyword_top_k]:
            score_by_id[chunk_id] = score_by_id.get(chunk_id, 0.0) + score * keyword_weight

        if self._looks_like_table_question(question):
            table_boost = float(self.settings.cfg("rag.table_boost", 0.15))
            for chunk in chunks:
                if chunk["chunk_type"] == "table":
                    score_by_id[chunk["id"]] = score_by_id.get(chunk["id"], 0.0) + table_boost

        self._apply_clause_boost(question, chunks, score_by_id)

        ranked_ids = [item[0] for item in sorted(score_by_id.items(), key=lambda item: item[1], reverse=True)[:candidate_top_k]]
        chunk_rows = self.store.get_chunks_by_ids(ranked_ids)
        by_score = score_by_id
        evidence = self._build_evidence(question, chunk_rows, by_score)
        evidence = self._deduplicate(evidence)
        evidence = self._rerank(question, evidence)
        return evidence[:final_top_k]

    def _build_evidence(self, question: str, chunk_rows: list[dict], by_score: dict[str, float]) -> list[dict]:
        snippet_chars = int(self.settings.cfg("document.chunking.max_snippet_chars", 700))
        evidence = []
        for chunk in chunk_rows:
            raw_snippet = chunk["text"].strip()
            snippet = raw_snippet if chunk["chunk_type"] == "table" else raw_snippet.replace("\n", " ")
            metadata = json.loads(chunk["metadata_json"] or "{}")
            ocr_confidence = float(metadata.get("ocr_confidence", metadata.get("confidence", 0.75)) or 0.0)
            score = float(by_score.get(chunk["id"], 0.0))
            confidence = max(0.0, min(1.0, (score * 0.75) + (ocr_confidence * 0.25)))
            evidence.append(
                {
                    "chunk_id": chunk["id"],
                    "type": chunk["chunk_type"],
                    "title": chunk["title"],
                    "score": round(score, 4),
                    "confidence": round(confidence, 4),
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "snippet": snippet[:snippet_chars],
                    "metadata": metadata,
                }
            )
        return evidence

    def _apply_clause_boost(self, question: str, chunks: list[dict], score_by_id: dict[str, float]) -> None:
        clause_boost = float(self.settings.cfg("rag.clause_id_boost", 0.12))
        for chunk in chunks:
            metadata = json.loads(chunk["metadata_json"] or "{}")
            clause_id = metadata.get("clause_id")
            if clause_id and str(clause_id) in question:
                score_by_id[chunk["id"]] = score_by_id.get(chunk["id"], 0.0) + clause_boost

    def _deduplicate(self, evidence: list[dict]) -> list[dict]:
        threshold = float(self.settings.cfg("rag.dedup_similarity_threshold", 0.92))
        deduped: list[dict] = []
        for item in evidence:
            duplicate = False
            for kept in deduped:
                same_page = item["page_start"] == kept["page_start"] and item["page_end"] == kept["page_end"]
                similarity = SequenceMatcher(None, item["snippet"], kept["snippet"]).ratio()
                if similarity >= threshold or (same_page and similarity >= 0.86):
                    duplicate = True
                    if item["score"] > kept["score"]:
                        kept.update(item)
                    break
            if not duplicate:
                deduped.append(item)
        return deduped

    def _rerank(self, question: str, evidence: list[dict]) -> list[dict]:
        if not bool(self.settings.cfg("rag.rerank.enabled", True)):
            return sorted(evidence, key=lambda item: item["score"], reverse=True)
        title_boost = float(self.settings.cfg("rag.rerank.title_match_boost", 0.08))
        confidence_weight = float(self.settings.cfg("rag.rerank.ocr_confidence_weight", 0.08))
        q_terms = set(self._terms(question))
        for item in evidence:
            title_terms = set(self._terms(item.get("title") or ""))
            overlap = len(q_terms & title_terms) / max(1, len(q_terms))
            item["rerank_score"] = round(item["score"] + overlap * title_boost + item["confidence"] * confidence_weight, 4)
        return sorted(evidence, key=lambda item: item["rerank_score"], reverse=True)

    def _keyword_scores(self, question: str, chunks: list[dict]) -> dict[str, float]:
        q_terms = self._terms(question)
        if not q_terms:
            return {}
        q_counter = Counter(q_terms)
        scores = {}
        for chunk in chunks:
            terms = self._terms(self._searchable_text(chunk) + " " + (chunk["title"] or ""))
            if not terms:
                continue
            c_counter = Counter(terms)
            overlap = sum(min(q_counter[t], c_counter[t]) for t in q_counter)
            if overlap:
                scores[chunk["id"]] = overlap / math.sqrt(len(terms))
        return scores

    def _terms(self, text: str) -> list[str]:
        cleaned = "".join(ch for ch in text.lower() if not ch.isspace())
        terms = []
        for n in (1, 2, 3):
            terms.extend(cleaned[i : i + n] for i in range(max(0, len(cleaned) - n + 1)))
        return terms

    def _looks_like_table_question(self, question: str) -> bool:
        return any(word in question for word in ["表", "尺寸", "公差", "数值", "多少", "mm", "毫米"])

    def _searchable_text(self, chunk: dict) -> str:
        text = chunk.get("text") or ""
        if chunk.get("chunk_type") != "table":
            return text
        text = re.sub(r"<[^>]+>", " ", text)
        return html_lib.unescape(text)
