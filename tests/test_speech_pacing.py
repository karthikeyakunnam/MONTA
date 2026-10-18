"""Deterministic transcript fixtures; these tests do not validate ASR accuracy."""

import json

import pytest

from services.timeline_generator.manual import ManualTimeline, apply_manual_timeline
from shared.contracts.timeline import TimelineIR, TransitionType, VideoSegmentIR
from speech.pacing import align_timeline_to_speech
from speech.transcriber import (LocalTranscriptionFailed, LocalWhisperModelCorrupt,
                                LocalWhisperModelIncompatible, WhisperTranscriber)


def test_worker_config_selects_explicit_base_without_fallback(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("WHISPER_MODEL_SIZE", "base")
    base = Settings(_env_file=None)
    assert base.WHISPER_MODEL_SIZE == "base"
    assert WhisperTranscriber(base.WHISPER_MODEL_SIZE).model_path().name == "base.pt"
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "large-v3")
    large = Settings(_env_file=None)
    assert large.WHISPER_MODEL_SIZE == "large-v3"
    assert WhisperTranscriber(large.WHISPER_MODEL_SIZE).model_path().name == "large-v3.pt"


def test_manual_speech_alignment_requires_explicit_opt_in():
    from app.schemas.project import EditOptions

    shot = {"clip_id": "source", "source_in_ms": 0, "source_out_ms": 22500}
    default = EditOptions.model_validate({"manual_timeline": {"shots": [shot]}})
    opted = EditOptions.model_validate({"manual_timeline": {"shots": [shot], "speech_align": True}})
    assert default.manual_timeline.speech_align is False
    assert opted.manual_timeline.speech_align is True
    assert opted.model_dump(mode="json")["manual_timeline"]["speech_align"] is True


def _timeline(path: str, *, end: int = 4000) -> TimelineIR:
    return TimelineIR(project_id="fixture", video_segments=(
        VideoSegmentIR(segment_id="speech", clip_id="source", source_path=path,
                       source_in_ms=750, source_out_ms=4750,
                       timeline_start_ms=0, timeline_end_ms=end),
    ), total_duration_ms=end)


def _fixture():
    return {"segments": [
        {"start": 0.5, "end": 1.0, "text": "Hello",
         "words": [{"start": 0.5, "end": 1.0, "text": "Hello"}]},
        {"start": 1.25, "end": 2.5, "text": "Welcome everyone.",
         "words": [{"start": 1.25, "end": 1.6, "text": "Welcome"},
                   {"start": 1.7, "end": 2.5, "text": "everyone."}]},
        {"start": 3.0, "end": 4.5, "text": "Thank you.",
         "words": [{"start": 3.0, "end": 3.5, "text": "Thank"},
                   {"start": 3.6, "end": 4.5, "text": "you."}]},
    ]}


def test_source_boundaries_and_caption_offsets_use_fixture_not_inference():
    timeline = _timeline("/source.mov")
    result = align_timeline_to_speech(timeline, {"/source.mov": _fixture()}, {"source": 5000})
    shot = result.timeline.video_segments[0]
    assert (shot.source_in_ms, shot.source_out_ms) == (500, 4500)
    assert result.timeline.total_duration_ms == 4000
    assert [(c.text, c.start_ms, c.end_ms, c.source_start_ms) for c in result.timeline.caption_cues] == [
        ("Hello", 0, 500, 500), ("Welcome everyone.", 750, 2000, 1250),
        ("Thank you.", 2500, 4000, 3000),
    ]
    assert result.status == "aligned"


def test_midword_cut_moves_to_safe_word_edge_and_unresolved_is_reported():
    transcript = {"segments": [{"start": 0, "end": 4, "text": "a phrase",
                                "words": [{"start": 0.6, "end": 1.1, "text": "phrase"}]}]}
    timeline = _timeline("/source.mov")
    result = align_timeline_to_speech(timeline, {"/source.mov": transcript}, {"source": 5000})
    assert result.timeline.video_segments[0].source_in_ms == 600
    assert result.decisions[0]["end_boundary"] == "unresolved"
    assert result.status == "partial"
    assert [cue.text for cue in result.timeline.caption_cues] == ["phrase"]


