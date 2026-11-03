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
    s2_id: str | None = None
    # Identifiants OpenAlex des travaux cités (ex. "W2741809807").
    references: list[str] = Field(default_factory=list)
    # Embedding déjà calculé par la source (Semantic Scholar), sinon None.
    embedding: list[float] | None = Field(default=None, repr=False)
    embedding_model: str | None = None
    source: str = "unknown"

    def canonical_id(self) -> str:
        """Identifiant interne stable : arXiv d'abord (domaine physique/maths), puis DOI, OpenAlex, S2."""
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.doi:
            return f"doi:{self.doi}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        if self.s2_id:
            return f"s2:{self.s2_id}"
        raise ValueError(f"Papier sans identifiant : {self.title!r}")
