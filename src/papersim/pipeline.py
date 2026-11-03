"""Enchaînement ingestion -> stockage -> enrichissement OpenAlex -> embeddings."""

import logging

import numpy as np
import psycopg

from . import db
from .config import S2_EMBEDDING_MODEL, settings
from .ids import arxiv_doi
from .ingest import openalex, semantic_scholar
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
                pid = db.upsert_paper(conn, p)
                if p.embedding and p.embedding_model:
                    vec = np.asarray(p.embedding, dtype=np.float32)
                    db.save_embeddings(conn, [(pid, vec)], TOPIC, p.embedding_model)
                ids.append(pid)
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


def embed_missing_local(conn: psycopg.Connection, embedder, batch_size: int = 64) -> int:
    """Calcule SPECTER2 sur cette machine pour les papiers qui n'ont pas encore d'embedding."""
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


def _s2_lookup_id(row: dict) -> str | None:
    if row["s2_id"]:
        return row["s2_id"]
    if row["arxiv_id"]:
        return f"ARXIV:{row['arxiv_id']}"
    if row["doi"]:
        return f"DOI:{row['doi']}"
    return None


def embed_missing_s2(conn: psycopg.Connection) -> int:
    """Télécharge depuis Semantic Scholar les vecteurs SPECTER2 manquants.

    Un seul passage : un papier inconnu de S2, ou sans vecteur, reste sans embedding
    (on ne boucle pas dessus).
    """
    rows = [r for r in db.papers_missing_embedding(conn, TOPIC, S2_EMBEDDING_MODEL) if _s2_lookup_id(r)]
    found = 0
    for start in range(0, len(rows), semantic_scholar.BATCH_MAX):
        chunk = rows[start : start + semantic_scholar.BATCH_MAX]
        results = semantic_scholar.fetch_s2([_s2_lookup_id(r) for r in chunk])
        to_store = []
        for row, paper in zip(chunk, results):
            if paper is None or not paper.embedding:
                continue
            # Garantit la fusion avec la ligne existante.
            paper.arxiv_id = row["arxiv_id"] or paper.arxiv_id
            paper.doi = row["doi"] or paper.doi
            to_store.append(paper)
        found += len(store_papers(conn, to_store))
        conn.commit()
    missing = len(rows) - found
    if missing:
        log.warning("%d papiers sans vecteur sur Semantic Scholar", missing)
    return found


def compute_embeddings(conn: psycopg.Connection) -> int:
    """Remplit les embeddings manquants selon PAPERSIM_EMBEDDING_SOURCE."""
    if settings.embedding_source == "s2":
        return embed_missing_s2(conn)
    from .embeddings import get_embedder

    return embed_missing_local(conn, get_embedder())


def ingest(
    conn: psycopg.Connection, papers: list[Paper], *, enrich: bool = True, embed: bool = True
) -> list[str]:
    ids = store_papers(conn, papers)
    conn.commit()
    if enrich and ids:
        enrich_from_openalex(conn, ids)
    if embed:
        compute_embeddings(conn)
    return ids
