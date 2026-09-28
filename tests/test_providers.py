"""Provider abstraction: vendor mapping, error taxonomy, resilience, structured output, registry."""

import json

import httpx
import pytest
from pydantic import BaseModel

from shared.providers.base import GenerationConfig, ImagePart, Message
from shared.providers.errors import (
    CapabilityError,
    CircuitOpenError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from shared.providers.gemini import GeminiProvider
from shared.providers.openai_compatible import OpenAICompatibleProvider
from shared.providers.registry import ProviderSettings, build_providers
from shared.providers.resilience import CircuitBreaker, FallbackProvider, ResilientProvider
from shared.providers.structured import extract_json_object, generate_structured
from tests.conftest import ScriptedProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gemini_ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}}


async def _no_sleep(_):
    return None


# ---------------------------------------------------------------- Gemini

async def test_gemini_request_mapping_and_response():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_gemini_ok('{"ok": true}'))

    p = GeminiProvider(api_key="k", model="gemini-2.0-flash", client=_client(handler))
    msgs = [Message.system("sys"), Message.user("look", images=(ImagePart(b"img"),)), Message.assistant("prev")]
    r = await p.generate(msgs, GenerationConfig(json_output=True, temperature=0.1, max_output_tokens=99))

    assert seen["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "k"
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"
    assert body["contents"][1]["role"] == "model"
    assert body["generationConfig"] == {"temperature": 0.1, "maxOutputTokens": 99, "responseMimeType": "application/json"}
    assert r.text == '{"ok": true}' and r.input_tokens == 11 and r.output_tokens == 7


@pytest.mark.parametrize("status,headers,exc", [
    (401, {}, ProviderAuthError), (403, {}, ProviderAuthError), (429, {"retry-after": "3"}, ProviderRateLimitError),
    (500, {}, ProviderUnavailableError), (503, {}, ProviderUnavailableError), (400, {}, ProviderResponseError),
])
async def test_http_status_mapping(status, headers, exc):
    p = GeminiProvider(api_key="k", model="m", client=_client(lambda r: httpx.Response(status, headers=headers, text="x")))
    with pytest.raises(exc) as info:
        await p.generate([Message.user("hi")])
    if exc is ProviderRateLimitError:
        assert info.value.retry_after_s == 3.0 and info.value.retryable


async def test_gemini_blocked_and_empty_are_response_errors():
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
    p = GeminiProvider(api_key="k", model="m", client=_client(lambda r: httpx.Response(200, json=blocked)))
    with pytest.raises(ProviderResponseError):
        await p.generate([Message.user("hi")])
    safety = {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}
    p = GeminiProvider(api_key="k", model="m", client=_client(lambda r: httpx.Response(200, json=safety)))
    with pytest.raises(ProviderResponseError):
        await p.generate([Message.user("hi")])


async def test_timeout_maps_to_retryable_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    p = GeminiProvider(api_key="k", model="m", client=_client(handler))
    with pytest.raises(ProviderTimeoutError) as info:
        await p.generate([Message.user("hi")])
    assert info.value.retryable


# ---------------------------------------------------------------- OpenAI-compatible (Qwen / Qwen-VL / vLLM)

async def test_openai_compatible_vision_payload():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    p = OpenAICompatibleProvider(name="qwen_vl", base_url="https://x/v1", model="qwen-vl-max", api_key="s", vision=True,
                                 client=_client(handler))
    r = await p.generate([Message.system("sys"), Message.user("describe", images=(ImagePart(b"a"),))], GenerationConfig(json_output=True))
    body = seen["body"]
    assert seen["auth"] == "Bearer s"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1]["content"][0]["type"] == "image_url"
    assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["response_format"] == {"type": "json_object"}
    assert r.input_tokens == 5