def test_real_case_fixture_keeps_final_phrase_with_bounded_speed_change():
    # Timestamp fixture reproduces the observed base transcript boundary:
    # the 22.5s out point lies inside a 22.48–22.70s word; phrase ends 23.64s.
    source = "/source.mov"
    timeline = TimelineIR(project_id="fixture", video_segments=(
        VideoSegmentIR(segment_id="speech", clip_id="source", source_path=source,
                       source_in_ms=0, source_out_ms=22500,
                       timeline_start_ms=0, timeline_end_ms=22500),
    ), total_duration_ms=22500)
    transcript = {"segments": [
        {"start": 0.0, "end": 3.8, "text": "Opening phrase."},
        {"start": 19.68, "end": 23.64, "text": "Complete final phrase.",
         "words": [{"start": 22.48, "end": 22.7, "text": "a"}]},
    ]}
    result = align_timeline_to_speech(timeline, {source: transcript}, {"source": 47248})
    assert result.timeline.video_segments[0].source_out_ms == 23640
    assert result.timeline.total_duration_ms == 22500
    assert result.decisions[0]["end_boundary"] == "final_phrase"
    assert result.decisions[0]["playback_speed_after"] == pytest.approx(1.0507, abs=0.0001)
    assert result.status == "aligned"
    assert result.timeline.caption_cues[-1].end_ms == 22500


def test_word_safe_cut_does_not_claim_phrase_alignment():
    source = "/source.mov"
    timeline = TimelineIR(project_id="fixture", video_segments=(
        VideoSegmentIR(segment_id="speech", clip_id="source", source_path=source,
                       source_in_ms=0, source_out_ms=22500,
                       timeline_start_ms=0, timeline_end_ms=22500),
    ), total_duration_ms=22500)
    transcript = {"segments": [{"start": 0, "end": 25, "text": "Long phrase",
                                "words": [{"start": 22.48, "end": 22.7, "text": "a"}]}]}
    result = align_timeline_to_speech(timeline, {source: transcript}, {"source": 26000})
    assert result.timeline.video_segments[0].source_out_ms == 22700
    assert result.status == "partial"
    assert result.decisions[0]["end_boundary"] == "word"


def test_missing_transcript_preserves_every_cut():
    timeline = _timeline("/source.mov")
    result = align_timeline_to_speech(timeline, {}, {"source": 5000})
    assert result.status == "unavailable"
    assert result.timeline.video_segments == timeline.video_segments


def test_manual_source_window_is_preserved_while_source_captions_are_mapped():
    timeline = _timeline("/source.mov")
    result = align_timeline_to_speech(timeline, {"/source.mov": _fixture()},
                                      {"source": 5000}, preserve_cuts=True)
    assert result.status == "user_override"
    assert result.timeline.video_segments == timeline.video_segments
    assert [cue.text for cue in result.timeline.caption_cues] == [
        "Welcome everyone.", "Thank you."]


def test_transition_overlap_maps_caption_to_encoded_time():
    first = VideoSegmentIR(segment_id="a", clip_id="a", source_path="/a.mov",
                           source_in_ms=0, source_out_ms=2000,
                           timeline_start_ms=0, timeline_end_ms=2000)
    second = VideoSegmentIR(segment_id="b", clip_id="b", source_path="/b.mov",
                            source_in_ms=0, source_out_ms=2000,
                            timeline_start_ms=2000, timeline_end_ms=4000,
                            transition_in=TransitionType.CROSSFADE, transition_in_ms=250)
    timeline = TimelineIR(project_id="fixture", video_segments=(first, second), total_duration_ms=4000)
    transcripts = {"/b.mov": {"segments": [{"start": 0.5, "end": 1.0, "text": "Next."}]}}
    result = align_timeline_to_speech(timeline, transcripts, {"a": 2000, "b": 2000})
    assert result.timeline.caption_cues[0].start_ms == 2250
    assert result.timeline.caption_cues[0].end_ms == 2750


