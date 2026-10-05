"""
MONTA — Provider Registry
===========================
Builds text and vision providers from environment configuration. Supports both
local offline AI runtimes (Ollama, llama.cpp, MLX) and optional cloud providers.

Environment:
    MONTA_OFFLINE_MODE      "true"/"1" to enforce 100% offline local execution
    MONTA_TEXT_PROVIDERS    ordered fallback chain, e.g. "ollama,qwen,gemini"
    MONTA_VISION_PROVIDERS  ordered fallback chain, e.g. "ollama_vl,qwen_vl"
    MONTA_ARBITRATION       "failover" (default) or "consensus"
    MONTA_JUDGE_PROVIDERS   provider(s) for the LLM Story Judge
    OLLAMA_BASE_URL, OLLAMA_TEXT_MODEL, OLLAMA_VISION_MODEL
    LLAMA_CPP_BASE_URL, LLAMA_CPP_MODEL
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL
    QWEN_BASE_URL, QWEN_API_KEY, QWEN_TEXT_MODEL, QWEN_VL_MODEL
    OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_MODEL, OPENAI_COMPAT_VISION
    MONTA_PROVIDER_MAX_CONCURRENCY, MONTA_PROVIDER_MAX_ATTEMPTS
"""

import logging
from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.hardware.detector import HardwareDetector
from shared.local_runtime.adapters.llama_cpp import LlamaCppRuntime
from shared.local_runtime.adapters.mlx import MLXRuntime
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.provider import LocalAIProvider
from shared.providers.base import Capability, ModelProvider
from shared.providers.gemini import GEMINI_BASE_URL, GeminiProvider
from shared.providers.openai_compatible import OpenAICompatibleProvider
from shared.providers.resilience import FallbackProvider, ResilientProvider

logger = logging.getLogger("monta.registry")


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    monta_offline_mode: bool = False
    monta_text_providers: str = ""
    monta_vision_providers: str = ""
    monta_arbitration: str = "failover"
    monta_judge_providers: str = ""
    monta_provider_max_concurrency: int = 16
    monta_provider_max_attempts: int = 3

    # Local AI Runtime Settings
    ollama_base_url: str = "http://localhost:11434"
    ollama_text_model: str = "qwen2.5:7b"
    ollama_vision_model: str = "qwen2-vl:7b"

    llama_cpp_base_url: str = "http://localhost:8080"
    llama_cpp_model: str = "default"

    # Optional Cloud Providers
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = GEMINI_BASE_URL

    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    qwen_text_model: str = "qwen-plus"
    qwen_vl_model: str = "qwen-vl-max"

    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    openai_compat_vision: bool = False


@dataclass
class ProviderBundle:
    text: ModelProvider | None
    vision: ModelProvider | None
    text_candidates: list[ModelProvider] = field(default_factory=list)
    vision_candidates: list[ModelProvider] = field(default_factory=list)
    judge: ModelProvider | None = None
    arbitration: str = "failover"
    is_offline: bool = False

    @property
    def text_for_extraction(self) -> ModelProvider | list[ModelProvider] | None:
        """What Layer 3 should consult: every candidate under consensus, else the failover chain."""
        if self.arbitration == "consensus" and len(self.text_candidates) > 1:
            return self.text_candidates
        return self.text

    @property
    def vision_for_analysis(self) -> ModelProvider | list[ModelProvider] | None:
        if self.arbitration == "consensus" and len(self.vision_candidates) > 1:
            return self.vision_candidates
        return self.vision

    async def aclose(self) -> None:
        everything = (self.text, self.vision, self.judge, *self.text_candidates, *self.vision_candidates)
        for p in {id(p): p for p in everything if p is not None}.values():
            await p.aclose()


def _spec_keys(spec: str) -> list[str]:
    """Provider keys from a comma-separated spec."""
    return [k.strip() for k in spec.split("#", 1)[0].split(",") if k.strip()]


CLOUD_PROVIDERS = {"gemini", "qwen", "qwen_vl"}


