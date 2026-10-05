"""
Tests for Local AI Runtime, ModelRegistry, and Ollama/LocalAIProvider adapters.
"""

import pytest
import httpx

from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import DeviceType, HardwareProfile
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.base import LocalModelInfo, RuntimeType
from shared.local_runtime.provider import LocalAIProvider
from shared.local_runtime.registry import ModelRegistry
from shared.providers.base import (
    Capability,
    GenerationConfig,
    ImagePart,
    Message,
    ModelResponse,
)
from shared.providers.errors import (
    CapabilityError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def test_model_registry_known_models():
    qwen_text = ModelRegistry.get_spec("qwen2.5:7b")
    assert qwen_text.family == "qwen"
    assert Capability.TEXT in qwen_text.capabilities
    assert Capability.VISION not in qwen_text.capabilities
    assert not qwen_text.is_vision

    qwen_vl = ModelRegistry.get_spec("qwen2-vl:7b")
    assert Capability.VISION in qwen_vl.capabilities
    assert qwen_vl.is_vision

    llava = ModelRegistry.get_spec("llava:7b")
    assert llava.is_vision


def test_model_registry_selection():
    hw_low = HardwareProfile(
        device_type=DeviceType.CPU_ONLY,
        os_name="linux",
        cpu_arch="x86_64",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        total_ram_gb=8.0,
        available_ram_gb=4.0,
        vram_gb=0.0,
        recommended_model_size="3b",
    )
    # Picks 3b for low spec
    best_text = ModelRegistry.select_best_model("text", hw_low, ["qwen2.5:3b", "qwen2.5:7b"])
    assert "3b" in best_text

    # Vision model selection
    best_vis = ModelRegistry.select_best_model("vision", hw_low, ["qwen2.5:7b", "qwen2-vl:7b"])
    assert best_vis == "qwen2-vl:7b"


@pytest.mark.asyncio
async def test_ollama_runtime_chat_mock():
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/version"):
            return httpx.Response(200, json={"version": "0.5.11"})
        if url.endswith("/api/tags"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b",
                            "size": 4700000000,
                            "details": {"family": "qwen", "parameter_size": "7b", "quantization_level": "q4_k_m"},
                        },
                        {
                            "name": "qwen2-vl:7b",
                            "size": 5200000000,
                            "details": {"family": "qwen_vl", "parameter_size": "7b", "quantization_level": "q4_k_m"},
                        },
                    ]
                },
            )
        if url.endswith("/api/chat"):
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5:7b",
                    "message": {"role": "assistant", "content": '{"pace": "fast", "mood": "epic"}'},
                    "done_reason": "stop",
                    "total_duration": 150000000,
                    "prompt_eval_count": 42,
                    "eval_count": 28,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)
    runtime = OllamaRuntime(base_url="http://mock-ollama:11434", client=client)

    try:
        # Check health
        health = await runtime.check_health()
        assert health.is_healthy
        assert health.version == "0.5.11"
        assert "qwen2.5:7b" in health.installed_models

        # List models
        models = await runtime.list_models()
        assert len(models) == 2
        assert models[0].name == "qwen2.5:7b"

        # LocalAIProvider execution
        provider = LocalAIProvider(runtime, "qwen2.5:7b")
        assert Capability.TEXT in provider.capabilities
        assert Capability.JSON_MODE in provider.capabilities

        response = await provider.generate(
            [Message.user("Make it fast and energetic")],
            config=GenerationConfig(json_output=True),
        )
        assert response.provider == "ollama"
        assert response.model == "qwen2.5:7b"
        assert '{"pace": "fast"' in response.text
        assert response.input_tokens == 42
        assert response.output_tokens == 28
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_ollama_runtime_rejects_vision_on_text_only_model():
    runtime = OllamaRuntime(base_url="http://mock-ollama:11434")
    provider = LocalAIProvider(runtime, "qwen2.5:7b")

    img = ImagePart(data=b"fake-image", mime_type="image/jpeg")
    msg = Message.user("Analyze this", images=(img,))

    with pytest.raises(CapabilityError):
        await provider.generate([msg])


@pytest.mark.asyncio
async def test_ollama_runtime_connection_failure():
    # Attempting to talk to a non-existent port
    runtime = OllamaRuntime(base_url="http://127.0.0.1:59999")
    provider = LocalAIProvider(runtime, "qwen2.5:7b")

    with pytest.raises(ProviderUnavailableError):
        await provider.generate([Message.user("hello")], config=GenerationConfig(timeout_s=1.0))
