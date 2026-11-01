from pydantic import BaseModel, Field


class Paper(BaseModel):
    """Représentation normalisée d'un papier, quelle que soit sa source."""

    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    categories: list[str] = Field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    # Identifiants OpenAlex des travaux cités (ex. "W2741809807").
    references: list[str] = Field(default_factory=list)
    source: str = "unknown"

    def canonical_id(self) -> str:
        """Identifiant interne stable : arXiv d'abord (domaine physique/maths), puis DOI, puis OpenAlex."""
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.doi:
            return f"doi:{self.doi}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        raise ValueError(f"Papier sans identifiant : {self.title!r}")
