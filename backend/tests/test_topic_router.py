import httpx
import pytest

from app.config import Settings
from app.services.topic_router import JEV_ENDPOINT, TopicRouter


def settings(**overrides):
    return Settings(
        _env_file=None,
        **(
            {
                "CAPTURE_CLASSIFICATION": "custom",
                "CAPTURE_ROUTER": "jev",
                "JEV_API_KEY": "test-jev",
                "CAPTURE_CATEGORIES": [
                    {"id": "finance", "description": "投資理財"},
                    {"id": "fitness", "description": "運動"},
                    {"id": "tech", "description": "AI 技術學習"},
                ],
            }
            | overrides
        ),
    )


def test_jev_request_uses_official_endpoint_and_closed_categories():
    def handle(request):
        assert str(request.url) == JEV_ENDPOINT
        assert request.headers["Authorization"] == "Bearer test-jev"
        assert b'"unclassified"' in request.content and b'"finance"' in request.content
        return httpx.Response(200, json={"answers": {"category": {"choice": "tech"}}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert TopicRouter(settings(), client).classify("AI 技術") == "tech"


@pytest.mark.parametrize("choice", ["../../outside", "https://evil.invalid", "invented", None])
def test_unknown_model_output_never_becomes_a_path(choice):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"answers": {"category": {"choice": choice}}})
        )
    ) as client:
        assert TopicRouter(settings(), client).classify("source") == "unclassified"


def test_provider_failure_and_metadata_only_never_block_original_storage():
    def handle(request):
        raise httpx.ReadTimeout("test provider unavailable")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        router = TopicRouter(settings(), client)
        assert router.classify("source") == "unclassified"
        assert router.classify("#finance investment.pdf", metadata_only=True) == "finance"
        assert router.classify("AI.pdf", metadata_only=True) == "unclassified"
        assert router.classify("#fitness 今天跑步") == "fitness"
        assert TopicRouter(settings(CAPTURE_CLASSIFICATION="none"), client).classify("text") is None
