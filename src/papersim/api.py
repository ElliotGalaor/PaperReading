"""API HTTP (FastAPI).

Les identifiants peuvent contenir des « / » (anciens identifiants arXiv comme
arxiv:hep-th/9901001), d'où le convertisseur {paper_id:path}.
"""

import logging
import math
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, extract, filesearch, keywords, pipeline, search
from .config import settings
from .embeddings import get_embedder
from .ids import normalize_doi
from .ingest import arxiv, openalex

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Met le schéma à jour au démarrage (instructions idempotentes : nouvelles tables
    # comme « favorites » créées sans relancer papersim init-db). Base injoignable :
    # le serveur démarre quand même, les pages afficheront l'erreur.
    try:
        db.init_db()
    except Exception as e:  # noqa: BLE001
        log.warning("Schéma non vérifié au démarrage (base injoignable ?) : %s", e)
    yield


app = FastAPI(title="papersim", version="0.1.0", lifespan=lifespan)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

class RevalidatedStaticFiles(StaticFiles):
    """Fichiers statiques que le navigateur doit revérifier à chaque chargement
    (« no-cache » : il garde sa copie mais demande si elle a changé ; réponse 304 sinon).
    Sans cela, il peut réutiliser un ancien script avec une page plus récente."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Scripts partagés par les pages web (ex. /static/authors.js).
app.mount("/static", RevalidatedStaticFiles(directory=str(files("papersim").joinpath("static"))), name="static")


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


# --- Comptes et sessions ------------------------------------------------------------------
# Bibliothèque et recherche sont ouvertes à tous ; seuls les favoris demandent un compte.

SESSION_COOKIE = "papersim_session"
login_throttle = auth.LoginThrottle()


def optional_user(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> dict | None:
    return auth.user_from_session(conn, request.cookies.get(SESSION_COOKIE))


def current_user(user: dict | None = Depends(optional_user)) -> dict:
    if user is None:
        raise HTTPException(401, "Connectez-vous pour utiliser les favoris.")
    return user


class Credentials(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=1024)


def _open_session(response: Response, conn: psycopg.Connection, user: dict) -> dict:
    token = auth.create_session(conn, user["id"], settings.session_days)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.session_days * 86400,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )
    return {"user": {"username": user["username"]}}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """Le serveur répond (sans interroger la base) : sonde de santé de l'image Docker."""
    return {"status": "ok"}


@app.get("/auth/me")
def whoami(user: dict | None = Depends(optional_user)) -> dict:
    """Compte connecté (ou null) et possibilité de s'inscrire."""
    return {
        "user": {"username": user["username"]} if user else None,
        "allow_signup": settings.allow_signup,
    }


@app.post("/auth/login")
def login(creds: Credentials, request: Request, response: Response,
          conn: psycopg.Connection = Depends(get_conn)) -> dict:
    ip = _client_ip(request)
    wait = login_throttle.wait_time(ip, creds.username)
    if wait > 0:
        raise HTTPException(429, f"Trop d'essais ratés. Réessayez dans {math.ceil(wait)} s.")
    user = auth.authenticate(conn, creds.username, creds.password)
    if user is None:
        login_throttle.failure(ip, creds.username)
        raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
    login_throttle.success(ip, creds.username)
    return _open_session(response, conn, user)


@app.post("/auth/signup")
def signup(creds: Credentials, response: Response, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not settings.allow_signup:
        raise HTTPException(403, "Les inscriptions sont fermées. Demandez un compte à l'administrateur.")
    try:
        user = auth.create_user(conn, creds.username, creds.password)
    except ValueError as e:
        raise HTTPException(409 if "déjà pris" in str(e) else 422, str(e))
    return _open_session(response, conn, user)


@app.post("/auth/logout")
def logout(request: Request, response: Response, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    auth.delete_session(conn, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"user": None}


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page() -> str:
    return files("papersim").joinpath("static/login.html").read_text(encoding="utf-8")


# --- Modèles de requêtes ---------------------------------------------------------------------


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


@app.get("/papers")
def browse_papers(
    q: str | None = Query(None, max_length=200, description="Titre, résumé ou auteur"),
    category: str | None = Query(None, max_length=50, description="Catégorie, ex. cond-mat.supr-con"),
    sort: Literal["recent", "oldest", "title", "added"] = "recent",
    page: int = Query(1, ge=1, le=10_000),
    per_page: int = Query(25, ge=1, le=100),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Parcours paginé du corpus (onglet Bibliothèque)."""
    total, papers = db.list_papers(
        conn, query=q, category=category, sort=sort, limit=per_page, offset=(page - 1) * per_page
    )
    return {"total": total, "page": page, "per_page": per_page, "papers": papers}


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


@app.get("/favorites")
def list_favorites(user: dict = Depends(current_user), conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Favoris du compte connecté, du plus récent au plus ancien."""
    return {"favorites": db.list_favorites(conn, user["id"])}


@app.put("/favorites/{paper_id:path}")
def add_favorite(paper_id: str, user: dict = Depends(current_user),
                 conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        db.add_favorite(conn, user["id"], paper_id)
    except KeyError:
        raise HTTPException(404, f"Papier inconnu : {paper_id}")
    return {"paper_id": paper_id, "favorite": True}


@app.delete("/favorites/{paper_id:path}")
def remove_favorite(paper_id: str, user: dict = Depends(current_user),
                    conn: psycopg.Connection = Depends(get_conn)) -> dict:
    db.remove_favorite(conn, user["id"], paper_id)
    return {"paper_id": paper_id, "favorite": False}


@app.get("/corpus/categories")
def corpus_categories(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Thèmes présents dans le corpus avec leur nombre de papiers (filtre de la bibliothèque)."""
    return {
        "categories": [
            {**c, "name": keywords.theme_name(c["code"])} for c in db.corpus_categories(conn)
        ]
    }


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
