"""Recherche de papiers similaires à partir d'un fichier déposé.

1. Si la première page porte un identifiant arXiv ou un DOI, et que le titre
   trouvé en ligne correspond au fichier : le papier est ajouté au corpus et on
   lance la recherche complète (sémantique + lexical + citations).
2. Sinon : recherche en texte libre sur le titre et l'abstract extraits.

Les sources externes indisponibles ne bloquent pas la recherche : elles
produisent un avertissement renvoyé à l'interface.
"""

import logging

import httpx
import psycopg

from . import db, pipeline, search
from .config import settings
from .extract import Extracted, title_matches
from .ingest import arxiv, openalex
from .models import Paper

log = logging.getLogger(__name__)

EXTERNAL_ERRORS = (httpx.HTTPError, RuntimeError)


def resolve_paper(ex: Extracted, warnings: list[str]) -> Paper | None:
    """Retrouve la notice officielle du document, en vérifiant que le titre correspond."""
    try:
        for aid in ex.arxiv_ids[:2]:
            for paper in arxiv.fetch_arxiv_ids([aid]):
                if title_matches(paper.title, ex.first_page):
                    return paper
        for doi in ex.dois[:2]:
            paper = openalex.get_work_by_doi(doi)
            if paper and title_matches(paper.title, ex.first_page):
                return paper
    except EXTERNAL_ERRORS as e:
        log.warning("Identification du document impossible : %s", e)
        warnings.append("arXiv ou OpenAlex n'a pas répondu : recherche à partir du texte extrait.")
    return None


def _index_paper(conn: psycopg.Connection, paper: Paper, warnings: list[str]) -> str | None:
    ids = pipeline.store_papers(conn, [paper])
    conn.commit()
    if not ids:
        return None
    try:
        pipeline.enrich_from_openalex(conn, ids)
    except EXTERNAL_ERRORS as e:
        conn.rollback()
        log.warning("Enrichissement OpenAlex impossible : %s", e)
        warnings.append("Références OpenAlex indisponibles : pas de signal de citations.")
    try:
        pipeline.compute_embeddings(conn)
    except EXTERNAL_ERRORS as e:
        conn.rollback()
        log.warning("Embeddings indisponibles : %s", e)
        warnings.append("Embeddings indisponibles : pas de signal sémantique.")
    return ids[0]


def _query_embedder(warnings: list[str]):
    if settings.embedding_source != "local":
        warnings.append("Mode Semantic Scholar : la recherche sur texte libre est lexicale seulement.")
        return None
    try:
        from .embeddings import get_embedder

        return get_embedder()
    except RuntimeError as e:
        warnings.append(f"Modèle SPECTER2 indisponible ({e}) : recherche lexicale seulement.")
        return None


def search_document(conn: psycopg.Connection, ex: Extracted, k: int = 10) -> dict:
    warnings: list[str] = []

    paper = resolve_paper(ex, warnings)
    if paper is not None:
        pid = _index_paper(conn, paper, warnings)
        if pid:
            return {
                "mode": "paper",
                "paper": db.get_paper(conn, pid),
                "results": search.find_similar(conn, pid, k=k),
                "warnings": warnings,
            }

    embedder = _query_embedder(warnings)
    return {
        "mode": "text",
        "extracted": {
            "title": ex.title,
            "abstract": ex.abstract,
            "identifiers": ex.arxiv_ids + ex.dois,
        },
        "results": search.search_text(conn, embedder, ex.query_text(), k=k),
        "warnings": warnings,
    }