def test_manual_caption_correction_persists_in_canonical_timeline(tmp_path):
    source = tmp_path / "source.mov"
    source.write_bytes(b"fixture")
    base = TimelineIR(project_id="fixture", video_segments=(
        VideoSegmentIR(segment_id="a", clip_id="source", source_path=str(source),
                       source_in_ms=0, source_out_ms=2000,
                       timeline_start_ms=0, timeline_end_ms=2000),
    ), total_duration_ms=2000)
    manual = ManualTimeline.model_validate({"shots": [{"clip_id": "source", "source_in_ms": 0,
                                                         "source_out_ms": 2000}],
                                            "captions": [{"start": 0.5, "end": 1.5,
                                                          "text": "Corrected words"}]})
    edited = apply_manual_timeline(base, manual, {"source": 2000},
                                   {"source": (str(source), True, None)})
    restored = TimelineIR.model_validate(json.loads(edited.model_dump_json()))
    assert [(c.text, c.origin, c.start_ms, c.end_ms) for c in restored.caption_cues] == [
        ("Corrected words", "user_override", 500, 1500)]


def test_local_checkpoint_errors_are_distinct(tmp_path, monkeypatch):
    import whisper

    model = tmp_path / "model.pt"
    model.write_bytes(b"")
    transcriber = WhisperTranscriber(str(model))
    with pytest.raises(LocalWhisperModelCorrupt, match="empty"):
        transcriber.available_model_path()
    model.write_bytes(b"fixture")

    def incompatible(_path):
        raise KeyError("dims")

    monkeypatch.setattr(whisper, "load_model", incompatible)
    with pytest.raises(LocalWhisperModelIncompatible, match="incompatible"):
        transcriber._get_model()
    monkeypatch.setattr(whisper, "load_model", lambda _path: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(LocalWhisperModelCorrupt, match="cannot be loaded"):
        transcriber._get_model()


@pytest.mark.asyncio
async def test_inference_error_is_not_confused_with_missing_weights(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"fixture")
    transcriber = WhisperTranscriber(str(tmp_path / "model.pt"))

    class BrokenModel:
        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("decoder failure")

    transcriber._model = BrokenModel()
    with pytest.raises(LocalTranscriptionFailed, match="decoder failure"):
        await transcriber.transcribe(str(wav))


@pytest.mark.asyncio
async def test_local_model_is_asked_for_word_timestamps(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"fixture")
    transcriber = WhisperTranscriber(str(tmp_path / "model.pt"))
    options = []

    class FixtureModel:
        def transcribe(self, _path, **kwargs):
            options.append(kwargs)
            return {"text": "Hello", "language": "en", "segments": [
                {"start": 0.5, "end": 1.1, "text": "Hello", "words": [
                    {"start": 0.5, "end": 1.1, "word": " Hello"}]},
                {"start": 2, "end": 2, "text": ""},
            ]}

    transcriber._model = FixtureModel()
    result = await transcriber.transcribe(str(wav))
    assert options == [{"task": "transcribe", "word_timestamps": True}]
    assert result["segments"] == [{"id": 0, "start": 0.5, "end": 1.1,
                                    "text": "Hello", "words": [{"start": 0.5,
                                                                    "end": 1.1, "text": "Hello"}]}]


@pytest.mark.asyncio
async def test_malformed_model_timestamps_fail_without_becoming_captions(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"fixture")
    transcriber = WhisperTranscriber(str(tmp_path / "model.pt"))

    class BadFixtureModel:
        def transcribe(self, *_args, **_kwargs):
            return {"text": "false", "segments": [{"start": float("nan"),
                                                    "end": 1.0, "text": "false"}]}

    transcriber._model = BadFixtureModel()
    with pytest.raises(LocalTranscriptionFailed, match="malformed timestamps"):
        await transcriber.transcribe(str(wav))


@pytest.mark.asyncio
async def test_asr_cache_reuses_source_transcript_and_invalidates_changed_media(tmp_path, monkeypatch):
    import whisper

    model = tmp_path / "model.pt"
    model.write_bytes(b"fixture checkpoint")
    source = tmp_path / "source.mov"
    source.write_bytes(b"first")
    transcriber = WhisperTranscriber(str(model))
    calls = []

    async def fake_asr(path, language=None):
        calls.append(path)
        return {"text": "fixture", "language": "en", "segments": _fixture()["segments"]}

    monkeypatch.setattr(transcriber, "transcribe_video", fake_asr)
    cache = tmp_path / "cache"
    first = await transcriber.transcribe_video_cached(str(source), cache)
    again = await transcriber.transcribe_video_cached(str(source), cache)
    assert first == again and len(calls) == 1
    source.write_bytes(b"second")
    await transcriber.transcribe_video_cached(str(source), cache)
    assert len(calls) == 2
