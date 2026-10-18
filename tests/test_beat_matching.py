"""Ordered scene windows are grounded in multiple source-local observations."""

import pytest

from orchestration.agents.story.beat_matching import BeatMatchError, match_scene_beats
from shared.contracts.base import revalidate
from shared.contracts.clip import FrameEvidence
from shared.contracts.intent import SceneBeatRequirement
from tests.conftest import make_clip


def _beat(index, text):
    return SceneBeatRequirement(order=index, description=text, source_span=text)


def _clip(clip_id, observations, duration=25):
    return revalidate(
        make_clip(clip_id, 4, duration=duration),
        frame_evidence=tuple(FrameEvidence(time_s=at, description=description, model_id="local:test")
                             for at, description in observations),
    )


def test_ordered_beats_use_distinct_source_windows_and_not_clip_names():
    clips = {
        "podium_in_filename": _clip("podium_in_filename", [
            (2, "A banner beside the event building"),
            (5, "A sign on the building wall"),
            (14, "A group seated by the stage and audience"),
            (18, "People stand on stage beside a screen"),
        ]),
        "ordinary": _clip("ordinary", [
            (3, "People walking through the entrance"),
            (7, "Arrivals walking into the hall"),
            (15, "A speaker at a podium with a microphone"),
            (19, "A man speaking at a podium"),
        ]),
    }
    beats = tuple(_beat(i, value) for i, value in enumerate((
        "venue signage", "arrivals", "stage interaction", "podium speaker")))
    selected = match_scene_beats(beats, clips, 20)
    assert [item.clip_id for item in selected] == [
        "podium_in_filename", "ordinary", "podium_in_filename", "ordinary"]
    assert all(item.source_out_s <= clips[item.clip_id].duration_s for item in selected)
    assert all(item.evidence_times_s[0] != item.evidence_times_s[1] for item in selected)
    assert selected[0].source_out_s <= selected[2].source_in_s
    assert selected[1].source_out_s <= selected[3].source_in_s


def test_missing_or_single_unverified_scene_observation_blocks_selection():
    clips = {"podium.mp4": _clip("podium.mp4", [
        (2, "A banner outside the building"), (5, "A sign by the building"),
        (14, "A person at a podium"), (19, "A wall with no visible speaker"),
    ])}
    with pytest.raises(BeatMatchError, match="No two timestamped source observations support scene beat 2"):
        match_scene_beats((_beat(0, "venue signage"), _beat(1, "podium speaker")), clips, 10)


def test_overlapping_ranges_are_rejected_even_when_descriptions_repeat():
    clips = {"one": _clip("one", [
        (4, "A stage with a microphone"), (5, "A speaker on stage"),
    ], duration=8)}
    with pytest.raises(BeatMatchError, match="no nonoverlapping source windows"):
        match_scene_beats((_beat(0, "stage"), _beat(1, "speaker")), clips, 10)


def test_reused_source_keeps_chronology_across_intervening_clip():
    clips = {
        "a": _clip("a", [
            (2, "An event building with chairs"), (5, "A venue hall with seats"),
            (11, "A person on stage"), (14, "The stage has an audience"),
            (29, "A sign beside an event building"), (32, "A banner outside the building"),
        ], duration=40),
        "b": _clip("b", [
            (2, "Two people greet each other"), (5, "A smiling greeting"),
            (12, "A speech at a podium"), (16, "A speaker at the podium"),
        ], duration=20),
    }
    beats = tuple(_beat(i, text) for i, text in enumerate((
        "venue signage", "greetings", "stage", "podium speaker")))
    selected = match_scene_beats(beats, clips, 20)
    assert selected[0].clip_id == selected[2].clip_id == "a"
    assert selected[0].source_out_s <= selected[2].source_in_s
    assert selected[0].source_in_s < 10


def test_real_failed_greetings_descriptions_recover_from_one_wrong_model_observation():
    # From the failed IMG source pass and the denser local probe, 2026-10-19.
    # The model called a nearby gesture a photo and never used "greeting".
    clips = {"clip_ad54481e2bb1": _clip("clip_ad54481e2bb1", [
        (2.5, "A group of people gathered near a building with a camera"),
        (3.5, "A man has his hands clasped together in front of other men"),
        (5.5, "Men and women are standing close, engaged in conversation"),
        (7.405, "Two men are looking at each other, possibly in conversation"),
        (9.5, "A man holds his face while another person takes a photo"),
        (16.29, "A man standing at a podium giving a speech"),
        (19.252, "A man standing at a podium delivering a speech"),
    ], duration=20.733)}
    selected = match_scene_beats((_beat(0, "greetings"),), clips, 6.25)
    assert selected[0].evidence_basis == "gesture_and_interaction"
    assert selected[0].evidence_times_s == (3.5, 5.5)
    assert selected[0].source_in_s <= 3.5 < 5.5 < selected[0].source_out_s


def test_generic_crowd_or_single_gesture_cannot_substantiate_greeting():
    clips = {"source": _clip("source", [
        (2, "A man has his hands clasped"),
        (4, "A crowd standing near a window"),
        (7, "A man looking at a camera"),
    ])}
    with pytest.raises(BeatMatchError, match="No two timestamped source observations") as error:
        match_scene_beats((_beat(0, "greetings"),), clips, 6)
    assert "source: 3 timestamped observations" in str(error.value)
    assert "gesture at 2.00s" in str(error.value)
    assert "interpersonal action at none" in str(error.value)
