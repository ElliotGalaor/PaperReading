"""API HTTP (FastAPI).

Les identifiants peuvent contenir des « / » (anciens identifiants arXiv comme
arxiv:hep-th/9901001), d'où le convertisseur {paper_id:path}.
"""

from collections.abc import Iterator
from importlib.resources import files
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, extract, filesearch, pipeline, search
from .config import settings
from .embeddings import get_embedder
from .ids import normalize_doi
from .ingest import arxiv, openalex

app = FastAPI(title="papersim", version="0.1.0")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Scripts partagés par les pages web (ex. /static/authors.js).
app.mount("/static", StaticFiles(directory=str(files("papersim").joinpath("static"))), name="static")


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


class IngestRequest(BaseModel):
    arxiv_ids: list[str] = Field(default_factory=list, max_length=200)
    dois: list[str] = Field(default_factory=list, max_length=200)


class CompareRequest(BaseModel):
    a: str
    b: str


YEAR_BOUNDS = {"ge": 1000, "le": 2100}


class TextSearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=20_000)
    k: int = Field(10, ge=1, le=100)
    year_min: int | None = Field(None, **YEAR_BOUNDS)
    year_max: int | None = Field(None, **YEAR_BOUNDS)


Signal = Literal["semantic", "lexical", "citations"]


class SignalRankingRequest(TextSearchRequest):
    signal: Signal
    k: int = Field(15, ge=1, le=50)


def _years(year_min: int | None, year_max: int | None) -> search.Years:
    if year_min is not None and year_max is not None and year_min > year_max:
        raise HTTPException(422, "L'année de début doit précéder l'année de fin.")
    return search.Years(year_min, year_max)


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
    year_min: int | None = Query(None, **YEAR_BOUNDS),
    year_max: int | None = Query(None, **YEAR_BOUNDS),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    years = _years(year_min, year_max)
    try:
        results = search.find_similar(conn, paper_id, k=k, years=years)
    except KeyError:
        raise HTTPException(404, f"Papier inconnu : {paper_id}")
    return {"query": paper_id, "aspect": aspect, "results": results}


@app.get("/papers/{paper_id:path}/ranking/{signal}")
def paper_ranking(
    paper_id: str,
    signal: Signal,
    k: int = Query(15, ge=1, le=50),
    year_min: int | None = Query(None, **YEAR_BOUNDS),
    year_max: int | None = Query(None, **YEAR_BOUNDS),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Classement d'un seul signal (avant fusion) pour un papier du corpus."""
    years = _years(year_min, year_max)
    try:
        ranking = search.paper_signal(conn, paper_id, signal, k=k, years=years)
    except KeyError:
        raise HTTPException(404, f"Papier inconnu : {paper_id}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {
        "signal": signal,
        "query": {"mode": "paper", "paper": db.get_paper(conn, paper_id)},
        "results": search.ranking_details(conn, ranking),
    }


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
    year_min: int | None = Query(None, **YEAR_BOUNDS),
    year_max: int | None = Query(None, **YEAR_BOUNDS),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    years = _years(year_min, year_max)
    return {"query": q, "results": search.search_text(conn, _embedder(), q, k=k, years=years)}


@app.post("/search/text")
def search_long_text(req: TextSearchRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Comme GET /search, pour un texte long (abstract) : sert à refiltrer sans renvoyer le fichier."""
    years = _years(req.year_min, req.year_max)
    results = search.search_text(conn, _embedder(), req.query, k=req.k, years=years)
    return {"results": results}


@app.post("/search/text/ranking")
def text_ranking(req: SignalRankingRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Classement d'un seul signal (avant fusion) pour un texte libre."""
    years = _years(req.year_min, req.year_max)
    embedder = _embedder() if req.signal == "semantic" else None
    try:
        ranking = search.text_signal(conn, embedder, req.query, req.signal, k=req.k, years=years)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"signal": req.signal, "query": {"mode": "text"}, "results": search.ranking_details(conn, ranking)}


@app.get("/corpus/years")
def corpus_years(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Années extrêmes du corpus et nombre de papiers."""
    return search.corpus_years(conn)


def _embedder():
    return get_embedder() if settings.embedding_source == "local" else None


@app.post("/compare")
def compare(req: CompareRequest, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        return search.compare(conn, req.a, req.b)
    except KeyError as e:
        raise HTTPException(404, f"Papier(s) inconnu(s) : {e.args[0]}")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    """Interface web : glisser-déposer un fichier pour trouver les papiers similaires."""
    return files("papersim").joinpath("static/index.html").read_text(encoding="utf-8")


@app.get("/ranking", response_class=HTMLResponse, include_in_schema=False)
def ranking_page() -> str:
    """Page du classement d'un signal (ouverte depuis les étiquettes des résultats)."""
    return files("papersim").joinpath("static/ranking.html").read_text(encoding="utf-8")


@app.post("/search/file")
def search_file(
    file: UploadFile = File(..., description="PDF, .txt ou .md"),
    k: int = Form(10, ge=1, le=50),
    year_min: int | None = Form(None, **YEAR_BOUNDS),
    year_max: int | None = Form(None, **YEAR_BOUNDS),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Papiers similaires à un fichier déposé (le fichier n'est pas conservé)."""
    years = _years(year_min, year_max)
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Fichier trop volumineux (20 Mo au plus).")
    try:
        doc = extract.extract_document(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return filesearch.search_document(conn, doc, k=k, years=years)
