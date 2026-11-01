"""Ingestion via l'API OpenAlex : métadonnées, abstracts et références.

Doc : https://docs.openalex.org
"""

import httpx

from ..config import settings
from ..ids import arxiv_id_from_doi, arxiv_id_from_url, normalize_doi, openalex_short_id
from ..models import Paper

OPENALEX_API = "https://api.openalex.org"
PAGE_SIZE = 200  # maximum autorisé par OpenAlex
SELECT = (
    "id,doi,display_name,abstract_inverted_index,publication_year,"
    "authorships,primary_location,locations,referenced_works"
)


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stocke l'abstract en index inversé {mot: [positions]}."""
    if not inverted_index:
        return None
    by_position: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            by_position[pos] = word
    return " ".join(by_position[i] for i in sorted(by_position)) or None


def parse_work(work: dict) -> Paper | None:
    title = (work.get("display_name") or "").strip()
    if not title:
        return None

    doi = normalize_doi(work.get("doi"))
    arxiv_id = arxiv_id_from_doi(doi)
    if not arxiv_id:
        for loc in work.get("locations") or []:
            arxiv_id = arxiv_id_from_url(loc.get("landing_page_url"))
            if arxiv_id:
                break

    source = (work.get("primary_location") or {}).get("source") or {}
    return Paper(
        title=title,
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        authors=[
            a["author"]["display_name"]
            for a in work.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_short_id(work.get("id")),
        references=[r for r in map(openalex_short_id, work.get("referenced_works") or []) if r],
        source="openalex",
    )


def _params(**extra) -> dict:
    params = {"select": SELECT, **extra}
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    return params


def make_client() -> httpx.Client:
    return httpx.Client(base_url=OPENALEX_API, timeout=60, headers={"User-Agent": "papersim/0.1"})


def search_openalex(search: str, max_results: int = 100, filter: str | None = None) -> list[Paper]:
    """Recherche plein texte, ex. search='topological insulator', filter='publication_year:>2018'."""
    papers: list[Paper] = []
    cursor = "*"
    with make_client() as client:
        while cursor and len(papers) < max_results:
            extra = {"search": search, "per-page": PAGE_SIZE, "cursor": cursor}
            if filter:
                extra["filter"] = filter
            resp = client.get("/works", params=_params(**extra))
            resp.raise_for_status()
            data = resp.json()
            papers.extend(p for p in map(parse_work, data["results"]) if p)
            cursor = data["meta"].get("next_cursor")
            if not data["results"]:
                break
    return papers[:max_results]


def get_work_by_doi(doi: str, client: httpx.Client | None = None) -> Paper | None:
    own = client is None
    client = client or make_client()
    try:
        resp = client.get(f"/works/doi:{doi}", params=_params())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return parse_work(resp.json())
    finally:
        if own:
            client.close()
