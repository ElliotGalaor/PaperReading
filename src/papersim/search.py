"""Moteur de comparaison : plusieurs classements fusionnés par Reciprocal Rank Fusion.

Signaux du MVP :
- semantic  : cosinus des embeddings SPECTER2 (aspect "topic")
- lexical   : plein texte PostgreSQL sur titre + abstract
- citations : couplage bibliographique (références communes, normalisé)

RRF fusionne des rangs, pas des scores : pas besoin de calibrer des échelles
différentes. Les poids pourront être réglés une fois le jeu d'évaluation prêt.
"""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import psycopg

from . import db
from .config import settings
from .pipeline import TOPIC

Ranking = list[tuple[str, float]]  # [(paper_id, score brut), ...] trié du meilleur au moins bon


@dataclass(frozen=True)
class Years:
    """Filtre par année de publication, bornes incluses.

    Appliqué dans chaque signal AVANT la sélection des meilleurs candidats : on obtient
    les plus proches dans la période, pas un sous-ensemble du top général.
    Quand le filtre est actif, les papiers sans année sont exclus.
    """

    min: int | None = None
    max: int | None = None

    @property
    def active(self) -> bool:
        return self.min is not None or self.max is not None

    def params(self) -> dict:
        return {"ymin": self.min, "ymax": self.max}


ANY_YEAR = Years()
YEAR_SQL = (
    "(%(ymin)s::int IS NULL OR p.year >= %(ymin)s) "
    "AND (%(ymax)s::int IS NULL OR p.year <= %(ymax)s)"
)


def _allow_filtered_index_scan(conn: psycopg.Connection) -> None:
    """Avec un filtre, l'index HNSW peut renvoyer trop peu de lignes : pgvector >= 0.8
    sait poursuivre le parcours jusqu'à en trouver assez. Sans effet sur les versions anciennes."""
    try:
        with conn.transaction():
            conn.execute("SET hnsw.iterative_scan = strict_order")
    except psycopg.Error:
        pass


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
    aspect: str = TOPIC, model: str | None = None, years: Years = ANY_YEAR,
) -> Ranking:
    if years.active:
        _allow_filtered_index_scan(conn)
    rows = conn.execute(
        f"""
        SELECT e.paper_id, 1 - (e.embedding <=> %(emb)s) AS score
        FROM paper_embeddings e JOIN papers p ON p.id = e.paper_id
        WHERE e.aspect = %(aspect)s AND e.model = %(model)s
          AND (%(exclude)s::text IS NULL OR e.paper_id <> %(exclude)s)
          AND {YEAR_SQL}
        ORDER BY e.embedding <=> %(emb)s
        LIMIT %(k)s
        """,
        {"emb": embedding, "aspect": aspect, "model": model or settings.active_embedding_model,
         "exclude": exclude, "k": k, **years.params()},
    ).fetchall()
    return [(r["paper_id"], float(r["score"])) for r in rows]


def lexical_ranking(
    conn: psycopg.Connection, text: str, *, k: int, exclude: str | None = None,
    years: Years = ANY_YEAR,
) -> Ranking:
    # Requête en OU sur tous les lexèmes du texte : un abstract entier en ET ne renverrait rien.
    rows = conn.execute(
        f"""
        WITH q AS (
            SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
            FROM unnest(to_tsvector('english', %(text)s))
        )
        SELECT p.id, ts_rank(p.tsv, q.query) AS score
        FROM papers p, q
        WHERE q.query IS NOT NULL AND p.tsv @@ q.query
          AND (%(exclude)s::text IS NULL OR p.id <> %(exclude)s)
          AND {YEAR_SQL}
        ORDER BY score DESC
        LIMIT %(k)s
        """,
        {"text": text, "exclude": exclude, "k": k, **years.params()},
    ).fetchall()
    return [(r["id"], float(r["score"])) for r in rows]


