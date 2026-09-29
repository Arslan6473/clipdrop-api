import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ["API_KEY"] = "test-key"

from app import extractor, main  # noqa: E402
from app.config import Settings  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.jobs import Job  # noqa: E402

main.settings = Settings()
AUTH = {"x-api-key": "test-key"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "settings", Settings(api_key="test-key", work_dir=str(tmp_path)))
    main.limiter.__init__(1000, 60)
    with TestClient(main.app) as c:
        yield c


def test_requires_api_key(client):
    res = client.post("/analyze", json={"url": "https://youtu.be/aqz-KE-bpKQ"})
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


def test_rejects_unsupported_and_invalid(client):
    assert client.post("/analyze", json={"url": "https://example.com/x"}, headers=AUTH).json()["code"] == "UNSUPPORTED_URL"
    assert client.post("/analyze", json={}, headers=AUTH).json()["code"] == "INVALID_URL"
    res = client.post("/download", json={"url": "https://youtu.be/x", "formatId": "../etc"}, headers=AUTH)
    assert res.json()["code"] == "BAD_REQUEST"


def test_rejects_large_bodies(client):
    res = client.post("/analyze", content=b'{"url":"' + b"a" * 5000 + b'"}', headers={**AUTH, "content-type": "application/json"})
    assert res.status_code == 400


def test_analyze_success(client, monkeypatch):
    monkeypatch.setattr(
        extractor, "analyze",
        lambda url, platform: {"success": True, "video": {"title": "T", "source": platform}, "formats": [], "notice": None},
    )
    res = client.post("/analyze", json={"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["video"]["source"] == "youtube"


def test_provider_errors_are_mapped_without_details(client, monkeypatch):
    def boom(url, platform):
        raise ApiError("PRIVATE_CONTENT", "secret internal detail /srv/x")

    monkeypatch.setattr(extractor, "analyze", boom)
    res = client.post("/analyze", json={"url": "https://youtu.be/aqz-KE-bpKQ"}, headers=AUTH)
    assert res.status_code == 403
    assert "secret" not in res.text and res.json()["code"] == "PRIVATE_CONTENT"


def test_download_then_fetch_file(client, monkeypatch, tmp_path):
    def fake_download(url, format_id, store):
        job_id, directory = store.new_directory()
        path = os.path.join(directory, "media.mp4")
        open(path, "wb").write(b"video-bytes")
        job = Job(job_id, directory, path, "Clip 720p.mp4", "video/mp4", 11, time.time() + 60)
        store.register(job)
        return job

    monkeypatch.setattr(extractor, "download", fake_download)
    res = client.post("/download", json={"url": "https://www.instagram.com/reel/CDUMkliABpa/", "formatId": "v720"}, headers=AUTH)
    body = res.json()
    assert res.status_code == 200 and body["download"]["filename"] == "Clip 720p.mp4"
    file_path = "/" + body["download"]["downloadUrl"].split("/", 3)[3]
    got = client.get(file_path)
    assert got.status_code == 200 and got.content == b"video-bytes"
    assert got.headers["content-disposition"].startswith('attachment; filename="Clip 720p.mp4"')
    assert client.get("/files/" + "0" * 32).json()["code"] == "DOWNLOAD_EXPIRED"


def test_rate_limit(client):
    main.limiter.__init__(2, 60)
    codes = [client.post("/analyze", json={"url": "https://example.com"}, headers=AUTH).status_code for _ in range(3)]
    assert codes[-1] == 429