def _build_one(key: str, s: ProviderSettings, *, vision: bool) -> ModelProvider:
    key = key.strip().lower()

    if s.monta_offline_mode and key in CLOUD_PROVIDERS:
        raise ValueError(
            f"Provider '{key}' is a cloud service, but MONTA_OFFLINE_MODE is enabled. "
            f"Use local providers ('ollama', 'llama_cpp', 'mlx') in offline mode."
        )

    # Local AI Runtime Adapters
    if key in ("ollama", "local", "ollama_text"):
        runtime = OllamaRuntime(base_url=s.ollama_base_url)
        model = s.ollama_vision_model if vision else s.ollama_text_model
        return LocalAIProvider(runtime=runtime, model=model, vision=vision)

    if key in ("ollama_vl", "ollama_vision"):
        runtime = OllamaRuntime(base_url=s.ollama_base_url)
        return LocalAIProvider(runtime=runtime, model=s.ollama_vision_model, vision=True)

    if key == "llama_cpp":
        runtime = LlamaCppRuntime(base_url=s.llama_cpp_base_url)
        return LocalAIProvider(runtime=runtime, model=s.llama_cpp_model, vision=vision)

    if key == "mlx":
        runtime = MLXRuntime()
        return LocalAIProvider(runtime=runtime, model="mlx-default", vision=vision)

    # Optional Cloud Providers
    if key == "gemini":
        return GeminiProvider(api_key=s.gemini_api_key, model=s.gemini_model, base_url=s.gemini_base_url)
    if key == "qwen":
        return OpenAICompatibleProvider(name="qwen", base_url=s.qwen_base_url, api_key=s.qwen_api_key, model=s.qwen_text_model)
    if key == "qwen_vl":
        return OpenAICompatibleProvider(name="qwen_vl", base_url=s.qwen_base_url, api_key=s.qwen_api_key, model=s.qwen_vl_model, vision=True)
    if key == "openai_compatible":
        if not (s.openai_compat_base_url and s.openai_compat_model):
            raise ValueError("openai_compatible needs OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_MODEL")
        return OpenAICompatibleProvider(
            name="openai_compatible", base_url=s.openai_compat_base_url, api_key=s.openai_compat_api_key,
            model=s.openai_compat_model, vision=s.openai_compat_vision,
        )
    raise ValueError(f"unknown provider '{key}'")


def _candidates(spec: str, s: ProviderSettings, *, vision: bool) -> list[ModelProvider]:
    out = []
    for key in _spec_keys(spec):
        p = _build_one(key, s, vision=vision)
        if vision and Capability.VISION not in p.capabilities:
            raise ValueError(f"provider '{key}' cannot be used for vision")
        out.append(ResilientProvider(p, max_attempts=s.monta_provider_max_attempts, max_concurrency=s.monta_provider_max_concurrency))
    return out


def _chain(spec: str, s: ProviderSettings, *, vision: bool) -> ModelProvider | None:
    keys = _spec_keys(spec)
    if not keys:
        return None
    providers: list[ModelProvider] = []
    for key in keys:
        p = _build_one(key, s, vision=vision)
        if vision and Capability.VISION not in p.capabilities:
            raise ValueError(f"provider '{key}' cannot be used for vision")
        providers.append(
            ResilientProvider(p, max_attempts=s.monta_provider_max_attempts, max_concurrency=s.monta_provider_max_concurrency)
        )
    return providers[0] if len(providers) == 1 else FallbackProvider(providers)


def build_providers(settings: ProviderSettings | None = None) -> ProviderBundle:
    """Construct the configured providers. Misconfiguration raises at startup, not mid-request."""
    s = settings or ProviderSettings()
    if s.monta_arbitration not in ("failover", "consensus"):
        raise ValueError("MONTA_ARBITRATION must be 'failover' or 'consensus'")
    consensus = s.monta_arbitration == "consensus"
    return ProviderBundle(
        text=_chain(s.monta_text_providers, s, vision=False),
        vision=_chain(s.monta_vision_providers, s, vision=True),
        text_candidates=_candidates(s.monta_text_providers, s, vision=False) if consensus else [],
        vision_candidates=_candidates(s.monta_vision_providers, s, vision=True) if consensus else [],
        judge=_chain(s.monta_judge_providers, s, vision=False),
        arbitration=s.monta_arbitration,
        is_offline=s.monta_offline_mode,
    )

