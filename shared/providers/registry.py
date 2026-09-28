"""
MONTA — Provider Registry
===========================
Builds text and vision providers from environment configuration. This is the
only module that knows which vendors exist.

Environment:
    MONTA_TEXT_PROVIDERS    ordered fallback chain, e.g. "qwen,gemini" (empty = disabled)
    MONTA_VISION_PROVIDERS  ordered fallback chain, e.g. "qwen_vl,gemini" (empty = disabled)
    MONTA_ARBITRATION       "failover" (default: first healthy provider answers) or
                            "consensus" (all listed providers answer; outputs are arbitrated)
    MONTA_JUDGE_PROVIDERS   provider(s) for the LLM Story Judge (must differ from the refiner's)
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL
    QWEN_BASE_URL, QWEN_API_KEY, QWEN_TEXT_MODEL, QWEN_VL_MODEL
    OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_MODEL, OPENAI_COMPAT_VISION
    MONTA_PROVIDER_MAX_CONCURRENCY, MONTA_PROVIDER_MAX_ATTEMPTS
"""

from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.providers.base import Capability, ModelProvider
from shared.providers.gemini import GEMINI_BASE_URL, GeminiProvider
from shared.providers.openai_compatible import OpenAICompatibleProvider
from shared.providers.resilience import FallbackProvider, ResilientProvider


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    monta_text_providers: str = ""
    monta_vision_providers: str = ""
    monta_arbitration: str = "failover"
    monta_judge_providers: str = ""
    monta_provider_max_concurrency: int = 16
    monta_provider_max_attempts: int = 3

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
    """Provider keys from a comma-separated spec.

    Drops an inline ``#`` comment before splitting. A ``.env`` line written as
    ``MONTA_JUDGE_PROVIDERS=            # must differ from the refiner`` keeps the comment in
    the value — pydantic-settings only strips comments on their own line — so the comment
    text arrived here as a provider name and failed with ``unknown provider '# must differ…'``.
    A configuration comment must never be mistaken for configuration.
    """
    return [k.strip() for k in spec.split("#", 1)[0].split(",") if k.strip()]


def _build_one(key: str, s: ProviderSettings, *, vision: bool) -> ModelProvider:
    key = key.strip().lower()
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
    )
