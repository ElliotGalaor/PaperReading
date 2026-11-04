import pytest
from fastapi.testclient import TestClient

from papersim import api, db


class FakeConn:
    pass


@pytest.fixture
def client(monkeypatch):
    captured = {}

    def fake_list(conn, *, query, category, sort, limit, offset):
        captured.update(query=query, category=category, sort=sort, limit=limit, offset=offset)
        return 61, [{"id": "arxiv:1", "title": "T"}]

    monkeypatch.setattr(api.db, "list_papers", fake_list)
    monkeypatch.setattr(api.db, "corpus_categories",
                        lambda conn: [{"code": "cond-mat.supr-con", "count": 700}, {"code": "cs.SE", "count": 2}])
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    yield TestClient(api.app), captured
    api.app.dependency_overrides.clear()


def test_browse_defaults(client):
    tc, captured = client
    resp = tc.get("/papers")
    assert resp.status_code == 200
    assert resp.json() == {"total": 61, "page": 1, "per_page": 25, "papers": [{"id": "arxiv:1", "title": "T"}]}
    assert captured == {"query": None, "category": None, "sort": "recent", "limit": 25, "offset": 0}


def test_browse_filters_and_pagination(client):
    tc, captured = client
    resp = tc.get("/papers", params={"q": "nitride", "category": "cond-mat.supr-con", "sort": "title",
                                     "page": 3, "per_page": 10})
    assert resp.status_code == 200
    assert captured == {"query": "nitride", "category": "cond-mat.supr-con", "sort": "title", "limit": 10, "offset": 20}


@pytest.mark.parametrize("params", [{"sort": "random"}, {"page": 0}, {"per_page": 500}, {"q": "x" * 300}])
def test_browse_rejects_bad_params(client, params):
    tc, _ = client
    assert tc.get("/papers", params=params).status_code == 422


def test_get_paper_route_still_works_next_to_browse(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr(api.db, "get_paper", lambda conn, pid: {"id": pid})
    assert tc.get("/papers/arxiv:hep-th/9901001").json() == {"id": "arxiv:hep-th/9901001"}


def test_categories_have_readable_names(client):
    tc, _ = client
    cats = tc.get("/corpus/categories").json()["categories"]
    assert cats[0] == {"code": "cond-mat.supr-con", "count": 700, "name": "Supraconductivité"}
    assert cats[1]["name"] == "Informatique (cs.SE)"


def test_library_is_the_default_tab(client):
    tc, _ = client
    html = tc.get("/").text
    assert 'id="tab-library" aria-controls="panel-library" aria-selected="true"' in html
    assert 'id="panel-search" role="tabpanel" aria-labelledby="tab-search" hidden' in html


def test_sort_whitelist():
    assert set(db.LIBRARY_SORTS) == {"recent", "oldest", "title", "added"}
    with pytest.raises(ValueError):
        db.list_papers(FakeConn(), sort="p.id; DROP TABLE papers")
