import httpx
import pytest
from fastapi.testclient import TestClient
from pdfgen import make_pdf

from papersim import api, filesearch
from papersim.extract import Extracted
from papersim.models import Paper

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
                        lambda conn, pid, k: log.setdefault("similar", (pid, k)) and RESULT)
    monkeypatch.setattr(filesearch.search, "search_text",
                        lambda conn, emb, q, k: log.setdefault("text", (emb, q, k)) and RESULT)
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
    assert calls["similar"] == ("arxiv:2101.00001", 5)
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

    def fake_search(conn, doc, k):
        captured.update(doc=doc, k=k)
        return {"mode": "text", "results": [], "warnings": []}

    monkeypatch.setattr(api.filesearch, "search_document", fake_search)
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


def test_k_is_bounded(client):
    tc, _ = client
    resp = tc.post("/search/file", files={"file": ("a.txt", b"x" * 100, "text/plain")}, data={"k": "500"})
    assert resp.status_code == 422
