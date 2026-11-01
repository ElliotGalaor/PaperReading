import pytest

from papersim.ingest.arxiv import parse_feed
from papersim.ingest.openalex import parse_work, reconstruct_abstract

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>Pseudogap and
      superconductivity</title>
    <summary>  We study the
      pseudogap phase.  </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Emmy Noether</name></author>
    <arxiv:doi>10.1103/PhysRevB.99.1</arxiv:doi>
    <arxiv:journal_ref>Phys. Rev. B 99, 1 (2021)</arxiv:journal_ref>
    <category term="cond-mat.supr-con" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cond-mat.str-el" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

ARXIV_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_foo</id>
    <title>Error</title>
    <summary>incorrect id format for foo</summary>
  </entry>
</feed>"""


def test_parse_arxiv_feed():
    [p] = parse_feed(ARXIV_FEED)
    assert p.arxiv_id == "2101.00001"
    assert p.title == "Pseudogap and superconductivity"
    assert p.abstract == "We study the pseudogap phase."
    assert p.authors == ["Ada Lovelace", "Emmy Noether"]
    assert p.year == 2021
    assert p.doi == "10.1103/physrevb.99.1"
    assert p.venue == "Phys. Rev. B 99, 1 (2021)"
    assert p.categories == ["cond-mat.supr-con", "cond-mat.str-el"]
    assert p.canonical_id() == "arxiv:2101.00001"


def test_arxiv_error_raises():
    with pytest.raises(ValueError, match="incorrect id format"):
        parse_feed(ARXIV_ERROR)


def test_reconstruct_abstract():
    assert reconstruct_abstract({"the": [0, 3], "cat": [1], "saw": [2], "dog": [4]}) == (
        "the cat saw the dog"
    )
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_parse_openalex_work():
    work = {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.48550/arXiv.2101.00001",
        "display_name": "Pseudogap and superconductivity",
        "abstract_inverted_index": {"We": [0], "study": [1]},
        "publication_year": 2021,
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {}}],
        "primary_location": {"source": {"display_name": "arXiv"}},
        "locations": [],
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    }
    p = parse_work(work)
    assert p.openalex_id == "W123"
    assert p.arxiv_id == "2101.00001"
    assert p.abstract == "We study"
    assert p.authors == ["Ada Lovelace"]
    assert p.references == ["W1", "W2"]
    assert p.venue == "arXiv"


def test_parse_openalex_arxiv_from_location():
    work = {
        "id": "https://openalex.org/W9",
        "doi": "https://doi.org/10.1103/PhysRevB.99.1",
        "display_name": "T",
        "locations": [{"landing_page_url": "https://arxiv.org/abs/2101.00001v1"}],
    }
    p = parse_work(work)
    assert p.doi == "10.1103/physrevb.99.1"
    assert p.arxiv_id == "2101.00001"


def test_parse_openalex_without_title_is_skipped():
    assert parse_work({"id": "https://openalex.org/W1", "display_name": None}) is None
