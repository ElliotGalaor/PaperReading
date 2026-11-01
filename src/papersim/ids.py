"""Normalisation des identifiants (arXiv, DOI, OpenAlex) pour la déduplication."""

import re

_ARXIV_VERSION = re.compile(r"v\d+$")
_ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?(?:[?#]|$)", re.I)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
_DOI_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:", re.I)


def normalize_arxiv_id(raw: str) -> str:
    """'http://arxiv.org/abs/2101.00001v2' ou 'arXiv:2101.00001' -> '2101.00001'."""
    raw = raw.strip()
    m = _ARXIV_IN_URL.search(raw)
    if m:
        raw = m.group(1)
    raw = re.sub(r"^arxiv:", "", raw, flags=re.I)
    return _ARXIV_VERSION.sub("", raw)


def arxiv_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _ARXIV_IN_URL.search(url)
    return _ARXIV_VERSION.sub("", m.group(1)) if m else None


def normalize_doi(raw: str | None) -> str | None:
    """'https://doi.org/10.1103/PhysRevB.1.1' -> '10.1103/physrevb.1.1' (les DOI sont insensibles à la casse)."""
    if not raw:
        return None
    doi = _DOI_PREFIX.sub("", raw.strip()).lower()
    return doi or None


def arxiv_id_from_doi(doi: str | None) -> str | None:
    """Les DOI DataCite d'arXiv ont la forme 10.48550/arXiv.<id>."""
    m = _ARXIV_DOI.match(doi or "")
    return normalize_arxiv_id(m.group(1)) if m else None


def arxiv_doi(arxiv_id: str) -> str:
    return f"10.48550/arxiv.{arxiv_id}".lower()


def openalex_short_id(raw: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'."""
    return raw.rstrip("/").rsplit("/", 1)[-1] if raw else None
