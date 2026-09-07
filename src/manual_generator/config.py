from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("data"), alias="MANUAL_GENERATOR_DATA_DIR")
    database_url: str | None = Field(default=None, alias="MANUAL_GENERATOR_DATABASE_URL")
    host: str = Field(default="127.0.0.1", alias="MANUAL_GENERATOR_HOST")
    port: int = Field(default=8000, alias="MANUAL_GENERATOR_PORT")
    pdf_enabled: bool = Field(default=False, alias="MANUAL_GENERATOR_PDF_ENABLED")
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_BASE_URL"),
    )
    llm_model: str = Field(
        default="kimi-k2.6",
        validation_alias=AliasChoices("MODEL", "LLM_MODEL", "OPENAI_MODEL"),
    )
    llm_protocol: str = Field(
        default="chat_completions",
        validation_alias=AliasChoices("LLM_PROTOCOL", "OPENAI_PROTOCOL"),
    )
    max_upload_bytes: int = 1024**3
    max_expanded_bytes: int = 5 * 1024**3
    max_zip_files: int = 50_000

    @property
    def sqlite_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'manual-generator.sqlite3'}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "tasks").mkdir(exist_ok=True)
    return settings
