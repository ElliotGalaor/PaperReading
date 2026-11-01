from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAPERSIM_", extra="ignore")

    database_url: str = "postgresql://papersim:papersim@localhost:5432/papersim"

    openalex_mailto: str | None = None
    openalex_api_key: str | None = None

    embedding_base: str = "allenai/specter2_base"
    embedding_model: str = "allenai/specter2"
    embedding_dim: int = 768
    embedding_batch_size: int = 16


settings = Settings()
