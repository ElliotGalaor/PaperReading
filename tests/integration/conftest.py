"""Tests d'intégration contre une vraie base PostgreSQL + pgvector.

Activés seulement si PAPERSIM_TEST_DATABASE_URL pointe vers une base DÉDIÉE aux tests
(elle est vidée avant chaque test). En CI, un service pgvector/pgvector:pg16 la fournit.

En local :
    docker exec paperreading-db-1 psql -U papersim -c "CREATE DATABASE papersim_test"
    PAPERSIM_TEST_DATABASE_URL=postgresql://papersim:papersim@localhost:5432/papersim_test pytest -m integration
"""

import os

import pytest

from papersim import db
from papersim.config import settings

TEST_URL = os.environ.get("PAPERSIM_TEST_DATABASE_URL")
TABLES = "papers, paper_embeddings, paper_references, users, sessions, user_favorites"


def pytest_collection_modifyitems(items):
    for item in items:
        if "/integration/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
            if not TEST_URL:
                item.add_marker(pytest.mark.skip(reason="PAPERSIM_TEST_DATABASE_URL non défini"))


@pytest.fixture(scope="session")
def database():
    """Schéma créé une fois pour la session, sur la base de test."""
    original = settings.database_url
    settings.database_url = TEST_URL
    db.init_db()
    yield TEST_URL
    settings.database_url = original


@pytest.fixture
def conn(database):
    """Connexion sur une base vidée avant chaque test."""
    with db.connect() as c:
        c.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        c.execute("DROP TABLE IF EXISTS favorites")
        c.commit()
        yield c
