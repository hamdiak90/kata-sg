"""Tests de l'API. Ils ont besoin d'un PostgreSQL :
- en local : docker compose up -d db
- en CI : un conteneur postgres est lancé par le workflow
"""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_ne_depend_pas_de_la_base():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_teste_la_base():
    assert client.get("/ready").status_code == 200


def test_creer_puis_lister():
    r = client.post("/api/items", json={"name": "test"})
    assert r.status_code == 201
    new_id = r.json()["id"]
    ids = [i["id"] for i in client.get("/api/items").json()]
    assert new_id in ids


def test_nom_vide_refuse():
    assert client.post("/api/items", json={"name": ""}).status_code == 422


def test_crash_desactive_par_defaut():
    assert client.post("/api/crash").status_code == 404