def coupling_ranking(
    conn: psycopg.Connection, paper_id: str, *, k: int, years: Years = ANY_YEAR
) -> Ranking:
    """Couplage bibliographique : références communes / sqrt(nA * nB)."""
    rows = conn.execute(
        f"""
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
        JOIN papers p ON p.id = r.paper_id
        WHERE r.paper_id <> %(id)s AND {YEAR_SQL}
        GROUP BY r.paper_id, s.n
        ORDER BY score DESC
        LIMIT %(k)s
        """,
        {"id": paper_id, "k": k, **years.params()},
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
    weights: dict[str, float] | None = None, years: Years = ANY_YEAR,
) -> list[dict]:
    """Papiers similaires à un papier du corpus."""
    paper = db.get_paper(conn, paper_id)
    if paper is None:
        raise KeyError(paper_id)

    signals: dict[str, Ranking] = {}
    emb = db.get_embedding(conn, paper_id, TOPIC, settings.active_embedding_model)
    if emb is not None:
        signals["semantic"] = vector_ranking(conn, emb, k=pool, exclude=paper_id, years=years)
    signals["lexical"] = lexical_ranking(conn, _paper_text(paper), k=pool, exclude=paper_id, years=years)
    signals["citations"] = coupling_ranking(conn, paper_id, k=pool, years=years)
    return _fuse(conn, signals, k, weights)


def _paper_text(paper: dict) -> str:
    return f"{paper['title']} {paper['abstract'] or ''}"


# --- Classement d'un seul signal (avant fusion) ------------------------------------------


def paper_signal(
    conn: psycopg.Connection, paper_id: str, signal: str, *, k: int = 15,
    years: Years = ANY_YEAR,
) -> Ranking:
    """Classement brut d'un signal pour un papier du corpus (mêmes requêtes que find_similar)."""
    paper = db.get_paper(conn, paper_id)
    if paper is None:
        raise KeyError(paper_id)
    if signal == "semantic":
        emb = db.get_embedding(conn, paper_id, TOPIC, settings.active_embedding_model)
        if emb is None:
            raise ValueError("Ce papier n'a pas encore d'embedding (lancez papersim embed).")
        return vector_ranking(conn, emb, k=k, exclude=paper_id, years=years)
    if signal == "lexical":
        return lexical_ranking(conn, _paper_text(paper), k=k, exclude=paper_id, years=years)
    if signal == "citations":
        return coupling_ranking(conn, paper_id, k=k, years=years)
    raise ValueError(f"Signal inconnu : {signal}")


def text_signal(
    conn: psycopg.Connection, embedder, query: str, signal: str, *, k: int = 15,
    years: Years = ANY_YEAR,
) -> Ranking:
    """Classement brut d'un signal pour un texte libre (mêmes requêtes que search_text)."""
    if signal == "lexical":
        return lexical_ranking(conn, query, k=k, years=years)
    if signal == "semantic":
        if embedder is None:
            raise ValueError("Classement sémantique indisponible : aucun modèle local pour encoder le texte.")
        return vector_ranking(conn, embedder.embed_query(query), k=k, years=years)
    if signal == "citations":
        raise ValueError("Le classement par citations n'existe que pour un papier reconnu.")
    raise ValueError(f"Signal inconnu : {signal}")


def ranking_details(conn: psycopg.Connection, ranking: Ranking) -> list[dict]:
    papers = db.get_papers(conn, [pid for pid, _ in ranking])
    return [
        {"rank": i, "score": round(score, 4), "paper": papers[pid]}
        for i, (pid, score) in enumerate(ranking, start=1)
        if pid in papers
    ]


def search_text(
    conn: psycopg.Connection, embedder, query: str, *, k: int = 10, pool: int = 50,
    weights: dict[str, float] | None = None, years: Years = ANY_YEAR,
) -> list[dict]:
    """Recherche par texte libre (une phrase, un problème, un abstract).

    Sans embedder (mode "s2" : pas de modèle local pour encoder la requête),
    seul le signal lexical est utilisé.
    """
    signals = {"lexical": lexical_ranking(conn, query, k=pool, years=years)}
    if embedder is not None:
        signals["semantic"] = vector_ranking(
            conn, embedder.embed_query(query), k=pool, years=years
        )
    return _fuse(conn, signals, k, weights)


def corpus_years(conn: psycopg.Connection) -> dict:
    """Années extrêmes du corpus (bornes proposées par l'interface)."""
    row = conn.execute(
        "SELECT min(year) AS min, max(year) AS max, count(*) AS papers FROM papers"
    ).fetchone()
    return {"min": row["min"], "max": row["max"], "papers": row["papers"]}


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
        (a, b, TOPIC, settings.active_embedding_model),
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
