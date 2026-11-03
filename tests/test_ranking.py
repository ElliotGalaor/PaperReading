import pytest
from fastapi.testclient import TestClient

from papersim import api, search
from papersim.search import Years

PAPER = {"id": "arxiv:1", "title": "T", "abstract": "A", "authors": [], "year": 2023}


class FakeConn:
    pass


# --- Moteur --------------------------------------------------------------------


def test_text_signal_errors():
    with pytest.raises(ValueError, match="aucun modèle local"):
        search.text_signal(FakeConn(), None, "query", "semantic")
    with pytest.raises(ValueError, match="papier reconnu"):
        search.text_signal(FakeConn(), None, "query", "citations")
    with pytest.raises(ValueError, match="inconnu"):
        search.text_signal(FakeConn(), None, "query", "nope")


def test_paper_signal_dispatch(monkeypatch):
    calls = {}
    monkeypatch.setattr(search.db, "get_paper", lambda conn, pid: PAPER if pid == "arxiv:1" else None)
    monkeypatch.setattr(search.db, "get_embedding", lambda *a: None)
    monkeypatch.setattr(search, "lexical_ranking",
                        lambda conn, text, k, exclude, years: calls.setdefault("lex", (text, k, exclude, years)) and [])
    years = Years(2020, None)
    search.paper_signal(FakeConn(), "arxiv:1", "lexical", k=15, years=years)
    assert calls["lex"] == ("T A", 15, "arxiv:1", years)

    with pytest.raises(ValueError, match="embedding"):
        search.paper_signal(FakeConn(), "arxiv:1", "semantic")
    with pytest.raises(KeyError):
        search.paper_signal(FakeConn(), "arxiv:404", "lexical")


def test_ranking_details_keeps_order_and_skips_missing(monkeypatch):
    monkeypatch.setattr(search.db, "get_papers", lambda conn, ids: {"a": {"id": "a"}, "c": {"id": "c"}})
    out = search.ranking_details(FakeConn(), [("a", 0.91234), ("b", 0.8), ("c", 0.7)])
    assert [r["paper"]["id"] for r in out] == ["a", "c"]
    assert out[0] == {"rank": 1, "score": 0.9123, "paper": {"id": "a"}}
    assert out[1]["rank"] == 3  # le rang d'origine est conservé


# --- Routes ----------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    captured = {}

    def fake_paper_signal(conn, pid, signal, k, years):
        if pid == "arxiv:404":
            raise KeyError(pid)
        if signal == "semantic" and pid == "arxiv:noemb":
            raise ValueError("Ce papier n'a pas encore d'embedding.")
        captured.update(pid=pid, signal=signal, k=k, years=years)
        return [("arxiv:2", 0.9)]

    def fake_text_signal(conn, embedder, query, signal, k, years):
        captured.update(query=query, signal=signal, k=k, years=years, embedder=embedder)
        return [("arxiv:2", 0.5)]

    monkeypatch.setattr(api.search, "paper_signal", fake_paper_signal)
    monkeypatch.setattr(api.search, "text_signal", fake_text_signal)
    monkeypatch.setattr(api.search, "ranking_details",
                        lambda conn, r: [{"rank": 1, "score": r[0][1], "paper": {"id": r[0][0]}}])
    monkeypatch.setattr(api.db, "get_paper", lambda conn, pid: {"id": pid, "title": "T"})
    monkeypatch.setattr(api, "_embedder", lambda: "EMB")
    yield TestClient(api.app), captured
    api.app.dependency_overrides.clear()


def test_paper_ranking_route(client):
    tc, captured = client
    resp = tc.get("/papers/arxiv:hep-th/9901001/ranking/lexical", params={"year_min": 2010})
    assert resp.status_code == 200
    body = resp.json()
    assert body["signal"] == "lexical"
    assert body["query"]["paper"]["id"] == "arxiv:hep-th/9901001"
    assert captured == {"pid": "arxiv:hep-th/9901001", "signal": "lexical", "k": 15, "years": Years(2010, None)}


def test_paper_ranking_errors(client):
    tc, _ = client
    assert tc.get("/papers/arxiv:404/ranking/lexical").status_code == 404
    resp = tc.get("/papers/arxiv:noemb/ranking/semantic")
    assert resp.status_code == 422 and "embedding" in resp.json()["detail"]
    assert tc.get("/papers/arxiv:1/ranking/bogus").status_code == 422
    assert tc.get("/papers/arxiv:1/ranking/lexical", params={"k": 500}).status_code == 422


def test_text_ranking_route(client):
    tc, captured = client
    resp = tc.post("/search/text/ranking", json={"query": "pseudogap disorder", "signal": "semantic", "year_max": 2024})
    assert resp.status_code == 200
    assert captured["signal"] == "semantic" and captured["k"] == 15
    assert captured["years"] == Years(None, 2024)
    assert captured["embedder"] == "EMB"

    tc.post("/search/text/ranking", json={"query": "pseudogap disorder", "signal": "lexical"})
    assert captured["embedder"] is None  # pas de modèle chargé pour le classement par mots


def test_ranking_page_is_served(client):
    tc, _ = client
    resp = tc.get("/ranking")
    assert resp.status_code == 200
    assert "Classement" in resp.text


def test_shared_authors_script_is_served(client):
    tc, _ = client
    for page in ("/", "/ranking"):
        assert '<script src="/static/authors.js"></script>' in tc.get(page).text
    resp = tc.get("/static/authors.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "PaperAuthors" in resp.text
