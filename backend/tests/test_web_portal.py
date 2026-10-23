from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import app


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.inline_scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "img", "link"):
            for name in ("src", "href"):
                if name in attributes:
                    self.assets.append(attributes[name])
        if tag == "script" and "src" not in attributes:
            self.inline_scripts.append(attributes)


@pytest.mark.parametrize("path", ["/", "/demo"])
def test_byok_portal_blocks_external_code_and_does_not_cache_credentials(path):
    response = TestClient(app, base_url="http://localhost").get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "connect-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy and "'unsafe-inline'" not in policy
    parser = AssetParser()
    parser.feed(response.text)
    assert parser.assets and not parser.inline_scripts
    assert all(
        urlsplit(asset).scheme == "" and asset.startswith("/static/") for asset in parser.assets
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("/static/demo.html", "/demo"),
        ("/static/drive_setup.html", "/oauth/google/setup"),
        ("/static/drive_complete.html", "/oauth/google/complete"),
    ],
)
def test_raw_html_paths_cannot_bypass_portal_security_headers(source, target):
    response = TestClient(app, base_url="http://localhost").get(source, follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == target
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/oauth/google/setup", "/oauth/google/complete"])
def test_drive_portal_loads_local_styles_and_binds_inline_script_nonce(path):
    response = TestClient(app, base_url="http://localhost").get(path)
    assert response.status_code == 200
    parser = AssetParser()
    parser.feed(response.text)
    assert "/static/capture.css" in parser.assets
    assert all(asset.startswith("/static/") for asset in parser.assets)
    policy = response.headers["content-security-policy"]
    assert "style-src 'self'" in policy and "'unsafe-inline'" not in policy
    for attributes in parser.inline_scripts:
        assert attributes.get("nonce") and f"'nonce-{attributes['nonce']}'" in policy


@pytest.mark.parametrize("path", ["/static/capture.css", "/static/demo.js"])
def test_local_portal_assets_are_available(path):
    response = TestClient(app, base_url="http://localhost").get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/css" if path.endswith(".css") else ("text/javascript", "application/javascript")
    )