async def test_text_only_provider_rejects_images_before_network():
    calls = []
    p = OpenAICompatibleProvider(name="qwen", base_url="https://x/v1", model="qwen-plus",
                                 client=_client(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(CapabilityError):
        await p.generate([Message.user("x", images=(ImagePart(b"a"),))])
    assert calls == []


# ---------------------------------------------------------------- resilience

async def test_resilient_retries_transient_then_succeeds():
    inner = ScriptedProvider([ProviderUnavailableError("down"), ProviderTimeoutError("slow"), "ok"])
    p = ResilientProvider(inner, max_attempts=3, sleep=_no_sleep)
    assert (await p.generate([Message.user("x")])).text == "ok"
    assert len(inner.requests) == 3


async def test_resilient_does_not_retry_auth():
    inner = ScriptedProvider([ProviderAuthError("bad key"), "never"])
    p = ResilientProvider(inner, max_attempts=3, sleep=_no_sleep)
    with pytest.raises(ProviderAuthError):
        await p.generate([Message.user("x")])
    assert len(inner.requests) == 1


async def test_circuit_breaker_opens_and_half_opens():
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_s=10, clock=lambda: now[0])
    inner = ScriptedProvider([ProviderUnavailableError("a"), ProviderUnavailableError("b"), "recovered"])
    p = ResilientProvider(inner, max_attempts=1, breaker=breaker, sleep=_no_sleep)
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await p.generate([Message.user("x")])
    with pytest.raises(CircuitOpenError):
        await p.generate([Message.user("x")])
    now[0] = 11.0
    assert breaker.state == "half_open"
    assert (await p.generate([Message.user("x")])).text == "recovered"
    assert breaker.state == "closed"


async def test_fallback_uses_next_provider():
    a = ScriptedProvider([ProviderUnavailableError("a down")], name="a")
    b = ScriptedProvider(["from b"], name="b")
    assert (await FallbackProvider([a, b]).generate([Message.user("x")])).text == "from b"


async def test_fallback_raises_when_all_fail():
    a = ScriptedProvider([ProviderUnavailableError("a")], name="a")
    b = ScriptedProvider([ProviderAuthError("b")], name="b")
    with pytest.raises(ProviderError, match="all providers failed"):
        await FallbackProvider([a, b]).generate([Message.user("x")])


# ---------------------------------------------------------------- structured output

class _Out(BaseModel):
    value: int


def test_extract_json_object_tolerates_chatter():
    assert extract_json_object('Sure! ```json\n{"value": 1}\n```') == {"value": 1}
    assert extract_json_object('prefix {"value": 2} suffix {"x": 3}') == {"value": 2}
    assert extract_json_object("no json") is None


async def test_structured_repairs_invalid_then_accepts():
    p = ScriptedProvider(['{"value": "not-int"}', '{"value": 5}'])
    result = await generate_structured(p, [Message.user("go")], _Out, max_repairs=1)
    assert result.value.value == 5
    assert len(result.attempts) == 2 and result.attempts[0].errors
    assert "rejected" in p.requests[1][-1].text


async def test_structured_semantic_validator_and_exhaustion():
    p = ScriptedProvider(['{"value": 1}', '{"value": 1}'])
    with pytest.raises(StructuredOutputError) as info:
        await generate_structured(p, [Message.user("go")], _Out, semantic_validator=lambda o: ["must be even"] if o.value % 2 else [], max_repairs=1)
    assert info.value.errors == ["must be even"]


# ---------------------------------------------------------------- registry

def test_registry_builds_chains_and_rejects_bad_config():
    s = ProviderSettings(monta_text_providers="qwen,gemini", monta_vision_providers="qwen_vl", gemini_api_key="k", _env_file=None)
    bundle = build_providers(s)
    assert isinstance(bundle.text, FallbackProvider)
    assert isinstance(bundle.vision, ResilientProvider)

    assert build_providers(ProviderSettings(_env_file=None)).text is None
    with pytest.raises(ValueError, match="cannot be used for vision"):
        build_providers(ProviderSettings(monta_vision_providers="qwen", _env_file=None))
    with pytest.raises(ValueError, match="unknown provider"):
        build_providers(ProviderSettings(monta_text_providers="nope", _env_file=None))
