"""API HTTP (FastAPI).

Les identifiants peuvent contenir des « / » (anciens identifiants arXiv comme
arxiv:hep-th/9901001), d'où le convertisseur {paper_id:path}.
"""

from collections.abc import Iterator
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import db, pipeline, search
from .config import settings
from .embeddings import get_embedder
from .ids import normalize_doi
from .ingest import arxiv, openalex

app = FastAPI(title="papersim", version="0.1.0")


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


class IngestRequest(BaseModel):
    arxiv_ids: list[str] = Field(default_factory=list, max_length=200)
    dois: list[str] = Field(default_factory=list, max_length=200)


class CompareRequest(BaseModel):
    a: str
    b: str


@app.post("/papers")
def add_papers(req: IngestRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Ajoute des papiers par identifiant arXiv ou DOI, puis les indexe.

    Synchrone pour le MVP. À passer en tâche de fond (RQ/Celery) avec l'upload PDF.
    """
    papers = arxiv.fetch_arxiv_ids(req.arxiv_ids) if req.arxiv_ids else []
    with openalex.make_client() as client:
        for doi in filter(None, map(normalize_doi, req.dois)):
            work = openalex.get_work_by_doi(doi, client)
            if work:
                papers.append(work)
    ids = pipeline.ingest(conn, papers)
    return {"ingested": ids}


@app.get("/papers/{paper_id:path}/similar")
def similar(
    paper_id: str,
    k: int = Query(10, ge=1, le=100),
    aspect: Literal["topic"] = "topic",  # 'problem' | 'method' arriveront à l'étape 3
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    try:
        results = search.find_similar(conn, paper_id, k=k)
    except KeyError:
        raise HTTPException(404, f"Papier inconnu : {paper_id}")
    return {"query": paper_id, "aspect": aspect, "results": results}


@app.get("/papers/{paper_id:path}")
def get_paper(paper_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    paper = db.get_paper(conn, paper_id)
    if paper is None:
        raise HTTPException(404, f"Papier inconnu : {paper_id}")
    return paper


@app.get("/search")
def search_text(
    q: str = Query(..., min_length=3),
    k: int = Query(10, ge=1, le=100),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    embedder = get_embedder() if settings.embedding_source == "local" else None
    return {"query": q, "results": search.search_text(conn, embedder, q, k=k)}


@app.post("/compare")
def compare(req: CompareRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        return search.compare(conn, req.a, req.b)
    except KeyError as e:
        raise HTTPException(404, f"Papier(s) inconnu(s) : {e.args[0]}")
