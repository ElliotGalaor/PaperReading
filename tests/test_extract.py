import pytest
from pdfgen import make_pdf

from papersim.extract import (
    extract_document,
    find_arxiv_ids,
    find_dois,
    guess_abstract,
    guess_title,
    title_matches,
)

ABSTRACT = (
    "We study the pseudogap phase of cuprate superconductors using a combination of "
    "numerical simulations and analytical arguments, and show that disorder suppresses it."
)
FIRST_PAGE = [
    "arXiv:2101.00001v2 [cond-mat.supr-con] 3 Jan 2021",
    "Pseudogap suppression by disorder in cuprates",
    "Ada Lovelace, Emmy Noether",
    "ada@example.org",
    "Abstract",
    ABSTRACT,
    "1 Introduction",
    "The pseudogap was discovered long ago.",
]
REFERENCES = ["References", "[1] A. Author, arXiv:1706.03762 (2017). doi:10.1103/PhysRevB.1.1"]


def test_extract_pdf():
    doc = extract_document("paper.pdf", make_pdf([FIRST_PAGE, REFERENCES]))
    assert doc.arxiv_ids == ["2101.00001"]  # l'arXiv cité en page 2 est ignoré
    assert doc.dois == []
    assert doc.title == "Pseudogap suppression by disorder in cuprates"
    assert doc.abstract == ABSTRACT
    assert doc.query_text().startswith("Pseudogap suppression")


def test_extract_text_file():
    text = "\n".join(FIRST_PAGE).encode()
    doc = extract_document("notes.md", text)
    assert doc.abstract == ABSTRACT


def test_unsupported_and_empty_files():
    with pytest.raises(ValueError, match="Format non pris en charge"):
        extract_document("image.png", b"\x89PNG....")
    with pytest.raises(ValueError, match="Aucun texte"):
        extract_document("blank.pdf", make_pdf([[""]]))
    with pytest.raises(ValueError, match="PDF illisible"):
        extract_document("broken.pdf", b"%PDF-1.4 garbage")


def test_find_identifiers():
    assert find_arxiv_ids("arXiv:hep-th/9901001v1 and arXiv: 2101.00001") == [
        "hep-th/9901001",
        "2101.00001",
    ]
    assert find_dois("DOI: 10.1103/PhysRevB.99.1. Also (10.1000/xyz).") == [
        "10.1103/physrevb.99.1",
        "10.1000/xyz",
    ]
    assert find_dois("https://doi.org/10.48550/arXiv.2101.00001") == []


def test_guess_abstract_requires_marker_and_length():
    assert guess_abstract("No marker here " * 20) is None
    assert guess_abstract("Abstract\nToo short.\nIntroduction") is None
    long_text = "Abstract: " + "word " * 1000
    assert len(guess_abstract(long_text)) == 3000


def test_guess_title_skips_noise():
    assert guess_title("arXiv:2101.00001v1\nA\nA real paper title here\n") == "A real paper title here"
    assert guess_title("x\ny") is None


def test_title_matches():
    page = "\n".join(FIRST_PAGE)
    assert title_matches("Pseudogap Suppression by Disorder in Cuprates", page)
    assert not title_matches("Attention Is All You Need", page)
