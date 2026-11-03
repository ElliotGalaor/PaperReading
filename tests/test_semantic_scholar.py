import httpx

from papersim.config import S2_EMBEDDING_MODEL, Settings
from papersim.ingest import semantic_scholar
from papersim.ingest.semantic_scholar import parse_paper
from papersim.pipeline import _s2_lookup_id

S2_PAPER = {
    "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "externalIds": {"DOI": "10.1103/PhysRevB.99.1", "ArXiv": "2101.00001", "CorpusId": 13756489},
    "title": "Pseudogap and superconductivity",
    "abstract": "We study the pseudogap phase.",
    "year": 2021,
    "venue": "Physical Review B",
    "authors": [{"authorId": "1", "name": "Ada Lovelace"}, {"authorId": None, "name": None}],
    "fieldsOfStudy": ["Physics"],
    "embedding": {"model": "specter_v2", "vector": [0.1, 0.2, 0.3]},
}


def test_parse_paper():
    p = parse_paper(S2_PAPER)
    assert p.s2_id == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    assert p.doi == "10.1103/physrevb.99.1"
    assert p.arxiv_id == "2101.00001"
    assert p.authors == ["Ada Lovelace"]
    assert p.categories == ["Physics"]
    assert p.embedding == [0.1, 0.2, 0.3]
    assert p.embedding_model == S2_EMBEDDING_MODEL
    assert p.canonical_id() == "arxiv:2101.00001"


def test_parse_paper_minimal():
    p = parse_paper({"paperId": "abc", "title": "T", "externalIds": None, "embedding": None,
                     "fieldsOfStudy": None, "authors": []})
    assert p.canonical_id() == "s2:abc"
    assert p.embedding is None and p.embedding_model is None


def test_parse_paper_null_or_untitled():
    assert parse_paper(None) is None  # identifiant inconnu dans /paper/batch
    assert parse_paper({"paperId": "x", "title": "  "}) is None


def test_lookup_id_priority():
    row = {"s2_id": None, "arxiv_id": "2101.00001", "doi": "10.1/x"}
    assert _s2_lookup_id(row) == "ARXIV:2101.00001"
    assert _s2_lookup_id({**row, "s2_id": "abc"}) == "abc"
    assert _s2_lookup_id({**row, "arxiv_id": None}) == "DOI:10.1/x"
    assert _s2_lookup_id({"s2_id": None, "arxiv_id": None, "doi": None}) is None


def test_active_embedding_model():
    assert Settings(embedding_source="local").active_embedding_model == "allenai/specter2"
    assert Settings(embedding_source="s2").active_embedding_model == S2_EMBEDDING_MODEL


RealS2Client = semantic_scholar.S2Client


def _fake_client(monkeypatch, handler):
    monkeypatch.setattr(semantic_scholar, "MIN_INTERVAL", 0)
    monkeypatch.setattr(semantic_scholar.time, "sleep", lambda s: None)
    client = RealS2Client()
    client._http = httpx.Client(base_url=semantic_scholar.S2_API,
                                transport=httpx.MockTransport(handler))
    return client


def test_retry_on_429(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, json=[S2_PAPER])

    with _fake_client(monkeypatch, handler) as client:
        resp = client.request("POST", "/paper/batch", json={"ids": ["x"]})
    assert resp.json()[0]["title"] == S2_PAPER["title"]
    assert len(calls) == 3


def test_fetch_s2_keeps_alignment(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=[None, S2_PAPER])

    monkeypatch.setattr(semantic_scholar, "S2Client", lambda: _fake_client(monkeypatch, handler))
    result = semantic_scholar.fetch_s2(["ARXIV:0000.00000", "ARXIV:2101.00001"])
    assert result[0] is None
    assert result[1].arxiv_id == "2101.00001"


def test_search_pagination(monkeypatch):
    pages = {0: {"total": 3, "offset": 0, "next": 2, "data": [S2_PAPER, S2_PAPER]},
             2: {"total": 3, "offset": 2, "data": [S2_PAPER]}}
    seen = []

    def handler(request):
        offset = int(request.url.params["offset"])
        seen.append((offset, int(request.url.params["limit"]), request.url.params.get("year")))
        return httpx.Response(200, json=pages[offset])

    monkeypatch.setattr(semantic_scholar, "SEARCH_PAGE", 2)
    monkeypatch.setattr(semantic_scholar, "S2Client", lambda: _fake_client(monkeypatch, handler))
    papers = semantic_scholar.search_s2("q", max_results=10, year="2018-")
    assert len(papers) == 3
    assert seen == [(0, 2, "2018-"), (2, 2, "2018-")]
