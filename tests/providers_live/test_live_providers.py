"""
Live provider contract tests — REAL network calls to Qwen, Qwen-VL and Gemini.

Run explicitly (never in the default PR suite):

    GEMINI_API_KEY=... QWEN_API_KEY=... python -m pytest -m live tests/providers_live -v

Each test skips (with the reason) when its credentials are absent, so a
partial key set still exercises what it can. Results should be recorded in
``docs/layers-3-7.md#real-model-validation`` with date and model versions.
"""

import os
import struct
import zlib

import pytest

from orchestration.agents.intelligence.vision import VisionAnalyzer
from services.prompt_engine.llm_extractor import LLMIntent, build_messages
from shared.arbitration import INTENT_POLICY, ReliabilityTable, run_arbitrated
from shared.contracts.clip import TechnicalMetadata
from shared.providers.base import GenerationConfig, ImagePart, Message
from shared.providers.errors import ProviderAuthError, ProviderTimeoutError
from shared.providers.gemini import GeminiProvider
from shared.providers.openai_compatible import OpenAICompatibleProvider
from shared.providers.resilience import FallbackProvider
from shared.providers.structured import generate_structured

pytestmark = pytest.mark.live

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
QWEN_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_URL = os.getenv("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")

needs_gemini = pytest.mark.skipif(not GEMINI_KEY, reason="GEMINI_API_KEY not set")
needs_qwen = pytest.mark.skipif(not QWEN_KEY, reason="QWEN_API_KEY not set")
needs_both = pytest.mark.skipif(not (GEMINI_KEY and QWEN_KEY), reason="needs GEMINI_API_KEY and QWEN_API_KEY")

PROMPT = "make this feel like nike ad slow start then huge motivation ending use dark colors and aggressive cuts"


def gemini():
    return GeminiProvider(api_key=GEMINI_KEY, model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))


def qwen():
    return OpenAICompatibleProvider(name="qwen", base_url=QWEN_URL, api_key=QWEN_KEY, model=os.getenv("QWEN_TEXT_MODEL", "qwen-plus"))


def qwen_vl():
    return OpenAICompatibleProvider(name="qwen_vl", base_url=QWEN_URL, api_key=QWEN_KEY,
                                    model=os.getenv("QWEN_VL_MODEL", "qwen-vl-max"), vision=True)


def _png(width=64, height=64) -> bytes:
    """A real PNG (red/blue split) built without imaging libraries."""
    rows = b"".join(b"\x00" + (b"\xff\x00\x00" * (width // 2) + b"\x00\x00\xff" * (width // 2)) for _ in range(height))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


META = TechnicalMetadata(clip_id="live", path="/live.mp4", duration_s=4, fps=30, width=64, height=64, codec="h264",
                         has_audio=False, file_size_bytes=1, fingerprint="live")


@pytest.mark.parametrize("factory", [pytest.param(gemini, marks=needs_gemini), pytest.param(qwen, marks=needs_qwen)])
async def test_text_intent_schema_compliance(factory):
    provider = factory()
    try:
        result = await generate_structured(provider, build_messages(PROMPT), LLMIntent,
                                           config=GenerationConfig(temperature=0, json_output=True, timeout_s=30))
        assert result.value.pace is not None
        assert result.response.input_tokens > 0 and result.response.latency_ms > 0
        print(f"{provider.model_id}: attempts={len(result.attempts)} latency={result.response.latency_ms:.0f}ms "
              f"tokens={result.response.input_tokens}+{result.response.output_tokens}")
    finally:
        await provider.aclose()


@pytest.mark.parametrize("factory", [pytest.param(gemini, marks=needs_gemini), pytest.param(qwen_vl, marks=needs_qwen)])
async def test_vision_observation_with_real_image(factory):
    provider = factory()
    try:
        obs = await VisionAnalyzer(provider, timeout_s=60).observe(META, [ImagePart(_png(), "image/png")], [0.0])
        assert obs.reasoning and obs.story_role_candidates
    finally:
        await provider.aclose()


@pytest.mark.parametrize("factory", [pytest.param(gemini, marks=needs_gemini), pytest.param(qwen, marks=needs_qwen)])
async def test_timeout_is_reported_as_retryable(factory):
    provider = factory()
    try:
        with pytest.raises(ProviderTimeoutError) as info:
            await provider.generate([Message.user("Write 2000 words about editing.")],
                                    GenerationConfig(max_output_tokens=2048, timeout_s=0.001))
        assert info.value.retryable
    finally:
        await provider.aclose()


@needs_gemini
async def test_invalid_key_is_auth_error_not_retry():
    bad = GeminiProvider(api_key="invalid-key-for-test", model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    try:
        with pytest.raises(ProviderAuthError):
            await bad.generate([Message.user("hi")])
    finally:
        await bad.aclose()


@needs_gemini
async def test_failover_from_broken_primary_to_real_secondary():
    broken = OpenAICompatibleProvider(name="broken", base_url="https://127.0.0.1:9/v1", model="none")
    chain = FallbackProvider([broken, gemini()])
    try:
        r = await chain.generate([Message.user("Reply with the single word: ok")], GenerationConfig(timeout_s=30))
        assert "ok" in r.text.lower()
    finally:
        await chain.aclose()


@needs_both
async def test_arbitration_between_qwen_and_gemini():
    providers = [qwen(), gemini()]
    try:
        value, summary = await run_arbitrated(providers, build_messages(PROMPT), LLMIntent, INTENT_POLICY, ReliabilityTable(),
                                              config=GenerationConfig(temperature=0, json_output=True, timeout_s=30))
        assert len(summary.models_succeeded) >= 1
        assert all(r.rationale for r in summary.records)
        print(f"consensus={summary.consensus} failures={summary.failures}")
    finally:
        for p in providers:
            await p.aclose()
