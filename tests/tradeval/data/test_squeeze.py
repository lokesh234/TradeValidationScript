"""tradeval.data.squeeze: serving the file tradeval-squeeze publishes."""

from __future__ import annotations

import json

import pytest

from tradeval.data import squeeze


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    squeeze.clear()
    monkeypatch.delenv("TRADEVAL_SQUEEZE_FILE", raising=False)
    monkeypatch.delenv("TRADEVAL_SQUEEZE_S3", raising=False)
    yield
    squeeze.clear()


def test_reads_the_published_file_and_keeps_it_ten_minutes(tmp_path, monkeypatch):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"version": 1, "next": {"opex": "2026-10-16"}}))
    monkeypatch.setenv("TRADEVAL_SQUEEZE_FILE", str(path))
    assert squeeze.latest(now=0)["next"]["opex"] == "2026-10-16"
    path.write_text(json.dumps({"version": 1, "next": {"opex": "2026-11-20"}}))
    assert squeeze.latest(now=squeeze.FRESH_FOR - 1)["next"]["opex"] == "2026-10-16"
    assert squeeze.latest(now=squeeze.FRESH_FOR)["next"]["opex"] == "2026-11-20"


def test_nothing_configured_or_a_missing_file_is_not_published(tmp_path, monkeypatch):
    with pytest.raises(squeeze.NotPublished):
        squeeze.latest()
    monkeypatch.setenv("TRADEVAL_SQUEEZE_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(squeeze.NotPublished):
        squeeze.latest()


def test_the_endpoint_answers_503_until_published(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from serve import app

    client = TestClient(app)
    assert client.get("/mobile/squeeze").status_code == 503
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"version": 1}))
    monkeypatch.setenv("TRADEVAL_SQUEEZE_FILE", str(path))
    squeeze.clear()
    assert client.get("/mobile/squeeze").json() == {"version": 1}
