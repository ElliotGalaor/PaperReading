"""Fichiers de déploiement : route de santé, Dockerfile, docker-compose, workflow de livraison."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

yaml = pytest.importorskip("yaml")  # fourni par uvicorn[standard]

from papersim import api

ROOT = Path(__file__).resolve().parents[1]


def test_health_needs_no_database(monkeypatch):
    def no_db():
        raise AssertionError("la route de santé ne doit pas ouvrir de connexion")

    monkeypatch.setattr(api.db, "connect", no_db)
    resp = TestClient(api.app).get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_dockerfile_runs_unprivileged_with_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER papersim" in dockerfile
    assert "/health" in dockerfile and "HEALTHCHECK" in dockerfile
    assert "download.pytorch.org/whl/cpu" in dockerfile  # torch CPU, image plus légère


def test_compose_keeps_db_only_by_default():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "profiles" not in services["db"]          # « docker compose up -d » : la base seule
    assert services["app"]["profiles"] == ["app"]
    assert services["app"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert services["app"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "@db:5432" in services["app"]["environment"]["PAPERSIM_DATABASE_URL"]


def test_cd_publishes_only_after_green_ci():
    cd = yaml.safe_load((ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8"))
    triggers = cd[True] if True in cd else cd["on"]  # PyYAML lit la clé « on » comme True
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    image = cd["jobs"]["image"]
    assert "conclusion == 'success'" in image["if"]
    assert image["permissions"] == {"contents": "read", "packages": "write"}
