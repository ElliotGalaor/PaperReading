"""Ingestion via l'API Semantic Scholar (Academic Graph).

Intérêt principal : les embeddings SPECTER2 sont fournis déjà calculés
(champ "embedding.specter_v2"), ce qui évite de faire tourner le modèle.

Les références ne sont pas prises ici : le couplage bibliographique repose sur
les identifiants OpenAlex, récupérés ensuite par `enrich` (via DOI / arXiv).

Doc : https://api.semanticscholar.org/api-docs/graph
Limites : avec clé, 1 requête/s ; sans clé, pool partagé (erreurs 429 fréquentes).
"""

import logging
import time

import httpx

from ..config import S2_EMBEDDING_MODEL, settings
from ..ids import normalize_arxiv_id, normalize_doi
from ..models import Paper

log = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,externalIds,title,abstract,year,venue,authors,fieldsOfStudy,embedding.specter_v2"
SEARCH_PAGE = 100  # maximum par page en recherche
SEARCH_MAX = 1000  # la recherche par pertinence s'arrête à 1000 résultats
BATCH_MAX = 500  # identifiants par appel /paper/batch
MIN_INTERVAL = 1.1  # secondes entre deux requêtes
MAX_RETRIES = 6


def parse_paper(data: dict | None) -> Paper | None:
    if not data or not (data.get("title") or "").strip():
        return None
    ext = data.get("externalIds") or {}
    emb = data.get("embedding") or {}
    vector = emb.get("vector") or None
    return Paper(
        title=data["title"].strip(),
        abstract=data.get("abstract") or None,
        authors=[a["name"] for a in data.get("authors") or [] if a.get("name")],
        year=data.get("year"),
        venue=data.get("venue") or None,
        categories=list(data.get("fieldsOfStudy") or []),
        doi=normalize_doi(ext.get("DOI")),
        arxiv_id=normalize_arxiv_id(ext["ArXiv"]) if ext.get("ArXiv") else None,
        s2_id=data.get("paperId"),
        embedding=vector,
        embedding_model=S2_EMBEDDING_MODEL if vector else None,
        source="semantic_scholar",
    )


class S2Client:
    """Client minimal : clé optionnelle, cadence de 1 requête/s, réessais sur 429/5xx."""

    def __init__(self) -> None:
        headers = {"User-Agent": "papersim/0.1"}
        if settings.s2_api_key:
            headers["x-api-key"] = settings.s2_api_key
        self._http = httpx.Client(base_url=S2_API, timeout=60, headers=headers)
        self._last = 0.0

    def __enter__(self) -> "S2Client":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_RETRIES):
            wait = self._last + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            resp = self._http.request(method, path, **kwargs)
            self._last = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = min(60, 2 ** (attempt + 1))
                log.info("Semantic Scholar %s, nouvel essai dans %ds", resp.status_code, delay)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        if resp.status_code == 429 and not settings.s2_api_key:
            raise RuntimeError(
                "Semantic Scholar refuse les requêtes sans clé (429). Demandez une clé gratuite sur "
                "https://www.semanticscholar.org/product/api#api-key-form puis renseignez "
                "PAPERSIM_S2_API_KEY dans .env."
            )
        raise RuntimeError(f"Semantic Scholar indisponible après {MAX_RETRIES} essais ({resp.status_code})")


def search_s2(
    query: str,
    max_results: int = 100,
    year: str | None = None,
    fields_of_study: str | None = None,
) -> list[Paper]:
    """Recherche par pertinence, ex. year='2018-', fields_of_study='Physics,Mathematics'."""
    max_results = min(max_results, SEARCH_MAX)
    papers: list[Paper] = []
    offset = 0
    with S2Client() as client:
        while offset < max_results:
            limit = min(SEARCH_PAGE, max_results - offset)
            params = {"query": query, "fields": FIELDS, "offset": offset, "limit": limit}
            if year:
                params["year"] = year
            if fields_of_study:
                params["fieldsOfStudy"] = fields_of_study
            data = client.request("GET", "/paper/search", params=params).json()
            batch = data.get("data") or []
            papers.extend(p for p in map(parse_paper, batch) if p)
            if "next" not in data or not batch:
                break
            offset = data["next"]
    return papers


def fetch_s2(ids: list[str]) -> list[Paper | None]:
    """Papiers par identifiant : 'ARXIV:2101.00001', 'DOI:10.1103/...', 'CorpusId:123' ou paperId.

    Renvoie une liste alignée sur `ids` (None pour un identifiant inconnu).
    """
    papers: list[Paper | None] = []
    with S2Client() as client:
        for start in range(0, len(ids), BATCH_MAX):
            chunk = ids[start : start + BATCH_MAX]
            data = client.request(
                "POST", "/paper/batch", params={"fields": FIELDS}, json={"ids": chunk}
            ).json()
            # Un identifiant inconnu renvoie null à sa position.
            papers.extend(map(parse_paper, data))
    return papers
