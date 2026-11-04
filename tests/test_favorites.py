from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from papersim import api


class FakeConn:
    pass


@pytest.fixture
def client(monkeypatch):
    """Favoris en mémoire à la place de la base."""
    store: dict[str, datetime] = {}
    known = {"arxiv:1", "arxiv:hep-th/9901001"}

    def add(conn, pid):
        if pid not in known:
            raise KeyError(pid)
        store.setdefault(pid, datetime.now(timezone.utc))

    def remove(conn, pid):
        return store.pop(pid, None) is not None

    def listing(conn):
        return [{"paper": {"id": pid, "title": "T"}, "added_at": t}
                for pid, t in sorted(store.items(), key=lambda kv: kv[1], reverse=True)]

    monkeypatch.setattr(api.db, "add_favorite", add)
    monkeypatch.setattr(api.db, "remove_favorite", remove)
    monkeypatch.setattr(api.db, "list_favorites", listing)
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_add_list_remove(client):
    assert client.get("/favorites").json() == {"favorites": []}

    resp = client.put("/favorites/arxiv:1")
    assert resp.status_code == 200 and resp.json() == {"paper_id": "arxiv:1", "favorite": True}
    client.put("/favorites/arxiv:hep-th/9901001")  # identifiant avec « / »
    client.put("/favorites/arxiv:1")              # deux fois : sans effet

    favs = client.get("/favorites").json()["favorites"]
    assert [f["paper"]["id"] for f in favs] == ["arxiv:hep-th/9901001", "arxiv:1"]  # plus récent d'abord
    assert "added_at" in favs[0]

    resp = client.delete("/favorites/arxiv:hep-th/9901001")
    assert resp.json() == {"paper_id": "arxiv:hep-th/9901001", "favorite": False}
    assert [f["paper"]["id"] for f in client.get("/favorites").json()["favorites"]] == ["arxiv:1"]


def test_unknown_paper_is_404(client):
    resp = client.put("/favorites/arxiv:404")
    assert resp.status_code == 404
    assert "inconnu" in resp.json()["detail"]


def test_removing_absent_favorite_is_harmless(client):
    assert client.delete("/favorites/arxiv:1").status_code == 200


def test_pages_load_favorites_script(client):
    for page in ("/", "/ranking"):
        assert '<script src="/static/favorites.js"></script>' in client.get(page).text
    js = client.get("/static/favorites.js")
    assert js.status_code == 200 and "PaperFavorites" in js.text


def test_schema_declares_favorites_table():
    from importlib.resources import files

    sql = files("papersim").joinpath("schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS favorites" in sql
    assert "ON DELETE CASCADE" in sql.split("favorites", 1)[1]
