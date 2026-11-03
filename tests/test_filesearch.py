import httpx
import pytest
from fastapi.testclient import TestClient
from pdfgen import make_pdf

from papersim import api, filesearch
from papersim.extract import Extracted
from papersim.models import Paper
from papersim.search import Years

PAGE = "arXiv:2101.00001v1\nPseudogap suppression by disorder in cuprates\nAbstract\n" + "text " * 40
RESULT = [{"paper": {"id": "arxiv:2", "title": "Other"}, "score": 0.03, "signals": {}}]


class FakeConn:
    def commit(self): pass
    def rollback(self): pass


@pytest.fixture
def calls(monkeypatch):
    """Remplace réseau, base et moteur de recherche par des faux."""
    log = {}
    monkeypatch.setattr(filesearch.pipeline, "store_papers", lambda conn, ps: ["arxiv:2101.00001"])
    monkeypatch.setattr(filesearch.pipeline, "enrich_from_openalex", lambda conn, ids: 1)
    monkeypatch.setattr(filesearch.pipeline, "compute_embeddings", lambda conn: 1)
    monkeypatch.setattr(filesearch.db, "get_paper", lambda conn, pid: {"id": pid, "title": "T"})
    monkeypatch.setattr(filesearch.search, "find_similar",
                        lambda conn, pid, k, years: log.setdefault("similar", (pid, k, years)) and RESULT)
    monkeypatch.setattr(filesearch.search, "search_text",
                        lambda conn, emb, q, k, years: log.setdefault("text", (emb, q, k, years)) and RESULT)
    monkeypatch.setattr(filesearch, "_query_embedder", lambda warnings: None)
    return log


def _doc(**kw):
    return Extracted(first_page=PAGE, text=PAGE, title="Pseudogap suppression by disorder in cuprates",
                     abstract="text " * 40, **kw)


def test_recognized_paper_uses_full_search(monkeypatch, calls):
    paper = Paper(title="Pseudogap Suppression by Disorder in Cuprates", arxiv_id="2101.00001")
    monkeypatch.setattr(filesearch.arxiv, "fetch_arxiv_ids", lambda ids: [paper])
    out = filesearch.search_document(FakeConn(), _doc(arxiv_ids=["2101.00001"]), k=5)
    assert out["mode"] == "paper"
    assert calls["similar"][:2] == ("arxiv:2101.00001", 5)
    assert out["warnings"] == []


def test_mismatched_title_falls_back_to_text(monkeypatch, calls):
    other = Paper(title="Attention Is All You Need", arxiv_id="2101.00001")
    monkeypatch.setattr(filesearch.arxiv, "fetch_arxiv_ids", lambda ids: [other])
    out = filesearch.search_document(FakeConn(), _doc(arxiv_ids=["2101.00001"]), k=5)
    assert out["mode"] == "text"
    assert "similar" not in calls
    assert calls["text"][1].startswith("Pseudogap suppression")


def test_network_error_falls_back_with_warning(monkeypatch, calls):
    def boom(ids):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(filesearch.arxiv, "fetch_arxiv_ids", boom)
    out = filesearch.search_document(FakeConn(), _doc(arxiv_ids=["2101.00001"]))
    assert out["mode"] == "text"
    assert any("n'a pas répondu" in w for w in out["warnings"])


def test_enrichment_failure_is_a_warning(monkeypatch, calls):
    paper = Paper(title="Pseudogap Suppression by Disorder in Cuprates", arxiv_id="2101.00001")
    monkeypatch.setattr(filesearch.arxiv, "fetch_arxiv_ids", lambda ids: [paper])

    def boom(conn, ids):
        raise httpx.HTTPStatusError("429", request=httpx.Request("GET", "http://x"),
                                    response=httpx.Response(429))
    monkeypatch.setattr(filesearch.pipeline, "enrich_from_openalex", boom)
    out = filesearch.search_document(FakeConn(), _doc(arxiv_ids=["2101.00001"]))
    assert out["mode"] == "paper"
    assert any("citations" in w for w in out["warnings"])


# --- Route HTTP ---------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    captured = {}

    def fake_search(conn, doc, k, years):
        captured.update(doc=doc, k=k, years=years)
        return {"mode": "text", "results": [], "warnings": []}

    def fake_text(conn, embedder, query, k, years):
        captured.update(query=query, k=k, years=years)
        return []

    def fake_similar(conn, pid, k, years):
        captured.update(pid=pid, k=k, years=years)
        return []

    monkeypatch.setattr(api.filesearch, "search_document", fake_search)
    monkeypatch.setattr(api.search, "search_text", fake_text)
    monkeypatch.setattr(api.search, "find_similar", fake_similar)
    monkeypatch.setattr(api, "_embedder", lambda: None)
    yield TestClient(api.app), captured
    api.app.dependency_overrides.clear()


def test_index_page(client):
    tc, _ = client
    resp = tc.get("/")
    assert resp.status_code == 200
    assert "Glissez un fichier ici" in resp.text


def test_upload_pdf(client):
    tc, captured = client
    pdf = make_pdf([PAGE.splitlines()])
    resp = tc.post("/search/file", files={"file": ("p.pdf", pdf, "application/pdf")}, data={"k": "7"})
    assert resp.status_code == 200
    assert captured["k"] == 7
    assert captured["doc"].arxiv_ids == ["2101.00001"]


def test_upload_rejects_bad_files(client, monkeypatch):
    tc, _ = client
    resp = tc.post("/search/file", files={"file": ("x.png", b"\x89PNG", "image/png")})
    assert resp.status_code == 422
    assert "Format non pris en charge" in resp.json()["detail"]

    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)
    resp = tc.post("/search/file", files={"file": ("big.txt", b"x" * 11, "text/plain")})
    assert resp.status_code == 413


def test_upload_with_year_filter(client):
    tc, captured = client
    resp = tc.post("/search/file", files={"file": ("a.txt", b"x " * 100, "text/plain")},
                   data={"year_min": "2015", "year_max": "2020"})
    assert resp.status_code == 200
    assert captured["years"] == Years(2015, 2020)


def test_refilter_endpoints_pass_years(client):
    tc, captured = client
    resp = tc.post("/search/text", json={"query": "pseudogap disorder", "k": 5, "year_min": 2018})
    assert resp.status_code == 200
    assert captured["years"] == Years(2018, None) and captured["k"] == 5

    resp = tc.get("/papers/arxiv:hep-th/9901001/similar", params={"year_max": "2001"})
    assert resp.status_code == 200
    assert captured["pid"] == "arxiv:hep-th/9901001"
    assert captured["years"] == Years(None, 2001)


def test_inverted_or_absurd_years_are_rejected(client):
    tc, _ = client
    resp = tc.post("/search/text", json={"query": "abc", "year_min": 2020, "year_max": 2010})
    assert resp.status_code == 422
    assert "précéder" in resp.json()["detail"]
    resp = tc.get("/papers/x/similar", params={"year_min": "20"})
    assert resp.status_code == 422


def test_k_is_bounded(client):
    tc, _ = client
    resp = tc.post("/search/file", files={"file": ("a.txt", b"x" * 100, "text/plain")}, data={"k": "500"})
    assert resp.status_code == 422
