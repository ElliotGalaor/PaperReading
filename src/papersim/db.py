"""Accès PostgreSQL + pgvector."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings
from .models import Paper

PAPER_COLUMNS = "id, title, abstract, authors, year, venue, categories, doi, arxiv_id, openalex_id, source"


@contextmanager
def connect(url: str | None = None, *, register: bool = True) -> Iterator[psycopg.Connection]:
    """Connexion avec commit automatique en sortie de bloc (rollback si exception)."""
    with psycopg.connect(url or settings.database_url, row_factory=dict_row) as conn:
        if register:
            register_vector(conn)
        yield conn


def init_db() -> None:
    sql = files("papersim").joinpath("schema.sql").read_text(encoding="utf-8")
    # register=False : l'extension vector n'existe peut-être pas encore.
    with connect(register=False) as conn:
        conn.execute(sql.replace("{dim}", str(settings.embedding_dim)))


# --- Papiers -----------------------------------------------------------------


def _find_existing(conn: psycopg.Connection, p: Paper) -> dict | None:
    """Déduplication : un papier déjà connu par l'un de ses identifiants."""
    return conn.execute(
        """
        SELECT id, title, abstract FROM papers
        WHERE arxiv_id = %(a)s OR doi = %(d)s OR openalex_id = %(o)s
        LIMIT 1
        """,
        {"a": p.arxiv_id, "d": p.doi, "o": p.openalex_id},
    ).fetchone()


def upsert_paper(conn: psycopg.Connection, p: Paper) -> str:
    """Insère ou fusionne un papier ; renvoie son identifiant interne."""
    existing = _find_existing(conn, p)
    pid = existing["id"] if existing else p.canonical_id()

    conn.execute(
        """
        INSERT INTO papers (id, title, abstract, authors, year, venue, categories,
                            doi, arxiv_id, openalex_id, source)
        VALUES (%(id)s, %(title)s, %(abstract)s, %(authors)s, %(year)s, %(venue)s, %(categories)s,
                %(doi)s, %(arxiv_id)s, %(openalex_id)s, %(source)s)
        ON CONFLICT (id) DO UPDATE SET
            title       = COALESCE(NULLIF(EXCLUDED.title, ''), papers.title),
            abstract    = COALESCE(EXCLUDED.abstract, papers.abstract),
            authors     = CASE WHEN jsonb_array_length(EXCLUDED.authors) > 0
                               THEN EXCLUDED.authors ELSE papers.authors END,
            year        = COALESCE(EXCLUDED.year, papers.year),
            venue       = COALESCE(EXCLUDED.venue, papers.venue),
            categories  = CASE WHEN cardinality(EXCLUDED.categories) > 0
                               THEN EXCLUDED.categories ELSE papers.categories END,
            doi         = COALESCE(papers.doi, EXCLUDED.doi),
            arxiv_id    = COALESCE(papers.arxiv_id, EXCLUDED.arxiv_id),
            openalex_id = COALESCE(papers.openalex_id, EXCLUDED.openalex_id),
            updated_at  = now()
        """,
        {
            "id": pid,
            "title": p.title,
            "abstract": p.abstract,
            "authors": Jsonb(p.authors),
            "year": p.year,
            "venue": p.venue,
            "categories": p.categories,
            "doi": p.doi,
            "arxiv_id": p.arxiv_id,
            "openalex_id": p.openalex_id,
            "source": p.source,
        },
    )

    # Le texte a changé : les embeddings existants ne sont plus valables.
    if existing and (
        (p.title and p.title != existing["title"])
        or (p.abstract and p.abstract != existing["abstract"])
    ):
        conn.execute("DELETE FROM paper_embeddings WHERE paper_id = %s", (pid,))

    if p.references:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO paper_references (paper_id, ref_openalex_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                [(pid, ref) for ref in p.references],
            )
    return pid


def get_paper(conn: psycopg.Connection, paper_id: str) -> dict | None:
    return conn.execute(f"SELECT {PAPER_COLUMNS} FROM papers WHERE id = %s", (paper_id,)).fetchone()


def get_papers(conn: psycopg.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = conn.execute(f"SELECT {PAPER_COLUMNS} FROM papers WHERE id = ANY(%s)", (ids,)).fetchall()
    return {r["id"]: r for r in rows}


def papers_without_openalex(
    conn: psycopg.Connection, ids: list[str] | None = None, limit: int | None = None
) -> list[dict]:
    return conn.execute(
        """
        SELECT id, doi, arxiv_id FROM papers
        WHERE openalex_id IS NULL AND (%(ids)s::text[] IS NULL OR id = ANY(%(ids)s))
        ORDER BY created_at
        LIMIT %(limit)s
        """,
        {"ids": ids, "limit": limit},
    ).fetchall()


def count_references(conn: psycopg.Connection, paper_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM paper_references WHERE paper_id = %s", (paper_id,)
    ).fetchone()
    return row["n"]


# --- Embeddings --------------------------------------------------------------


def papers_missing_embedding(
    conn: psycopg.Connection, aspect: str, model: str, limit: int
) -> list[dict]:
    return conn.execute(
        """
        SELECT p.id, p.title, p.abstract FROM papers p
        WHERE NOT EXISTS (
            SELECT 1 FROM paper_embeddings e
            WHERE e.paper_id = p.id AND e.aspect = %(aspect)s AND e.model = %(model)s
        )
        ORDER BY p.created_at
        LIMIT %(limit)s
        """,
        {"aspect": aspect, "model": model, "limit": limit},
    ).fetchall()


def save_embeddings(
    conn: psycopg.Connection, rows: list[tuple[str, np.ndarray]], aspect: str, model: str
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO paper_embeddings (paper_id, aspect, model, embedding)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (paper_id, aspect, model) DO UPDATE SET
                embedding = EXCLUDED.embedding, created_at = now()
            """,
            [(pid, aspect, model, vec) for pid, vec in rows],
        )


def get_embedding(
    conn: psycopg.Connection, paper_id: str, aspect: str, model: str
) -> np.ndarray | None:
    row = conn.execute(
        "SELECT embedding FROM paper_embeddings WHERE paper_id = %s AND aspect = %s AND model = %s",
        (paper_id, aspect, model),
    ).fetchone()
    return row["embedding"] if row else None
