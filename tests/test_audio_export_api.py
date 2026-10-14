"""
MONTA — Audio, Export & Render API Test Suite
================================================
Tests endpoints in:
- backend/app/api/v1/audio.py
- backend/app/api/v1/export.py
- backend/app/api/v1/render.py
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.router import api_router
from fastapi import FastAPI


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_audio_generate_endpoint(test_app):
    """Test POST /api/v1/audio/generate."""
    with patch("services.audio_studio.music_generator.MusicGenerator.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "/tmp/test_music.wav"

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/audio/generate",
                json={
                    "project_id": "proj_123",
                    "prompt": "Action trailer upbeat",
                    "duration_s": 25.0,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "complete"
            assert data["project_id"] == "proj_123"
            assert data["mood"] == "hype"  # Inferred from 'upbeat'/'action'
            assert data["duration_s"] == 25.0


@pytest.mark.asyncio
async def test_audio_sfx_endpoint(test_app):
    """Test POST /api/v1/audio/sfx."""
    with patch("services.audio_studio.sound_fx.SoundFX.generate_fx", new_callable=AsyncMock) as mock_sfx:
        mock_sfx.return_value = "/tmp/whoosh.wav"

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/audio/sfx",
                json={
                    "fx_type": "whoosh",
                    "duration_s": 1.5,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "complete"
            assert data["fx_type"] == "whoosh"
            assert data["duration_s"] == 1.5


@pytest.mark.asyncio
async def test_audio_tempo_match_endpoint(test_app):
    """Test POST /api/v1/audio/tempo-match."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/audio/tempo-match?project_id=proj_abc&target_bpm=128"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "synchronized"
        assert data["target_bpm"] == 128


@pytest.mark.asyncio
async def test_audio_voiceover_endpoint(test_app):
    """Test POST /api/v1/audio/voiceover."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/audio/voiceover",
                json={
                    "text": "This is a dramatic video opening.",
                    "voice": "deep_narrator",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "complete"
            assert "audio_path" in data
