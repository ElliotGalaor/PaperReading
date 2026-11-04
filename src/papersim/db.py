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

CONNECT_TIMEOUT = 3  # secondes, par adresse essayée : une base injoignable ne doit pas bloquer l'interface

PAPER_COLUMNS = (
    "id, title, abstract, authors, year, venue, categories, doi, arxiv_id, openalex_id, s2_id, source"
)


@contextmanager
def connect(url: str | None = None, *, register: bool = True) -> Iterator[psycopg.Connection]:
    """Connexion avec commit automatique en sortie de bloc (rollback si exception)."""
    with psycopg.connect(
        url or settings.database_url, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT
    ) as conn:
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
        SELECT id, abstract FROM papers
        WHERE arxiv_id = %(a)s OR doi = %(d)s OR openalex_id = %(o)s OR s2_id = %(s)s
        LIMIT 1
        """,
        {"a": p.arxiv_id, "d": p.doi, "o": p.openalex_id, "s": p.s2_id},
    ).fetchone()


def upsert_paper(conn: psycopg.Connection, p: Paper) -> str:
    """Insère ou fusionne un papier ; renvoie son identifiant interne."""
    existing = _find_existing(conn, p)
    pid = existing["id"] if existing else p.canonical_id()

    conn.execute(
        """
        INSERT INTO papers (id, title, abstract, authors, year, venue, categories,
                            doi, arxiv_id, openalex_id, s2_id, source)
        VALUES (%(id)s, %(title)s, %(abstract)s, %(authors)s, %(year)s, %(venue)s, %(categories)s,
                %(doi)s, %(arxiv_id)s, %(openalex_id)s, %(s2_id)s, %(source)s)
        ON CONFLICT (id) DO UPDATE SET
            -- Le texte déjà connu est conservé (il a servi aux embeddings) ;
            -- un abstract manquant est complété.
            abstract    = COALESCE(papers.abstract, EXCLUDED.abstract),
            authors     = CASE WHEN jsonb_array_length(EXCLUDED.authors) > 0
                               THEN EXCLUDED.authors ELSE papers.authors END,
            year        = COALESCE(EXCLUDED.year, papers.year),
            venue       = COALESCE(EXCLUDED.venue, papers.venue),
            categories  = CASE WHEN cardinality(EXCLUDED.categories) > 0
                               THEN EXCLUDED.categories ELSE papers.categories END,
            doi         = COALESCE(papers.doi, EXCLUDED.doi),
            arxiv_id    = COALESCE(papers.arxiv_id, EXCLUDED.arxiv_id),
            openalex_id = COALESCE(papers.openalex_id, EXCLUDED.openalex_id),
            s2_id       = COALESCE(papers.s2_id, EXCLUDED.s2_id),
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
            "s2_id": p.s2_id,
            "source": p.source,
        },
    )

    # Abstract ajouté à un papier qui n'en avait pas : l'embedding (titre seul) est à refaire.
    if existing and existing["abstract"] is None and p.abstract:
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
    conn: psycopg.Connection, aspect: str, model: str, limit: int | None = None
) -> list[dict]:
    return conn.execute(
        """
        SELECT p.id, p.title, p.abstract, p.doi, p.arxiv_id, p.s2_id FROM papers p
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


# --- Favoris (par compte) ---------------------------------------------------------------


def add_favorite(conn: psycopg.Connection, user_id: int, paper_id: str) -> None:
    """Ajoute un favori (sans effet s'il existe déjà) ; KeyError si le papier est inconnu."""
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO user_favorites (user_id, paper_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, paper_id),
            )
    except psycopg.errors.ForeignKeyViolation:
        raise KeyError(paper_id) from None


def remove_favorite(conn: psycopg.Connection, user_id: int, paper_id: str) -> bool:
    cur = conn.execute(
        "DELETE FROM user_favorites WHERE user_id = %s AND paper_id = %s", (user_id, paper_id)
    )
    return cur.rowcount > 0


def list_favorites(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """Favoris d'un compte, du plus récent au plus ancien, avec la fiche du papier."""
    columns = ", ".join(f"p.{c.strip()}" for c in PAPER_COLUMNS.split(","))
    rows = conn.execute(
        f"""
        SELECT {columns}, f.created_at AS favorited_at
        FROM user_favorites f JOIN papers p ON p.id = f.paper_id
        WHERE f.user_id = %s
        ORDER BY f.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [{"paper": {k: v for k, v in r.items() if k != "favorited_at"}, "added_at": r["favorited_at"]} for r in rows]


def adopt_legacy_favorites(conn: psycopg.Connection, user_id: int) -> int:
    """Donne au compte les favoris enregistrés avant l'arrivée des comptes, puis supprime
    l'ancienne table. Renvoie le nombre de favoris repris (0 s'il n'y a rien à reprendre)."""
    if conn.execute("SELECT to_regclass('favorites') AS t").fetchone()["t"] is None:
        return 0
    cur = conn.execute(
        """
        INSERT INTO user_favorites (user_id, paper_id, created_at)
        SELECT %s, paper_id, created_at FROM favorites
        ON CONFLICT DO NOTHING
        """,
        (user_id,),
    )
    conn.execute("DROP TABLE favorites")
    return cur.rowcount


# --- Bibliothèque : parcours du corpus ------------------------------------------------------

# Tris autorisés (jamais de texte venant de l'utilisateur dans le ORDER BY).
LIBRARY_SORTS = {
    "recent": "p.year DESC NULLS LAST, p.created_at DESC, p.id",
    "oldest": "p.year ASC NULLS LAST, p.created_at ASC, p.id",
    # Titre sans les caractères LaTeX ($, \, {, }) : « $d$-wave » se range à « d ».
    "title": r"lower(regexp_replace(p.title, '[$\\{}]', '', 'g')) ASC, p.id",
    "added": "p.created_at DESC, p.id",
}


def list_papers(
    conn: psycopg.Connection, *, query: str | None = None, category: str | None = None,
    sort: str = "recent", limit: int = 25, offset: int = 0,
) -> tuple[int, list[dict]]:
    """Une page du corpus, filtrée et triée ; renvoie (nombre total, papiers de la page).

    `query` cherche dans le titre et le résumé (plein texte, tolère les pluriels) ainsi
    que dans les noms d'auteurs (sous-chaîne, insensible à la casse).
    """
    order = LIBRARY_SORTS.get(sort)
    if order is None:
        raise ValueError(f"Tri inconnu : {sort}")
    columns = ", ".join(f"p.{c.strip()}" for c in PAPER_COLUMNS.split(","))
    q = (query or "").strip() or None
    rows = conn.execute(
        f"""
        SELECT {columns}, count(*) OVER () AS total
        FROM papers p
        WHERE (%(q)s::text IS NULL
               OR p.tsv @@ websearch_to_tsquery('english', %(q)s)
               OR p.title ILIKE '%%' || %(q)s || '%%'
               OR p.authors::text ILIKE '%%' || %(q)s || '%%')
          AND (%(cat)s::text IS NULL OR %(cat)s = ANY (p.categories))
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"q": q, "cat": category or None, "limit": limit, "offset": offset},
    ).fetchall()
    total = rows[0]["total"] if rows else _count_papers(conn, q, category)
    return total, [{k: v for k, v in r.items() if k != "total"} for r in rows]


def _count_papers(conn: psycopg.Connection, q: str | None, category: str | None) -> int:
    """Total quand la page demandée est vide (ex. page au-delà de la dernière)."""
    row = conn.execute(
        """
        SELECT count(*) AS n FROM papers p
        WHERE (%(q)s::text IS NULL
               OR p.tsv @@ websearch_to_tsquery('english', %(q)s)
               OR p.title ILIKE '%%' || %(q)s || '%%'
               OR p.authors::text ILIKE '%%' || %(q)s || '%%')
          AND (%(cat)s::text IS NULL OR %(cat)s = ANY (p.categories))
        """,
        {"q": q, "cat": category or None},
    ).fetchone()
    return row["n"]


def corpus_categories(conn: psycopg.Connection, limit: int = 60) -> list[dict]:
    """Catégories présentes dans le corpus, de la plus à la moins fréquente."""
    rows = conn.execute(
        """
        SELECT c AS code, count(*) AS n
        FROM papers, unnest(categories) AS c
        GROUP BY c ORDER BY n DESC, c
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [{"code": r["code"], "count": r["n"]} for r in rows]
