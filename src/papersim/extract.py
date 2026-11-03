"""Extraction du contenu d'un fichier déposé par l'utilisateur (PDF ou texte).

Heuristiques volontairement simples (pas de GROBID à cette étape) :
- identifiants arXiv / DOI cherchés sur la PREMIÈRE page uniquement, pour ne pas
  prendre l'identifiant d'un papier cité en bibliographie ;
- abstract = texte entre « Abstract » et l'introduction (ou les mots-clés) ;
- titre = première ligne « longue » de la première page.
"""

import io
import re
from dataclasses import dataclass, field

from .ids import normalize_arxiv_id, normalize_doi

MAX_PAGES = 3  # seules les premières pages sont lues (rapide, et suffisant pour titre + abstract)
QUERY_CHARS = 3000

_ARXIV_STAMP = re.compile(
    r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I
)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ABSTRACT = re.compile(
    r"\babstract\b[\s.:—–-]*(.+?)"
    r"(?=\n\s*(?:\d+\.?\s*|I\.?\s+)?(?:introduction|keywords|key\s+words|index\s+terms|pacs)\b|\Z)",
    re.I | re.S,
)
_WORD = re.compile(r"[a-z0-9]{4,}")


@dataclass
class Extracted:
    first_page: str
    text: str
    title: str | None = None
    abstract: str | None = None
    arxiv_ids: list[str] = field(default_factory=list)
    dois: list[str] = field(default_factory=list)

    def query_text(self) -> str:
        """Texte utilisé pour une recherche en texte libre."""
        if self.abstract:
            return f"{self.title or ''}\n{self.abstract}".strip()
        return self.text[:QUERY_CHARS]


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def pdf_pages_text(data: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF protégé par mot de passe.")
        return [(page.extract_text() or "") for page in reader.pages[:max_pages]]
    except (PdfReadError, KeyError, IndexError) as e:
        raise ValueError(f"PDF illisible : {e}") from e


def find_arxiv_ids(text: str) -> list[str]:
    return list(dict.fromkeys(normalize_arxiv_id(m) for m in _ARXIV_STAMP.findall(text)))


def find_dois(text: str) -> list[str]:
    dois = (normalize_doi(m.rstrip(".,;)]}")) for m in _DOI.findall(text))
    return [d for d in dict.fromkeys(dois) if d and not d.startswith("10.48550/")]


def guess_abstract(text: str) -> str | None:
    m = _ABSTRACT.search(text)
    if not m:
        return None
    abstract = _collapse(m.group(1))
    if len(abstract) < 100:
        return None
    return abstract[:QUERY_CHARS]


def guess_title(first_page: str) -> str | None:
    for line in first_page.splitlines():
        line = line.strip()
        if _ARXIV_STAMP.search(line) or "@" in line or "doi" in line.lower():
            continue
        if len(line.split()) >= 3 and 15 <= len(line) <= 250:
            return line
    return None


def title_matches(title: str, first_page: str, threshold: float = 0.6) -> bool:
    """Vérifie qu'un titre récupéré en ligne figure bien sur la première page du fichier."""
    words = set(_WORD.findall(title.lower()))
    if not words:
        return False
    page_words = set(_WORD.findall(first_page.lower()))
    return len(words & page_words) / len(words) >= threshold


def extract_document(filename: str, data: bytes) -> Extracted:
    name = filename.lower()
    if data[:5] == b"%PDF-":
        pages = pdf_pages_text(data)
    elif name.endswith((".txt", ".md")):
        pages = [data.decode("utf-8", errors="replace")]
    else:
        raise ValueError("Format non pris en charge : déposez un PDF, un .txt ou un .md.")

    text = "\n".join(pages).strip()
    if len(_collapse(text)) < 50:
        raise ValueError("Aucun texte exploitable dans ce fichier (PDF scanné ?).")
    first_page = pages[0]
    return Extracted(
        first_page=first_page,
        text=text,
        title=guess_title(first_page),
        abstract=guess_abstract(text),
        arxiv_ids=find_arxiv_ids(first_page),
        dois=find_dois(first_page),
    )
