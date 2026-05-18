from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.runtime_config import config_get, load_runtime_config


load_dotenv()


class Settings(BaseSettings):
    config_path: str = "config/default.yaml"
    project_name: str = "Agent Jinzheng"

    dashscope_api_key: str = ""
    dashscope_base_url: str = ""
    dashscope_chat_model: str = ""
    dashscope_embedding_model: str = ""
    dashscope_embedding_dim: int = 0

    sqlite_db_path: str = "data/processed/app.db"
    milvus_lite_uri: str = "data/processed/milvus_jinzheng.db"
    vector_backend: str = ""
    upload_dir: str = "data/input"
    processed_dir: str = "data/processed"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def runtime_config(self) -> dict:
        return load_runtime_config(self.config_path)

    def cfg(self, path: str, default=None):
        return config_get(self.runtime_config, path, default)

    @property
    def upload_path(self) -> Path:
        return Path(self.cfg("paths.upload_dir", self.upload_dir))

    @property
    def processed_path(self) -> Path:
        return Path(self.cfg("paths.processed_dir", self.processed_dir))

    @property
    def effective_sqlite_db_path(self) -> str:
        return self.cfg("paths.sqlite_db_path", self.sqlite_db_path)

    @property
    def effective_milvus_lite_uri(self) -> str:
        return self.cfg("paths.milvus_lite_uri", self.milvus_lite_uri)

    @property
    def effective_vector_backend(self) -> str:
        return self.vector_backend or self.cfg("rag.vector_backend", "milvus")

    @property
    def effective_dashscope_base_url(self) -> str:
        return self.dashscope_base_url or self.cfg("models.base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    @property
    def effective_chat_model(self) -> str:
        return self.dashscope_chat_model or self.cfg("models.chat_model", "qwen-plus")

    @property
    def effective_embedding_model(self) -> str:
        return self.dashscope_embedding_model or self.cfg("models.embedding_model", "text-embedding-v4")

    @property
    def effective_embedding_dim(self) -> int:
        return int(self.dashscope_embedding_dim or self.cfg("models.embedding_dim", 1024))

    @property
    def effective_processed_dir(self) -> str:
        return str(self.processed_path)

    @property
    def effective_upload_dir(self) -> str:
        return str(self.upload_path)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    settings.processed_path.mkdir(parents=True, exist_ok=True)
    return settings
