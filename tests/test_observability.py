"""Observability — every request traceable end-to-end; metrics, JSON logs, provider accounting."""

import io
import json
import logging

import httpx

from shared.observability.context import bind, correlation, hash_user, new_trace
from shared.observability.logging import JsonFormatter
from shared.observability.metrics import REGISTRY, Registry
from shared.observability.tracing import SPANS, span
from shared.providers.base import Message
from shared.providers.gemini import GeminiProvider
from tests.test_pipeline import GYM, NIKE, SyntheticMediaBackend, build, sources


async def test_pipeline_run_is_one_trace_end_to_end():
    SPANS.clear()
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    await build(media).run(project_id="proj-1", user_id="user-1", prompt=NIKE, clips=sources(GYM))
    spans = SPANS.all()
    root = next(s for s in spans if s.name == "pipeline.run")
    trace = [s for s in spans if s.trace_id == root.trace_id]
    names = {s.name for s in trace}
    assert {"pipeline.run", "stage.interpret", "stage.probe", "stage.compose", "stage.execute", "intent.analyze",
            "director.execute", "director.task", "clip.analyze", "story.design"} <= names
    assert len({s.trace_id for s in spans}) == 1, "all spans of the run share one trace id"
    task_spans = [s for s in trace if s.name == "director.task"]
    assert all(s.correlation.get("plan_id") and s.correlation.get("task_id") for s in task_spans)
    assert all(s.correlation.get("project_id") == "proj-1" for s in trace)
    assert all(s.correlation.get("user_ref") == hash_user("user-1") and "user-1" not in json.dumps(s.correlation) for s in trace)
    by_id = {s.span_id for s in trace}
    assert all(s.parent_id in by_id for s in trace if s.name != "pipeline.run")


async def test_pipeline_metrics_are_recorded():
    before = REGISTRY.get("monta_task_total").value(agent="story_architect", status="succeeded")
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    await build(media).run(project_id="p", user_id="u", prompt=NIKE, clips=sources(GYM))
    assert REGISTRY.get("monta_task_total").value(agent="story_architect", status="succeeded") == before + 1
    assert REGISTRY.get("monta_stage_duration_seconds").count(stage="execute", outcome="ok") >= 1
    assert REGISTRY.get("monta_clip_analysis_total").value(outcome="ok") >= 1
    text = REGISTRY.render_prometheus()
    assert "# TYPE monta_task_total counter" in text and 'monta_story_pattern_selected_total{pattern="' in text


async def test_provider_metrics_tokens_latency_and_redaction():
    body = {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3}}
    ok = GeminiProvider(api_key="k", model="obs-model", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    tokens = REGISTRY.get("monta_provider_tokens_total")
    before = tokens.value(provider="gemini", model="obs-model", direction="input")
    SPANS.clear()
    with new_trace(project_id="p"):
        await ok.generate([Message.user("x")])
    assert tokens.value(provider="gemini", model="obs-model", direction="input") == before + 12
    assert REGISTRY.get("monta_provider_calls_total").value(provider="gemini", model="obs-model", outcome="ok") >= 1
    assert any(s.name == "provider.call" and s.attributes["outcome"] == "ok" for s in SPANS.all())

    leaky = GeminiProvider(api_key="k", model="obs-model", client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="echo: SECRET PROMPT TEXT"))))
    try:
        await leaky.generate([Message.user("x")])
    except Exception as e:
        assert "SECRET PROMPT TEXT" not in str(e), "vendor error bodies must not be echoed"


def test_json_logs_carry_correlation_ids():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("monta.test_obs")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    with new_trace(project_id="proj-9", user_id="alice"), bind(task_id="analyze_clip:c1"):
        log.info("hello", extra={"clip_count": 3})
    record = json.loads(stream.getvalue().strip())
    assert record["project_id"] == "proj-9" and record["task_id"] == "analyze_clip:c1" and record["trace_id"]
    assert record["user_ref"] == hash_user("alice") and "alice" not in stream.getvalue()
    assert record["clip_count"] == 3
    assert correlation() == {}


def test_metric_registry_semantics():
    r = Registry()
    c = r.counter("t_total", "t", ["a"])
    c.inc(a="x")
    c.inc(2, a="x")
    h = r.histogram("t_seconds", "t", ["a"], buckets=(1, 5))
    h.observe(0.5, a="x")
    h.observe(3, a="x")
    assert c.value(a="x") == 3 and h.count(a="x") == 2 and h.sum(a="x") == 3.5
    text = r.render_prometheus()
    assert 't_seconds_bucket{a="x",le="1"} 1' in text and 't_seconds_bucket{a="x",le="+Inf"} 2' in text
    try:
        c.inc(b="y")
    except ValueError:
        pass
    else:
        raise AssertionError("label mismatch must raise")


def test_span_records_errors():
    SPANS.clear()
    try:
        with span("boom"):
            raise RuntimeError("bad")
    except RuntimeError:
        pass
    s = SPANS.all()[-1]
    assert s.status == "error" and "RuntimeError" in s.error
