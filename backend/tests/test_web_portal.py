import asyncio
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.main import app


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.inline_scripts = []
        self.favicons = []
        self.header_logos = []
        self.brand_text = []
        self.title_text = []
        self.in_header = False
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "img", "link"):
            for name in ("src", "href"):
                if name in attributes:
                    self.assets.append(attributes[name])
        if tag == "script" and "src" not in attributes:
            self.inline_scripts.append(attributes)
        if tag == "link" and "icon" in attributes.get("rel", "").split():
            self.favicons.append(attributes.get("href"))
        if tag == "img" and "brand-mark" in attributes.get("class", "").split():
            self.header_logos.append(attributes.get("src"))
        if tag == "header":
            self.in_header = True
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "header":
            self.in_header = False
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_header:
            self.brand_text.append(data)
        if self.in_title:
            self.title_text.append(data)


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


@pytest.mark.parametrize("path", ["/", "/demo", "/oauth/google/setup", "/oauth/google/complete"])
def test_portals_share_note_goblin_identity_and_allow_the_local_logo(path):
    client = TestClient(app, base_url="http://localhost")
    response = client.get(path)
    assert response.status_code == 200
    parser = AssetParser()
    parser.feed(response.text)
    assert "".join(parser.title_text).endswith(" · Note-Goblin")
    assert "".join(parser.brand_text).strip() == "Note-Goblin · 收件小站"
    assert parser.favicons == parser.header_logos == ["/static/img/logo.svg"]
    directives = {
        parts[0]: parts[1:]
        for directive in response.headers["content-security-policy"].split(";")
        if (parts := directive.split())
    }
    assert directives["img-src"] == ["'self'"]
    logo = client.get(parser.favicons[0])
    assert logo.status_code == 200 and logo.headers["content-type"].startswith("image/svg+xml")
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in logo.text


def test_application_name_defaults_to_note_goblin_and_fallback_uses_configured_name(monkeypatch):
    assert Settings.model_fields["APP_NAME"].default == "Note-Goblin"
    monkeypatch.setattr(main.settings, "APP_NAME", "Note-Goblin")
    monkeypatch.setattr(main.os.path, "exists", lambda path: False)
    assert asyncio.run(main.root()) == {"status": "ok", "service": "Note-Goblin"}
