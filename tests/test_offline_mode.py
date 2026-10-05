"""
Tests for MONTA offline mode enforcement and local-first runtime validation.
"""

import pytest

from shared.providers.registry import ProviderSettings, build_providers


def test_offline_mode_rejects_cloud_gemini():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="gemini",
        gemini_api_key="fake-key",
    )
    with pytest.raises(ValueError, match="is a cloud service, but MONTA_OFFLINE_MODE is enabled"):
        build_providers(settings)


def test_offline_mode_rejects_cloud_qwen():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="qwen",
        qwen_api_key="fake-key",
    )
    with pytest.raises(ValueError, match="is a cloud service, but MONTA_OFFLINE_MODE is enabled"):
        build_providers(settings)


def test_offline_mode_accepts_local_ollama():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="ollama",
        monta_vision_providers="ollama_vl",
        ollama_base_url="http://localhost:11434",
        ollama_text_model="qwen2.5:7b",
        ollama_vision_model="qwen2-vl:7b",
    )
    bundle = build_providers(settings)
    assert bundle.is_offline
    assert bundle.text is not None
    assert bundle.vision is not None
    assert bundle.text.name == "local_ollama"
    assert bundle.text.model == "qwen2.5:7b"
    assert bundle.vision.name == "local_ollama"
    assert bundle.vision.model == "qwen2-vl:7b"


def test_offline_mode_accepts_llama_cpp():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="llama_cpp",
        llama_cpp_base_url="http://localhost:8080",
        llama_cpp_model="qwen2.5-7b-gguf",
    )
    bundle = build_providers(settings)
    assert bundle.is_offline
    assert bundle.text is not None
    assert bundle.text.name == "local_llama_cpp"
    assert bundle.text.model == "qwen2.5-7b-gguf"
