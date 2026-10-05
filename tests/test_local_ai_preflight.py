"""
Tests for LocalAIPreflight diagnostic runner.
"""

import pytest

from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.preflight import LocalAIPreflight
from shared.providers.registry import ProviderSettings


@pytest.mark.asyncio
async def test_preflight_on_live_or_mock_runtime():
    settings = ProviderSettings(
        monta_offline_mode=True,
        ollama_base_url="http://localhost:11434",
        ollama_text_model="qwen2.5:0.5b",
    )
    report = await LocalAIPreflight.run(settings)

    assert report.runtime_name == "ollama"
    assert report.hardware_profile.total_ram_gb > 0
    assert report.offline_mode_enforced is True
    # Verify summary string prints without crashing
    summary_text = report.summary()
    assert "LOCAL AI PREFLIGHT" in summary_text
    print("\n" + summary_text)
