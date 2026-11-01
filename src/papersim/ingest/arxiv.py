"""Ingestion via l'API arXiv (flux Atom).

Doc : https://info.arxiv.org/help/api/user-manual.html
Règle d'usage : au plus une requête toutes les 3 secondes.
"""

import re
import time

import feedparser
import httpx

from ..ids import normalize_arxiv_id, normalize_doi
from ..models import Paper

ARXIV_API = "https://export.arxiv.org/api/query"
PAGE_SIZE = 100
DELAY_SECONDS = 3.0


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_entry(entry) -> Paper:
    if "/api/errors" in entry.get("id", ""):
        raise ValueError(f"Erreur de l'API arXiv : {_clean(entry.get('summary'))}")
    published = entry.get("published") or ""
    return Paper(
        title=_clean(entry.get("title")),
        abstract=_clean(entry.get("summary")) or None,
        authors=[a.get("name", "") for a in entry.get("authors", []) if a.get("name")],
        year=int(published[:4]) if published[:4].isdigit() else None,
        venue=_clean(entry.get("arxiv_journal_ref")) or None,
        categories=[t["term"] for t in entry.get("tags", []) if t.get("term")],
        doi=normalize_doi(entry.get("arxiv_doi")),
        arxiv_id=normalize_arxiv_id(entry["id"]),
        source="arxiv",
    )


def parse_feed(xml: str) -> list[Paper]:
    return [parse_entry(e) for e in feedparser.parse(xml).entries]


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "papersim/0.1"})


def search_arxiv(query: str, max_results: int = 100) -> list[Paper]:
    """Recherche arXiv, ex. 'cat:cond-mat.supr-con AND abs:"pseudogap"'."""
    papers: list[Paper] = []
    with _client() as client:
        while len(papers) < max_results:
            n = min(PAGE_SIZE, max_results - len(papers))
            resp = client.get(
                ARXIV_API,
                params={
                    "search_query": query,
                    "start": len(papers),
                    "max_results": n,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            resp.raise_for_status()
            batch = parse_feed(resp.text)
            papers.extend(batch)
            if len(batch) < n:
                break
            time.sleep(DELAY_SECONDS)
    return papers


def fetch_arxiv_ids(ids: list[str]) -> list[Paper]:
    """Récupère des papiers précis par identifiant arXiv."""
    ids = [normalize_arxiv_id(i) for i in ids]
    papers: list[Paper] = []
    with _client() as client:
        for start in range(0, len(ids), PAGE_SIZE):
            if start:
                time.sleep(DELAY_SECONDS)
            chunk = ids[start : start + PAGE_SIZE]
            resp = client.get(ARXIV_API, params={"id_list": ",".join(chunk), "max_results": len(chunk)})
            resp.raise_for_status()
            papers.extend(parse_feed(resp.text))
    return papers
