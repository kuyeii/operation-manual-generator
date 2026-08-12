import httpx
import pytest
from pydantic import ValidationError

from manual_generator.llm import (
    ModelOptions,
    OpenAICompatibleClient,
    _json_object,
    _normalize_action,
)
from manual_generator.schemas import browser_action_adapter


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080/"),
        ({"url": "http://127.0.0.1:8080/"}, "http://127.0.0.1:8080/"),
        ({"href": "/dashboard"}, "/dashboard"),
    ],
)
def test_normalize_navigate_target_aliases(target, expected):
    action = browser_action_adapter.validate_python(
        _normalize_action({"action": "navigate", "target": target})
    )

    assert action.url == expected


def test_normalize_navigate_rejects_unknown_target_shape():
    with pytest.raises(ValidationError):
        browser_action_adapter.validate_python(
            _normalize_action({"action": "navigate", "target": {"text": "首页"}})
        )


def test_json_object_accepts_fenced_response():
    assert _json_object('```json\n{"action":"screenshot"}\n```') == {
        "action": "screenshot"
    }


@pytest.mark.asyncio
async def test_feature_discovery_falls_back_to_json_object(monkeypatch):
    requests = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, *, headers, json):
            requests.append(json)
            request = httpx.Request("POST", url)
            if len(requests) == 1:
                return httpx.Response(400, request=request, json={"error": "unsupported format"})
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {
                            "content": '{"features":[{"title":"计算","entry_path":"/","goal":"执行计算","evidence_ids":["E001"]}]}'
                        }
                    }]
                },
            )

    monkeypatch.setattr("manual_generator.llm.httpx.AsyncClient", lambda **_: FakeAsyncClient())
    client = OpenAICompatibleClient(
        ModelOptions("key", "https://example.com/v1", "model", "chat_completions")
    )

    features = await client.discover_features([{"id": "E001", "kind": "button"}])

    assert features[0]["title"] == "计算"
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[1]["response_format"] == {"type": "json_object"}
