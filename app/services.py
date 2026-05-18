from functools import lru_cache

from app.agent.workflow import DocumentQAAgent
from app.core.config import get_settings
from app.core.embedding import EmbeddingClient
from app.core.llm import LLMClient
from app.document.parser import DocumentParser
from app.rag.service import RAGService
from app.rag.vector_store import VectorStore
from app.storage.sqlite_store import SQLiteStore


@lru_cache
def get_store() -> SQLiteStore:
    return SQLiteStore(get_settings().effective_sqlite_db_path)


@lru_cache
def get_parser() -> DocumentParser:
    settings = get_settings()
    return DocumentParser(get_store(), settings)


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(get_settings())


@lru_cache
def get_vector_store() -> VectorStore:
    return VectorStore(get_settings())


@lru_cache
def get_rag_service() -> RAGService:
    return RAGService(get_store(), get_embedding_client(), get_vector_store(), get_settings())


@lru_cache
def get_agent() -> DocumentQAAgent:
    return DocumentQAAgent(get_store(), get_rag_service(), LLMClient(get_settings()))
