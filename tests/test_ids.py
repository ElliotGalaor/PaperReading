import pytest

from papersim.ids import (
    arxiv_id_from_doi,
    arxiv_id_from_url,
    normalize_arxiv_id,
    normalize_doi,
    openalex_short_id,
)
from papersim.models import Paper


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://arxiv.org/abs/2101.00001v2", "2101.00001"),
        ("https://arxiv.org/pdf/2101.00001v1.pdf", "2101.00001"),
        ("arXiv:2101.00001", "2101.00001"),
        ("2101.00001v3", "2101.00001"),
        ("http://arxiv.org/abs/hep-th/9901001v1", "hep-th/9901001"),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert normalize_arxiv_id(raw) == expected


def test_arxiv_id_from_url():
    assert arxiv_id_from_url("https://arxiv.org/abs/1706.03762") == "1706.03762"
    assert arxiv_id_from_url("https://journals.aps.org/prb/abstract/10.1103/x") is None
    assert arxiv_id_from_url(None) is None


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1103/PhysRevB.99.1") == "10.1103/physrevb.99.1"
    assert normalize_doi("doi:10.1/ABC") == "10.1/abc"
    assert normalize_doi("") is None
    assert normalize_doi(None) is None


def test_arxiv_id_from_doi():
    assert arxiv_id_from_doi("10.48550/arxiv.2101.00001") == "2101.00001"
    assert arxiv_id_from_doi("10.1103/physrevb.99.1") is None


def test_openalex_short_id():
    assert openalex_short_id("https://openalex.org/W2741809807") == "W2741809807"


def test_canonical_id_priority():
    assert Paper(title="t", arxiv_id="1", doi="d", openalex_id="W1").canonical_id() == "arxiv:1"
    assert Paper(title="t", doi="d", openalex_id="W1").canonical_id() == "doi:d"
    assert Paper(title="t", openalex_id="W1").canonical_id() == "openalex:W1"
    with pytest.raises(ValueError):
        Paper(title="t").canonical_id()
