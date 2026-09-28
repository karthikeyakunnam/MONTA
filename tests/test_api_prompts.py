"""Layer 3 HTTP surface: /prompts/analyze returns the full explainable IntentAnalysis."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.v1.prompts import get_intent_engine, router  # noqa: E402
from services.prompt_engine import IntentEngine  # noqa: E402


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/prompts")
    app.dependency_overrides[get_intent_engine] = lambda: IntentEngine()
    return TestClient(app)


def test_analyze_returns_explained_intent():
    r = client().post("/prompts/analyze", json={
        "prompt": "make this feel like nike ad slow start then huge motivation ending use dark colors and aggressive cuts",
        "project_id": "p1",
    })
    assert r.status_code == 200
    body = r.json()
    intent = body["intent"]
    assert intent["pace"]["value"] == "aggressive" and intent["pace"]["reasoning"]
    assert intent["schema_version"] == "intent.v1"
    assert {m["field"] for m in intent["missing"]} == {"target_platform"}
    assert body["needs_clarification"] is False


def test_analyze_validates_input():
    assert client().post("/prompts/analyze", json={"prompt": "x" * 5000, "project_id": "p"}).status_code == 422
    assert client().post("/prompts/analyze", json={"prompt": "hi"}).status_code == 422
