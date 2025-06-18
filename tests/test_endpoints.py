import pytest
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_start_interaction_returns_question():
    response = client.get('/start_interaction/1')
    assert response.status_code == 200
    data = response.json()
    assert 'frage' in data


def test_bericht_automatisch_contains_keys():
    response = client.get('/bericht/automatisch')
    assert response.status_code == 200
    data = response.json()
    assert 'typ' in data
    assert 'inhalt' in data
