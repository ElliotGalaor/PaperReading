"""Moteur de comparaison : plusieurs classements fusionnés par Reciprocal Rank Fusion.

Signaux du MVP :
- semantic  : cosinus des embeddings SPECTER2 (aspect "topic")
- lexical   : plein texte PostgreSQL sur titre + abstract
- citations : couplage bibliographique (références communes, normalisé)

RRF fusionne des rangs, pas des scores : pas besoin de calibrer des échelles
différentes. Les poids pourront être réglés une fois le jeu d'évaluation prêt.
"""

from collections import defaultdict

import numpy as np
import psycopg

from . import db
from .config import settings
from .pipeline import TOPIC

Ranking = list[tuple[str, float]]  # [(paper_id, score brut), ...] trié du meilleur au moins bon


def rrf(
    rankings: dict[str, list[str]], k: int = 60, weights: dict[str, float] | None = None
) -> list[tuple[str, float]]:
    """score(d) = somme sur les signaux de poids / (k + rang de d)."""
    scores: dict[str, float] = defaultdict(float)
    for name, ids in rankings.items():
        w = (weights or {}).get(name, 1.0)
        for rank, pid in enumerate(ids, start=1):
            scores[pid] += w / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


# --- Signaux -------------------------------------------------------------------


def vector_ranking(
    conn: psycopg.Connection, embedding: np.ndarray, *, k: int, exclude: str | None = None,
    aspect: str = TOPIC, model: str | None = None,
) -> Ranking:
    rows = conn.execute(
        """
        SELECT paper_id, 1 - (embedding <=> %(emb)s) AS score
        FROM paper_embeddings
        WHERE aspect = %(aspect)s AND model = %(model)s
          AND (%(exclude)s::text IS NULL OR paper_id <> %(exclude)s)
        ORDER BY embedding <=> %(emb)s
        LIMIT %(k)s
        """,
        {"emb": embedding, "aspect": aspect, "model": model or settings.embedding_model,
         "exclude": exclude, "k": k},
    ).fetchall()
    return [(r["paper_id"], float(r["score"])) for r in rows]


def lexical_ranking(
    conn: psycopg.Connection, text: str, *, k: int, exclude: str | None = None
) -> Ranking:
    # Requête en OU sur tous les lexèmes du texte : un abstract entier en ET ne renverrait rien.
    rows = conn.execute(
        """
        WITH q AS (
            SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
            FROM unnest(to_tsvector('english', %(text)s))
        )
        SELECT p.id, ts_rank(p.tsv, q.query) AS score
        FROM papers p, q
        WHERE q.query IS NOT NULL AND p.tsv @@ q.query
          AND (%(exclude)s::text IS NULL OR p.id <> %(exclude)s)
        ORDER BY score DESC
        LIMIT %(k)s
        """,
        {"text": text, "exclude": exclude, "k": k},
    ).fetchall()
    return [(r["id"], float(r["score"])) for r in rows]


def coupling_ranking(conn: psycopg.Connection, paper_id: str, *, k: int) -> Ranking:
    """Couplage bibliographique : références communes / sqrt(nA * nB)."""
    rows = conn.execute(
        """
        WITH mine AS (
            SELECT ref_openalex_id FROM paper_references WHERE paper_id = %(id)s
        ),
        sizes AS (
            SELECT paper_id, count(*) AS n FROM paper_references GROUP BY paper_id
        )
        SELECT r.paper_id AS id,
               count(*)::float / sqrt((SELECT count(*) FROM mine) * s.n) AS score
        FROM paper_references r
        JOIN mine USING (ref_openalex_id)
        JOIN sizes s ON s.paper_id = r.paper_id
        WHERE r.paper_id <> %(id)s
        GROUP BY r.paper_id, s.n
        ORDER BY score DESC
        LIMIT %(k)s
        """,
        {"id": paper_id, "k": k},
    ).fetchall()
    return [(r["id"], float(r["score"])) for r in rows]


# --- Requêtes de haut niveau ------------------------------------------------------


def _fuse(
    conn: psycopg.Connection, signals: dict[str, Ranking], k: int,
    weights: dict[str, float] | None,
) -> list[dict]:
    fused = rrf({name: [pid for pid, _ in r] for name, r in signals.items()}, weights=weights)[:k]
    details = {
        name: {pid: {"rank": i, "score": round(s, 4)} for i, (pid, s) in enumerate(r, start=1)}
        for name, r in signals.items()
    }
    papers = db.get_papers(conn, [pid for pid, _ in fused])
    return [
        {
            "paper": papers[pid],
            "score": round(score, 5),
            # Pour chaque signal : rang et score brut. Sert d'explication de base.
            "signals": {name: d[pid] for name, d in details.items() if pid in d},
        }
        for pid, score in fused
        if pid in papers
    ]


def find_similar(
    conn: psycopg.Connection, paper_id: str, *, k: int = 10, pool: int = 50,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """Papiers similaires à un papier du corpus."""
    paper = db.get_paper(conn, paper_id)
    if paper is None:
        raise KeyError(paper_id)

    signals: dict[str, Ranking] = {}
    emb = db.get_embedding(conn, paper_id, TOPIC, settings.embedding_model)
    if emb is not None:
        signals["semantic"] = vector_ranking(conn, emb, k=pool, exclude=paper_id)
    text = f"{paper['title']} {paper['abstract'] or ''}"
    signals["lexical"] = lexical_ranking(conn, text, k=pool, exclude=paper_id)
    signals["citations"] = coupling_ranking(conn, paper_id, k=pool)
    return _fuse(conn, signals, k, weights)


def search_text(
    conn: psycopg.Connection, embedder, query: str, *, k: int = 10, pool: int = 50,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """Recherche par texte libre (une phrase, un problème, un abstract)."""
    signals = {
        "semantic": vector_ranking(conn, embedder.embed_query(query), k=pool),
        "lexical": lexical_ranking(conn, query, k=pool),
    }
    return _fuse(conn, signals, k, weights)


def compare(conn: psycopg.Connection, a: str, b: str) -> dict:
    """Comparaison côte à côte de deux papiers."""
    papers = db.get_papers(conn, [a, b])
    missing = [pid for pid in (a, b) if pid not in papers]
    if missing:
        raise KeyError(", ".join(missing))

    cos = conn.execute(
        """
        SELECT 1 - (ea.embedding <=> eb.embedding) AS cosine
        FROM paper_embeddings ea JOIN paper_embeddings eb
          ON ea.aspect = eb.aspect AND ea.model = eb.model
        WHERE ea.paper_id = %s AND eb.paper_id = %s AND ea.aspect = %s AND ea.model = %s
        """,
        (a, b, TOPIC, settings.embedding_model),
    ).fetchone()
    shared = conn.execute(
        """
        SELECT ra.ref_openalex_id FROM paper_references ra
        JOIN paper_references rb USING (ref_openalex_id)
        WHERE ra.paper_id = %s AND rb.paper_id = %s
        """,
        (a, b),
    ).fetchall()
    n_a, n_b = db.count_references(conn, a), db.count_references(conn, b)
    n_shared = len(shared)

    return {
        "a": papers[a],
        "b": papers[b],
        "semantic_cosine": round(float(cos["cosine"]), 4) if cos else None,
        "shared_references": [r["ref_openalex_id"] for r in shared],
        "coupling": round(n_shared / (n_a * n_b) ** 0.5, 4) if n_a and n_b else None,
        "shared_categories": sorted(set(papers[a]["categories"]) & set(papers[b]["categories"])),
    }
