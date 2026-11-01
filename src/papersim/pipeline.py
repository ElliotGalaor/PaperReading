"""Enchaînement ingestion -> stockage -> enrichissement OpenAlex -> embeddings."""

import logging

import psycopg

from . import db
from .ids import arxiv_doi
from .ingest import openalex
from .models import Paper

log = logging.getLogger(__name__)

TOPIC = "topic"


def store_papers(conn: psycopg.Connection, papers: list[Paper]) -> list[str]:
    ids: list[str] = []
    for p in papers:
        if not p.title:
            continue
        try:
            with conn.transaction():  # savepoint : un papier en échec n'annule pas les autres
                ids.append(db.upsert_paper(conn, p))
        except psycopg.errors.UniqueViolation as e:
            log.warning("Conflit d'identifiants, papier ignoré (%s) : %s", p.title[:80], e)
    return ids


def enrich_from_openalex(conn: psycopg.Connection, ids: list[str] | None = None) -> int:
    """Complète les papiers (surtout arXiv) avec leurs références et identifiants OpenAlex."""
    rows = db.papers_without_openalex(conn, ids)
    enriched = 0
    with openalex.make_client() as client:
        for row in rows:
            candidates = [row["doi"], arxiv_doi(row["arxiv_id"]) if row["arxiv_id"] else None]
            work = None
            for doi in filter(None, dict.fromkeys(candidates)):
                work = openalex.get_work_by_doi(doi, client)
                if work:
                    break
            if not work:
                continue
            # Garantit la fusion avec la ligne existante.
            work.arxiv_id = row["arxiv_id"] or work.arxiv_id
            work.doi = row["doi"] or work.doi
            if store_papers(conn, [work]):
                enriched += 1
    conn.commit()
    return enriched


def embed_missing(conn: psycopg.Connection, embedder, batch_size: int = 64) -> int:
    total = 0
    while True:
        rows = db.papers_missing_embedding(conn, TOPIC, embedder.name, batch_size)
        if not rows:
            return total
        vectors = embedder.embed_papers([(r["title"], r["abstract"]) for r in rows])
        db.save_embeddings(conn, [(r["id"], v) for r, v in zip(rows, vectors)], TOPIC, embedder.name)
        conn.commit()
        total += len(rows)
        log.info("%d embeddings calculés", total)


def ingest(
    conn: psycopg.Connection,
    papers: list[Paper],
    *,
    enrich: bool = True,
    embedder=None,
) -> list[str]:
    ids = store_papers(conn, papers)
    conn.commit()
    if enrich and ids:
        enrich_from_openalex(conn, ids)
    if embedder is not None:
        embed_missing(conn, embedder)
    return ids
