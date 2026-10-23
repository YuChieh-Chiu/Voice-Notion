import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import dependencies


def request():
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})


def test_empty_admin_key_never_authenticates(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "SIRI_API_KEY", "")
    with pytest.raises(HTTPException) as error:
        asyncio.run(dependencies.get_user_context(request(), "", None))
    assert error.value.status_code == 401


def test_personal_management_rejects_missing_or_wrong_key():
    for key in (None, "", "wrong", "非 ASCII 金鑰"):
        with pytest.raises(HTTPException) as error:
            asyncio.run(dependencies.get_personal_context(request(), key))
        assert error.value.status_code == 401
    context = asyncio.run(dependencies.get_personal_context(request(), "test-personal"))
    assert context.type == "admin"


@pytest.mark.parametrize("config", ["[]", "null", '{"X-Gemini-Api-Key":{}}', '"text"', "x" * 8193])
def test_byok_config_must_be_a_bounded_object(config):
    with pytest.raises(HTTPException) as error:
        asyncio.run(dependencies.get_user_context(request(), None, config))
    assert error.value.status_code == 400
