from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Nom sous lequel sont rangés les vecteurs SPECTER2 fournis par Semantic Scholar.
# Séparé du modèle local tant que leur compatibilité n'est pas vérifiée.
S2_EMBEDDING_MODEL = "semantic-scholar/specter_v2"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAPERSIM_", extra="ignore")

    database_url: str = "postgresql://papersim:papersim@localhost:5432/papersim"

    openalex_mailto: str | None = None
    openalex_api_key: str | None = None
    s2_api_key: str | None = None

    # Cache des réponses arXiv (1 jour) et verrou partagé entre programmes.
    cache_dir: Path = Path.home() / ".cache" / "papersim"
    # Plafond de résultats par recherche arXiv (arXiv conseille d'affiner au-delà de 1000).
    arxiv_max_results: int = 2000

    # "local" : SPECTER2 calculé sur cette machine (torch requis).
    # "s2"    : vecteurs SPECTER2 téléchargés depuis Semantic Scholar (pas de GPU).
    embedding_source: Literal["local", "s2"] = "local"

    embedding_base: str = "allenai/specter2_base"
    embedding_model: str = "allenai/specter2"
    embedding_dim: int = 768
    embedding_batch_size: int = 16

    @property
    def active_embedding_model(self) -> str:
        """Espace de vecteurs utilisé pour la recherche."""
        return self.embedding_model if self.embedding_source == "local" else S2_EMBEDDING_MODEL


settings = Settings()
