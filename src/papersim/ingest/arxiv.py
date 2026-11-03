"""Ingestion via l'API arXiv (flux Atom).

Doc : https://info.arxiv.org/help/api/user-manual.html
Conditions : https://info.arxiv.org/help/api/tou.html

Garde-fous (l'API est gratuite, mais arXiv peut bloquer un usage abusif) :
- une seule requête à la fois et au moins 3 s entre deux requêtes, pour TOUT le
  programme (threads de l'API web compris) et entre programmes (verrou fichier) ;
- réessais limités sur 429 / 503, en respectant l'en-tête Retry-After ;
- nombre de résultats plafonné (PAPERSIM_ARXIV_MAX_RESULTS, 30 000 au plus) ;
- cache d'une journée : la même requête n'est pas renvoyée deux fois le même jour.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx

from ..config import settings
from ..ids import normalize_arxiv_id, normalize_doi
from ..models import Paper

log = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
PAGE_SIZE = 100  # arXiv accepte jusqu'à 2000 par appel
DELAY_SECONDS = 3.0  # « no more than one request every three seconds »
HARD_MAX_RESULTS = 30_000  # au-delà, arXiv répond 400
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


# --- Verrou : une requête à la fois, espacées de DELAY_SECONDS -------------------


@contextmanager
def _file_lock(path: Path) -> Iterator:
    """Verrou exclusif entre processus ; le fichier contient l'heure de la dernière requête."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
            try:
                yield f
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield f
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class ArxivGate:
    def __init__(self, lock_path: Path, delay: float = DELAY_SECONDS):
        self.lock_path = lock_path
        self.delay = delay
        self._thread_lock = threading.Lock()

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Attend son tour ; la requête s'exécute pendant que le verrou est tenu."""
        with self._thread_lock, _file_lock(self.lock_path) as f:
            f.seek(0)
            try:
                last = float(f.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = last + self.delay - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                # Compté à partir de la FIN de la requête : marge supplémentaire.
                f.seek(0)
                f.truncate()
                f.write(repr(time.time()))
                f.flush()


_gate: ArxivGate | None = None


def get_gate() -> ArxivGate:
    global _gate
    if _gate is None:
        _gate = ArxivGate(settings.cache_dir / "arxiv.lock")
    return _gate


# --- Cache d'une journée -----------------------------------------------------------


def _cache_file(params: dict) -> Path:
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    return settings.cache_dir / "arxiv" / f"{key}.xml"


def _read_cache(params: dict) -> str | None:
    path = _cache_file(params)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
    except FileNotFoundError:
        return None
    if mtime != datetime.now(timezone.utc).date():
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(params: dict, text: str) -> None:
    path = _cache_file(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- Requête unique : cache -> verrou -> réessais ----------------------------------------


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    try:
        return min(300.0, max(DELAY_SECONDS, float(resp.headers.get("Retry-After", ""))))
    except ValueError:
        return (10.0, 30.0, 60.0)[min(attempt, 2)]


def _get(client: httpx.Client, params: dict) -> str:
    cached = _read_cache(params)
    if cached is not None:
        log.info("arXiv : réponse en cache")
        return cached

    for attempt in range(MAX_RETRIES + 1):
        with get_gate().slot():
            resp = client.get(ARXIV_API, params=params)
        if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            delay = _retry_delay(resp, attempt)
            log.warning("arXiv a répondu %s, nouvel essai dans %.0f s", resp.status_code, delay)
            time.sleep(delay)
            continue
        resp.raise_for_status()
        break

    if "/api/errors" not in resp.text:  # ne pas mettre en cache une erreur
        _write_cache(params, resp.text)
    return resp.text


# --- Parsing -------------------------------------------------------------------


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_entry(entry) -> Paper:
    if "/api/errors" in entry.get("id", ""):
        raise ValueError(f"Erreur de l'API arXiv : {_clean(entry.get('summary'))}")
    published = entry.get("published") or ""
    return Paper(
        title=_clean(entry.get("title")),
        abstract=_clean(entry.get("summary")) or None,
        authors=[a.get("name", "") for a in entry.get("authors", []) if a.get("name")],
        year=int(published[:4]) if published[:4].isdigit() else None,
        venue=_clean(entry.get("arxiv_journal_ref")) or None,
        categories=[t["term"] for t in entry.get("tags", []) if t.get("term")],
        doi=normalize_doi(entry.get("arxiv_doi")),
        arxiv_id=normalize_arxiv_id(entry["id"]),
        source="arxiv",
    )


def parse_feed(xml: str) -> list[Paper]:
    return [parse_entry(e) for e in feedparser.parse(xml).entries]


# --- Points d'entrée ------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "papersim/0.1"})


def clamp_max_results(requested: int) -> int:
    limit = min(settings.arxiv_max_results, HARD_MAX_RESULTS)
    if requested > limit:
        log.warning(
            "arXiv : %d résultats demandés, plafonné à %d (PAPERSIM_ARXIV_MAX_RESULTS). "
            "Affinez la requête, ou utilisez OAI-PMH pour de gros volumes.",
            requested,
            limit,
        )
        return limit
    return requested


def search_arxiv(query: str, max_results: int = 100) -> list[Paper]:
    """Recherche arXiv, ex. 'cat:cond-mat.supr-con AND abs:"pseudogap"'."""
    max_results = clamp_max_results(max_results)
    papers: list[Paper] = []
    with _client() as client:
        while len(papers) < max_results:
            n = min(PAGE_SIZE, max_results - len(papers))
            text = _get(
                client,
                {
                    "search_query": query,
                    "start": len(papers),
                    "max_results": n,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            batch = parse_feed(text)
            papers.extend(batch)
            if len(batch) < n:
                break
    return papers


def fetch_arxiv_ids(ids: list[str]) -> list[Paper]:
    """Récupère des papiers précis par identifiant arXiv."""
    ids = list(dict.fromkeys(normalize_arxiv_id(i) for i in ids))
    papers: list[Paper] = []
    with _client() as client:
        for start in range(0, len(ids), PAGE_SIZE):
            chunk = ids[start : start + PAGE_SIZE]
            text = _get(client, {"id_list": ",".join(chunk), "max_results": len(chunk)})
            papers.extend(parse_feed(text))
    return papers
