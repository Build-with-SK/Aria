"""
tests/test_app_shell_cache.py
=============================
The built app is served at /app by backend.main._SPAStatics. Its index.html
names the hashed bundles of the current build; sent without Cache-Control,
browsers reused it heuristically and kept running the previous build for
minutes after a deploy (2026-10-16). The shell must be revalidated every time;
hashed assets stay cacheable and a missing asset still 404s.
"""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path):
    from backend.main import _SPAStatics
    (tmp_path / "index.html").write_text("<script src='assets/index-abc.js'></script>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    app.mount("/app", _SPAStatics(directory=str(tmp_path), html=True), name="app")
    return TestClient(app)


@pytest.mark.parametrize("path", ["/app/", "/app/index.html", "/app/strategies", "/app/login"])
def test_the_shell_is_revalidated_every_time(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "index-abc.js" in r.text
    assert r.headers.get("cache-control") == "no-cache"


def test_a_hashed_asset_stays_cacheable(client):
    r = client.get("/app/assets/index-abc.js")
    assert r.status_code == 200
    assert "no-cache" not in (r.headers.get("cache-control") or "")


def test_a_missing_asset_is_still_a_404_not_the_shell(client):
    assert client.get("/app/assets/index-gone.js").status_code == 404


def test_a_revalidation_of_the_shell_is_a_304_that_keeps_the_rule(client):
    first = client.get("/app/")
    r = client.get("/app/", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert r.headers.get("cache-control") == "no-cache"
