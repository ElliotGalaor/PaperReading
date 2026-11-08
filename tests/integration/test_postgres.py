"""Requêtes SQL réelles : import, bibliothèque, recherche hybride, comptes, favoris, API."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from papersim import api, auth, db, search
from papersim.config import settings
from papersim.models import Paper
from papersim.pipeline import TOPIC, store_papers
from papersim.search import Years


def paper(i: int, *, year: int, title: str, abstract: str, cats=("cond-mat.supr-con",), refs=()) -> Paper:
    return Paper(title=title, abstract=abstract, authors=[f"Author {i}", "Ada Lovelace"], year=year,
                 categories=list(cats), arxiv_id=f"2601.{i:05d}", references=list(refs), source="test")


def vec(*weights: float) -> np.ndarray:
    """Vecteur de la dimension du modèle, défini par ses premières composantes."""
    v = np.zeros(settings.embedding_dim, dtype=np.float32)
    v[: len(weights)] = weights
    return v / np.linalg.norm(v)


@pytest.fixture
def corpus(conn):
    """Cinq papiers : thèmes, années, références et embeddings contrôlés."""
    papers = [
        paper(1, year=2019, title="Pseudogap in underdoped cuprates",
              abstract="Disorder suppresses the pseudogap in cuprate superconductors.", refs=["W1", "W2", "W3"]),
        paper(2, year=2021, title="Disorder and the pseudogap phase",
              abstract="We study how disorder affects the pseudogap of cuprates.", refs=["W1", "W2"]),
        paper(3, year=2023, title=r"$\Gamma$-point pairing in nickelates",
              abstract="Nickelate superconductors show d-wave pairing.", refs=["W9"]),
        paper(4, year=2024, title="Josephson junctions with topological spectra",
              abstract="Multiterminal Josephson junctions and Andreev bound states.", cats=("cond-mat.mes-hall",)),
        paper(5, year=2025, title="Quantum computing with transmons",
              abstract="Transmon qubits for quantum computing.", cats=("quant-ph",), refs=["W1"]),
    ]
    ids = store_papers(conn, papers)
    vectors = [vec(1, 0.1), vec(1, 0.2), vec(0.5, 1), vec(0, 1, 0.3), vec(0, 0.1, 1)]
    db.save_embeddings(conn, list(zip(ids, vectors, strict=True)), TOPIC, settings.active_embedding_model)
    conn.commit()
    return ids


# --- Schéma et import -------------------------------------------------------------------------


def test_schema_is_idempotent(database):
    db.init_db()  # deuxième passage : aucune erreur
    db.init_db()


def test_upsert_merges_sources_without_duplicates(conn):
    [pid] = store_papers(conn, [paper(1, year=2020, title="Original", abstract="First abstract.")])
    enriched = Paper(title="Original (OpenAlex)", abstract="Different abstract.", doi="10.1/x",
                     arxiv_id="2601.00001", openalex_id="W42", references=["W1", "W2"], source="openalex")
    assert store_papers(conn, [enriched]) == [pid]
    row = db.get_paper(conn, pid)
    assert (row["openalex_id"], row["doi"]) == ("W42", "10.1/x")
    assert row["abstract"] == "First abstract."  # le premier abstract est conservé
    assert db.count_references(conn, pid) == 2
    assert conn.execute("SELECT count(*) AS n FROM papers").fetchone()["n"] == 1


def test_new_abstract_invalidates_title_only_embedding(conn):
    [pid] = store_papers(conn, [Paper(title="Title only", arxiv_id="2601.00009", source="test")])
    db.save_embeddings(conn, [(pid, vec(1))], TOPIC, "m")
    store_papers(conn, [Paper(title="Title only", abstract="Now with an abstract.", arxiv_id="2601.00009",
                              source="test")])
    assert db.get_embedding(conn, pid, TOPIC, "m") is None


# --- Bibliothèque -------------------------------------------------------------------------------


def test_library_search_filters_sort_and_pages(conn, corpus):
    total, page = db.list_papers(conn, limit=2)
    assert total == 5 and [p["year"] for p in page] == [2025, 2024]  # plus récents d'abord
    assert db.list_papers(conn, query="pseudogap")[0] == 2               # plein texte
    assert db.list_papers(conn, query="Author 3")[0] == 1                # nom d'auteur
    assert db.list_papers(conn, category="quant-ph")[0] == 1
    _, by_title = db.list_papers(conn, sort="title")
    # « $\Gamma$-point » se range à « Gamma » (entre D et J), pas en tête à cause de « $ » ;
    # attendu identique quelle que soit la collation de la base.
    assert [p["title"].split()[0] for p in by_title] == [
        "Disorder", r"$\Gamma$-point", "Josephson", "Pseudogap", "Quantum"]
    total, empty = db.list_papers(conn, limit=25, offset=500)
    assert total == 5 and empty == []
    cats = {c["code"]: c["count"] for c in db.corpus_categories(conn)}
    assert cats == {"cond-mat.supr-con": 3, "cond-mat.mes-hall": 1, "quant-ph": 1}


# --- Recherche hybride ---------------------------------------------------------------------------


def test_find_similar_combines_signals(conn, corpus):
    results = search.find_similar(conn, corpus[0], k=4)
    ids = [r["paper"]["id"] for r in results]
    assert ids[0] == corpus[1]  # même sujet, même vecteur proche, références communes
    first = results[0]["signals"]
    assert set(first) == {"semantic", "lexical", "citations"}
    assert corpus[0] not in ids  # jamais le papier lui-même


def test_year_filter_applies_to_every_signal(conn, corpus):
    for years in (Years(2023, None), Years(None, 2020), Years(2021, 2024)):
        results = search.find_similar(conn, corpus[0], k=10, years=years)
        for r in results:
            y = r["paper"]["year"]
            assert (years.min is None or y >= years.min) and (years.max is None or y <= years.max)


def test_single_signal_rankings(conn, corpus):
    semantic = search.paper_signal(conn, corpus[0], "semantic", k=3)
    assert semantic[0][0] == corpus[1] and semantic[0][1] > semantic[-1][1]
    coupling = search.paper_signal(conn, corpus[0], "citations", k=5)
    assert [pid for pid, _ in coupling][:2] == [corpus[1], corpus[4]]  # 2 puis 1 référence commune
    assert search.corpus_years(conn) == {"min": 2019, "max": 2025, "papers": 5}


# --- Comptes, sessions, favoris -------------------------------------------------------------------


def test_accounts_sessions_and_isolated_favorites(conn, corpus):
    alice = auth.create_user(conn, "alice", "alice-password")
    bob = auth.create_user(conn, "bob", "bob-password-1")
    with pytest.raises(ValueError, match="déjà pris"):
        auth.create_user(conn, "ALICE", "another-password")
    assert auth.authenticate(conn, "Alice", "alice-password")["id"] == alice["id"]
    assert auth.authenticate(conn, "alice", "wrong-password") is None

    token = auth.create_session(conn, alice["id"], days=30)
    assert auth.user_from_session(conn, token)["username"] == "alice"
    stored = conn.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert token not in stored  # seule l'empreinte est stockée

    db.add_favorite(conn, alice["id"], corpus[0])
    db.add_favorite(conn, bob["id"], corpus[2])
    db.add_favorite(conn, bob["id"], corpus[2])  # doublon sans effet
    assert [f["paper"]["id"] for f in db.list_favorites(conn, alice["id"])] == [corpus[0]]
    assert [f["paper"]["id"] for f in db.list_favorites(conn, bob["id"])] == [corpus[2]]
    with pytest.raises(KeyError):
        db.add_favorite(conn, bob["id"], "arxiv:inconnu")

    conn.execute("DELETE FROM users WHERE id = %s", (bob["id"],))  # le compte part avec ses favoris
    assert conn.execute("SELECT count(*) AS n FROM user_favorites").fetchone()["n"] == 1


def test_expired_session_is_refused(conn):
    user = auth.create_user(conn, "carol", "carol-password")
    token = auth.create_session(conn, user["id"], days=30)
    conn.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert auth.user_from_session(conn, token) is None


def test_first_account_adopts_legacy_favorites(conn, corpus):
    conn.execute("CREATE TABLE favorites (paper_id TEXT PRIMARY KEY REFERENCES papers (id), "
                 "created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    conn.execute("INSERT INTO favorites (paper_id) VALUES (%s), (%s)", (corpus[0], corpus[3]))
    first = auth.create_user(conn, "first", "first-password")
    second = auth.create_user(conn, "second", "second-password")
    assert {f["paper"]["id"] for f in db.list_favorites(conn, first["id"])} == {corpus[0], corpus[3]}
    assert db.list_favorites(conn, second["id"]) == []
    assert conn.execute("SELECT to_regclass('favorites') AS t").fetchone()["t"] is None


# --- API de bout en bout (vraie base, vrais cookies) ----------------------------------------------


def test_api_end_to_end(conn, corpus):
    client = TestClient(api.app)
    assert client.get("/papers", params={"q": "pseudogap"}).json()["total"] == 2
    assert client.put(f"/favorites/{corpus[0]}").status_code == 401

    resp = client.post("/auth/signup", json={"username": "dave", "password": "dave-password"})
    assert resp.status_code == 200 and "papersim_session" in resp.cookies
    assert client.put(f"/favorites/{corpus[0]}").status_code == 200
    favs = client.get("/favorites").json()["favorites"]
    assert [f["paper"]["id"] for f in favs] == [corpus[0]]

    similar = client.get(f"/papers/{corpus[0]}/similar", params={"k": 3, "year_min": 2021}).json()
    assert all(r["paper"]["year"] >= 2021 for r in similar["results"])

    client.post("/auth/logout")
    assert client.get("/favorites").status_code == 401
