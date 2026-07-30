"""Application settings, read from environment / .env file."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Azure OpenAI ----
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_chat_deployment: str = "gpt-5.1-chat"
    azure_embedding_deployment: str = "text-embedding-3-large"
    azure_embedding_api_version: str = "2024-12-01-preview"
    embedding_dimensions: int = 3072

    # ---- Paths ----
    database_path: Path = Path("data/books.db")
    # Durable chat history — deliberately a separate file from the rebuildable
    # book cache (books.db), so re-ingesting books never wipes user sessions.
    sessions_database_path: Path = Path("data/sessions.db")
    # Durable ingestion backlog — a dedicated file so its writes never contend
    # with the chunk-store connection, and so it survives a books.db rebuild.
    ingest_queue_database_path: Path = Path("data/ingest_queue.db")
    raw_data_dir: Path = Path("data/raw")

    # ---- Seeding ----
    # On startup, if the library is empty, ingest the bundled sample books from
    # raw_data_dir in the background so the app is usable out of the box. Runs
    # once (skipped when any book already exists); set to false to disable.
    seed_on_start: bool = True

    # ---- Logging ----
    log_level: str = "INFO"

    # ---- Ingestion ----
    embedding_batch_size: int = 64
    chunk_target_tokens: int = 600
    chunk_overlap_fraction: float = 0.15
    summarize_concurrency: int = 4

    # ---- Agent ----
    agent_max_rounds: int = 5
    context_budget_chars: int = 12_000
    # Sampling temperature.  Left unset by default: the provided gpt-5.1-chat
    # deployment only accepts the model default (1) and 400s on any other
    # value, so we omit the parameter entirely unless a deployment that
    # supports it is configured.
    chat_temperature: float | None = None


def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]
