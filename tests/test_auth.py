import pytest
from fastapi.testclient import TestClient

from papersim import api, auth
from papersim.config import settings

# --- Mots de passe, validation, limitation ----------------------------------------------------


def test_password_hashing():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("wrong password", h)
    assert auth.hash_password("correct horse battery") != h  # sel aléatoire
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("x", "bcrypt$1$2$3$aa$bb")


@pytest.mark.parametrize("name", ["alice", "Bob.Martin", "a_b-c", "abc"])
def test_valid_usernames(name):
    assert auth.validate_username(f" {name} ") == name


@pytest.mark.parametrize("name", ["", "ab", "a" * 33, "alice smith", "élodie", "a/b", "<script>"])
def test_invalid_usernames(name):
    with pytest.raises(ValueError):
        auth.validate_username(name)


def test_password_rules():
    auth.validate_password("12345678")
    for bad in ("", "short", "x" * 257):
        with pytest.raises(ValueError):
            auth.validate_password(bad)


def test_throttle(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock["t"])
    t = auth.LoginThrottle(free=2, base_wait=10, max_wait=40)
    for _ in range(2):
        t.failure("1.1.1.1", "alice")
    assert t.wait_time("1.1.1.1", "alice") == 0      # 2 échecs gratuits
    t.failure("1.1.1.1", "Alice ")                   # même compte (casse, espaces)
    assert t.wait_time("1.1.1.1", "alice") == 10
    assert t.wait_time("1.1.1.1", "bob") == 0        # autre compte non touché
    t.failure("1.1.1.1", "alice")
    assert t.wait_time("1.1.1.1", "alice") == 20     # attente doublée
    for _ in range(5):
        t.failure("1.1.1.1", "alice")
    assert t.wait_time("1.1.1.1", "alice") == 40     # plafond
    clock["t"] += 41
    assert t.wait_time("1.1.1.1", "alice") == 0
    t.success("1.1.1.1", "alice")
    t.failure("1.1.1.1", "alice")
    assert t.wait_time("1.1.1.1", "alice") == 0      # compteur remis à zéro


# --- Routes de connexion (comptes en mémoire) -------------------------------------------------


class FakeConn:
    pass


@pytest.fixture
def client(monkeypatch):
    users: dict[str, dict] = {}
    sessions: dict[str, int] = {}

    def create_user(conn, username, password):
        username = auth.validate_username(username)
        auth.validate_password(password)
        if username.lower() in users:
            raise ValueError("Cet identifiant est déjà pris.")
        users[username.lower()] = {"id": len(users) + 1, "username": username, "hash": auth.hash_password(password)}
        return {"id": users[username.lower()]["id"], "username": username}

    def authenticate(conn, username, password):
        u = users.get((username or "").strip().lower())
        return {"id": u["id"], "username": u["username"]} if u and auth.verify_password(password, u["hash"]) else None

    def create_session(conn, user_id, days):
        token = f"tok{len(sessions)}"
        sessions[token] = user_id
        return token

    def user_from_session(conn, token):
        uid = sessions.get(token)
        return next(({"id": u["id"], "username": u["username"]} for u in users.values() if u["id"] == uid), None)

    monkeypatch.setattr(api.auth, "create_user", create_user)
    monkeypatch.setattr(api.auth, "authenticate", authenticate)
    monkeypatch.setattr(api.auth, "create_session", create_session)
    monkeypatch.setattr(api.auth, "user_from_session", user_from_session)
    monkeypatch.setattr(api.auth, "delete_session", lambda conn, token: sessions.pop(token, None))
    monkeypatch.setattr(api, "login_throttle", auth.LoginThrottle(free=2, base_wait=30))
    monkeypatch.setattr(api.db, "list_favorites", lambda conn, uid: [])
    api.app.dependency_overrides[api.get_conn] = lambda: FakeConn()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_signup_logs_in_and_logout_logs_out(client):
    assert client.get("/auth/me").json() == {"user": None, "allow_signup": True}
    resp = client.post("/auth/signup", json={"username": "alice", "password": "secret-pass"})
    assert resp.status_code == 200 and resp.json() == {"user": {"username": "alice"}}
    cookie = resp.headers["set-cookie"]
    assert "papersim_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/auth/me").json()["user"] == {"username": "alice"}
    assert client.get("/favorites").status_code == 200

    client.post("/auth/logout")
    assert client.get("/auth/me").json()["user"] is None
    assert client.get("/favorites").status_code == 401


def test_signup_errors(client):
    client.post("/auth/signup", json={"username": "alice", "password": "secret-pass"})
    resp = client.post("/auth/signup", json={"username": "ALICE", "password": "another-pass"})
    assert resp.status_code == 409 and "déjà pris" in resp.json()["detail"]
    resp = client.post("/auth/signup", json={"username": "bob", "password": "short"})
    assert resp.status_code == 422 and "trop court" in resp.json()["detail"]


def test_login_and_wrong_password_throttled(client):
    client.post("/auth/signup", json={"username": "alice", "password": "secret-pass"})
    client.post("/auth/logout")
    ok = client.post("/auth/login", json={"username": "alice", "password": "secret-pass"})
    assert ok.status_code == 200
    client.post("/auth/logout")
    for _ in range(3):
        bad = client.post("/auth/login", json={"username": "alice", "password": "nope-nope"})
    assert bad.status_code == 401 and "incorrect" in bad.json()["detail"]
    blocked = client.post("/auth/login", json={"username": "alice", "password": "secret-pass"})
    assert blocked.status_code == 429 and "Réessayez" in blocked.json()["detail"]


def test_unknown_user_gets_same_message(client):
    resp = client.post("/auth/login", json={"username": "ghost", "password": "whatever1"})
    assert resp.status_code == 401 and resp.json()["detail"] == "Identifiant ou mot de passe incorrect."


def test_signup_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    resp = client.post("/auth/signup", json={"username": "alice", "password": "secret-pass"})
    assert resp.status_code == 403
    assert client.get("/auth/me").json()["allow_signup"] is False


def test_login_page_is_served(client):
    resp = client.get("/login")
    assert resp.status_code == 200 and 'autocomplete="username"' in resp.text
