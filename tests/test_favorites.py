from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from papersim import api

ALICE = {"id": 1, "username": "alice"}
BOB = {"id": 2, "username": "bob"}


class FakeConn:
    pass


@pytest.fixture
def setup(monkeypatch):
    """Favoris en mémoire par compte, et compte connecté choisi par le test."""
    store: dict[tuple[int, str], datetime] = {}
    known = {"arxiv:1", "arxiv:2", "arxiv:hep-th/9901001"}
    who = {"user": ALICE}

    def add(conn, uid, pid):
        if pid not in known:
            raise KeyError(pid)
        store.setdefault((uid, pid), datetime.now(timezone.utc))

    def remove(conn, uid, pid):
        return store.pop((uid, pid), None) is not None

    def listing(conn, uid):
        mine = [(pid, t) for (u, pid), t in store.items() if u == uid]
        return [{"paper": {"id": pid, "title": "T"}, "added_at": t}
                for pid, t in sorted(mine, key=lambda kv: kv[1], reverse=True)]

    monkeypatch.setattr(api.db, "add_favorite", add)
    monkeypatch.setattr(api.db, "remove_favorite", remove)
    monkeypatch.setattr(api.db, "list_favorites", listing)
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    api.app.dependency_overrides[api.optional_user] = lambda: who["user"]
    yield TestClient(api.app), who
    api.app.dependency_overrides.clear()


def ids(client):
    return [f["paper"]["id"] for f in client.get("/favorites").json()["favorites"]]


def test_add_list_remove(setup):
    client, _ = setup
    assert ids(client) == []
    resp = client.put("/favorites/arxiv:1")
    assert resp.status_code == 200 and resp.json() == {"paper_id": "arxiv:1", "favorite": True}
    client.put("/favorites/arxiv:hep-th/9901001")  # identifiant avec « / »
    client.put("/favorites/arxiv:1")              # deux fois : sans effet
    assert ids(client) == ["arxiv:hep-th/9901001", "arxiv:1"]  # plus récent d'abord
    client.delete("/favorites/arxiv:hep-th/9901001")
    assert ids(client) == ["arxiv:1"]


def test_each_account_has_its_own_favorites(setup):
    client, who = setup
    client.put("/favorites/arxiv:1")
    who["user"] = BOB
    assert ids(client) == []
    client.put("/favorites/arxiv:2")
    client.delete("/favorites/arxiv:1")  # ne touche pas aux favoris d'Alice
    assert ids(client) == ["arxiv:2"]
    who["user"] = ALICE
    assert ids(client) == ["arxiv:1"]


def test_favorites_require_login(setup):
    client, who = setup
    who["user"] = None
    for method, url in [("get", "/favorites"), ("put", "/favorites/arxiv:1"), ("delete", "/favorites/arxiv:1")]:
        resp = getattr(client, method)(url)
        assert resp.status_code == 401
        assert "Connectez-vous" in resp.json()["detail"]


def test_library_stays_public(setup, monkeypatch):
    client, who = setup
    who["user"] = None
    monkeypatch.setattr(api.db, "list_papers", lambda conn, **kw: (0, []))
    assert client.get("/papers").status_code == 200
    assert client.get("/").status_code == 200


def test_unknown_paper_is_404(setup):
    client, _ = setup
    resp = client.put("/favorites/arxiv:404")
    assert resp.status_code == 404 and "inconnu" in resp.json()["detail"]


def test_removing_absent_favorite_is_harmless(setup):
    client, _ = setup
    assert client.delete("/favorites/arxiv:1").status_code == 200


def test_pages_load_session_and_favorites_scripts(setup):
    client, _ = setup
    for page in ("/", "/ranking"):
        html = client.get(page).text
        assert html.index("/static/session.js") < html.index("/static/favorites.js")
        assert 'id="user-bar"' not in html  # remplacé par l'onglet « Compte » du bord droit
        assert '<link rel="stylesheet" href="/static/session.css">' in html  # panneau de connexion
    for script in ("favorites.js", "session.js", "session.css"):
        resp = client.get(f"/static/{script}")
        assert resp.status_code == 200
        # Le navigateur doit revérifier les scripts : jamais d'ancienne version avec une page récente.
        assert resp.headers["cache-control"] == "no-cache"
        etag = resp.headers["etag"]
        assert client.get(f"/static/{script}", headers={"If-None-Match": etag}).status_code == 304
    js = client.get("/static/session.js").text
    assert "openLogin" in js and 'aria-modal' in js
    assert "account-edge" in js and "account-edge" in client.get("/static/session.css").text


def test_schema_declares_user_tables():
    from importlib.resources import files

    sql = files("papersim").joinpath("schema.sql").read_text(encoding="utf-8")
    for table in ("users", "sessions", "user_favorites"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "PRIMARY KEY (user_id, paper_id)" in sql
