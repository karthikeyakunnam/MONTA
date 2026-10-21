"""An inferred transition cannot silently shorten an explicitly timed edit."""

from types import SimpleNamespace

import pytest

from orchestration.pipeline import MontaPipeline
from services.timeline_generator.translator import StoryToTimelineTranslator
from shared.contracts.timeline import TimelineIR, TransitionType, VideoSegmentIR


def _parts(tmp_path, *, explicit_transition=False):
    source = tmp_path / "source.mp4"
    source.touch()
    segments = tuple(VideoSegmentIR(
        segment_id=f"shot-{index}", clip_id="source", source_path=str(source),
        source_in_ms=index * 1000, source_out_ms=(index + 1) * 1000,
        timeline_start_ms=index * 1000, timeline_end_ms=(index + 1) * 1000,
        transition_in=TransitionType.CROSSFADE if index else TransitionType.CUT,
        transition_in_ms=250 if index else 0,
    ) for index in range(2))
    timeline = TimelineIR(project_id="test", video_segments=segments, total_duration_ms=2000)
    directives = [SimpleNamespace(category="transition", priority="required")] if explicit_transition else []
    pack = SimpleNamespace(video_metadata=[SimpleNamespace(clip_id="source", duration_s=2)],
                           intent=SimpleNamespace(creative_brief=SimpleNamespace(directives=directives)))
    story = SimpleNamespace(requested_duration_s=2)
    return timeline, pack, story


def test_inferred_crossfade_yields_to_explicit_duration(tmp_path, monkeypatch):
    timeline, pack, story = _parts(tmp_path)
    monkeypatch.setattr(StoryToTimelineTranslator, "translate", lambda **_kwargs: timeline)
    result = MontaPipeline.generate_timeline_ir(object(), story, pack)
    assert result.render_duration_s == 2
    assert all(segment.transition_in == TransitionType.CUT and segment.transition_in_ms == 0
               for segment in result.video_segments)


def test_explicit_crossfade_requires_replanned_duration(tmp_path, monkeypatch):
    timeline, pack, story = _parts(tmp_path, explicit_transition=True)
    monkeypatch.setattr(StoryToTimelineTranslator, "translate", lambda **_kwargs: timeline)
    with pytest.raises(ValueError, match="after transition overlap"):
        MontaPipeline.generate_timeline_ir(object(), story, pack)
