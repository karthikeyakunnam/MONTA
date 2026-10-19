"""Ordered scene windows are grounded in multiple source-local observations."""

import pytest

from orchestration.agents.story.beat_matching import (
    BeatMatchError, _candidates, _context_candidates, match_scene_beats,
)
from shared.contracts.base import revalidate
from shared.contracts.clip import FrameEvidence
from shared.contracts.intent import SceneBeatRequirement
from tests.conftest import make_clip


def _beat(index, text):
    return SceneBeatRequirement(order=index, description=text, source_span=text)


def _clip(clip_id, observations, duration=25, cuts=()):
    return revalidate(
        make_clip(clip_id, 4, duration=duration),
        frame_evidence=tuple(FrameEvidence(time_s=at, description=description, model_id="local:test")
                             for at, description in observations),
        scene_cut_times_s=cuts,
    )


def test_ordered_beats_use_distinct_source_windows_and_not_clip_names():
    clips = {
        "podium_in_filename": _clip("podium_in_filename", [
            (2, "A banner beside the event building"),
            (5, "A sign on the building wall"),
            (14, "A woman presenting an award to a man on stage"),
            (18, "People speaking together on stage beside a screen"),
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


def test_decorated_entrance_can_extend_signage_beat_only_with_sign_anchor():
    beat = _beat(0, "venue signage")
    clip = _clip("event", [
        (1.5, "Outdoor venue banner beside the road"),
        (3.5, "Poster on the wall outside the venue"),
        (6.5, "A sign above a decorated archway with flowers"),
        (7.5, "People walking through an archway decorated with flowers"),
        (8.2, "Two people walking through a flower decorated archway"),
        (10.5, "Statue beside an unrelated screen"),
        (11.0, "Golden statue in front of the screen"),
    ], duration=12, cuts=(5, 9, 10))
    clips = {"event": clip}
    anchors = _candidates(0, beat, clips, None)
    context = _context_candidates(0, beat, clips, anchors)
    assert anchors and anchors[0].source_out_s <= 5
    assert any(5 < item.source_in_s < item.source_out_s <= 9 for item in context)
    assert all(item.source_out_s <= 10 for item in context)
    assert not any(item.is_anchor for item in context)
    assert _context_candidates(0, beat, clips, []) == []


def test_people_present_near_stage_do_not_prove_stage_interaction():
    clip = _clip("event", [
        (1.0, "People present near a stage with chairs"),
        (1.5, "A crowd of people present in front of the stage"),
    ], duration=2, cuts=())
    with pytest.raises(BeatMatchError, match="no short source shot supported"):
        match_scene_beats((_beat(0, "stage interaction"),), {"event": clip}, 3)


def test_source_blur_excludes_middle_of_sign_shot_without_claiming_scene_cut():
    clip = _clip("event", [(1.5, "Event banner beside road"),
                           (3.6, "Venue sign beside road"),
                           (4.4, "Event poster by the venue")], duration=5.5)
    samples = tuple((round(i * 0.25, 2), 0.002 if 2.75 <= i * 0.25 <= 3 else 0.025)
                    for i in range(22))
    clip = revalidate(clip, sharpness_samples=samples)
    anchors = _candidates(0, _beat(0, "venue signage"), {"event": clip}, None)
    assert anchors
    assert all(not (item.source_in_s < 2.75 < item.source_out_s)
               for item in anchors)
    assert any(item.source_in_s > 3 and item.source_out_s > 4.4 for item in anchors)


def test_stage_person_plus_visible_action_recovers_one_missed_model_description():
    clip = _clip("event", [
        (1.0, "A man standing on stage with a yellow scarf"),
        (1.7, "A person speaking into a microphone in front of the stage"),
    ], duration=2)
    selected, = match_scene_beats((_beat(0, "stage interaction"),), {"event": clip}, 1.84)
    assert selected.evidence_basis == "stage_person_and_action"
    assert selected.source_in_s >= 0
    assert selected.source_out_s <= 2


def test_one_visible_greeting_gesture_with_same_shot_social_context_is_uncertain_anchor():
    clip = _clip("event", [
        (1.0, "A man has his hands clasped in greeting beside a woman"),
        (1.6, "The man stands next to another person in the same group"),
    ], duration=2)
    selected, = match_scene_beats((_beat(0, "greetings"),), {"event": clip}, 1.84)
    assert selected.evidence_basis == "gesture_and_social_context"
    generic = _clip("generic", [
        (1.0, "A crowd of people"), (1.6, "People stand next to each other"),
    ], duration=2)
    with pytest.raises(BeatMatchError):
        match_scene_beats((_beat(0, "greetings"),), {"generic": generic}, 1.84)


def test_raised_hand_at_stage_is_an_action_but_crowd_presence_is_not():
    clip = _clip("event", [
        (1.0, "A man standing on stage with flowers"),
        (1.7, "A man in front of the stage with his hand raised"),
    ], duration=2)
    selected, = match_scene_beats((_beat(0, "stage interaction"),), {"event": clip}, 1.84)
    assert selected.evidence_basis == "stage_person_and_action"


def test_greeting_context_excludes_car_and_seated_stage_in_source_fixture():
    # Model-style descriptions from three different measured source shots.
    # A nearby crowd or a later seated speaker does not extend a greeting.
    clip = _clip("event", [
        (0.3, "People gathered around a car, some engaged in conversation"),
        (0.7, "People standing around a car with cameras"),
        (2.8, "A man with hands clasped next to a woman in a group"),
        (3.2, "The man stands next to another person"),
        (5.2, "People engaged in conversation in front of a window"),
        (5.6, "Two people smiling at each other by a window"),
        (13.2, "Three men sitting on chairs in front of a banner"),
        (13.8, "One man reading while another man is smiling at him in chairs"),
    ], duration=15, cuts=(1.25, 2.5, 3.5, 4.75, 6, 12.75, 14.25))
    beat = _beat(0, "greetings")
    anchors = _candidates(0, beat, {"event": clip}, None)
    context = _context_candidates(0, beat, {"event": clip}, anchors)
    assert any(4.75 < item.source_in_s < item.source_out_s <= 6 for item in context)
    assert not any(item.source_in_s < 1.25 or item.source_in_s >= 12.75
                   for item in context)


def test_significance_word_does_not_turn_statue_scene_into_signage():
    clip = _clip("event", [
        (1.0, "A crowd under a canopy holding signs"),
        (1.6, "People around a statue discussing its significance"),
    ], duration=2)
    with pytest.raises(BeatMatchError):
        match_scene_beats((_beat(0, "venue signage"),), {"event": clip}, 1.84)


def test_context_keeps_earlier_evidence_when_last_sample_touches_cut_margin():
    beat = _beat(0, "stage interaction")
    clip = _clip("event", [
        (1.1, "A man speaking on stage"),
        (1.5, "A speaker addressing people on stage"),
        (3.3, "People standing around chairs facing the stage"),
        (4.1, "People at the event stage beside chairs"),
        (4.94, "More people visible beside the stage"),
    ], duration=6, cuts=(2, 5))
    anchors = _candidates(0, beat, {"event": clip}, None)
    context = _context_candidates(0, beat, {"event": clip}, anchors)
    assert any(2 < item.source_in_s < item.source_out_s <= 4.92 for item in context)


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
            (2, "An event building with a sign beside chairs"),
            (5, "A venue hall with a banner above the seats"),
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


def test_real_failed_greetings_descriptions_cannot_license_unobserved_window_tail():
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
    # A camera-only observation at 9.5s is next to the old proposed 9.49s
    # out point. That 0.01s gap cannot establish a clean ending.
    with pytest.raises(BeatMatchError, match="continuous 6.25s source window"):
        match_scene_beats((_beat(0, "greetings"),), clips, 6.25)


def test_gesture_and_interaction_still_support_a_clean_window():
    clip = _clip("greeting", [(3.5, "Two people shake hands"),
                              (8.5, "People smiling at each other")], duration=15)
    selected, = match_scene_beats((_beat(0, "greetings"),), {"greeting": clip}, 6.25)
    assert selected.evidence_basis == "gesture_and_interaction"
    assert selected.evidence_times_s == (3.5, 8.5)


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


def test_mixed_source_montage_cannot_stretch_short_greeting_into_six_seconds():
    # The saved event source alternates greeting, camera, and statue images.
    # Two matching observations cannot license the unrelated material between.
    clips = {"event": _clip("event", [
        (5.18, "People standing close and engaged in conversation"),
        (6.66, "One man holding flowers as another smiles at him"),
        (8.14, "A man looking at a camera in front of a mirror"),
        (9.63, "A man with his hands clasped near a phone camera"),
        (11.11, "A statue decorated with flowers"),
    ], duration=20.733)}
    with pytest.raises(BeatMatchError, match="continuous 6.25s source window"):
        match_scene_beats((_beat(0, "greetings"),), clips, 6.25)


def test_stage_observations_separated_by_crowd_do_not_form_one_stage_shot():
    clips = {"event": _clip("event", [
        (16.6, "A person interacting on stage"),
        (19.62, "A crowd gathered under a canopy"),
        (22.64, "A speaker on stage"),
    ], duration=36.217)}
    with pytest.raises(BeatMatchError, match="continuous 6.25s source window"):
        match_scene_beats((_beat(0, "stage interaction"),), clips, 6.25)


def test_opening_window_starts_near_first_supporting_visual_evidence():
    clip = _clip("venue", [(1.51, "A venue sign outside"),
                           (4.53, "A venue poster outside")], duration=12)
    match, = match_scene_beats((_beat(0, "venue signage"),), {"venue": clip}, 6.25)
    assert 0 < 1.51 - match.source_in_s <= 0.25


def test_sparse_matching_observations_cannot_hide_an_intervening_source_cut():
    clip = _clip("event", [(5.2, "People greeting on stage"),
                           (8.2, "People greeting on stage")],
                 duration=20, cuts=(6.0,))
    with pytest.raises(BeatMatchError, match="measured cuts") as error:
        match_scene_beats((_beat(0, "greetings"),), {"event": clip}, 6.25)
    assert "6.00s" in str(error.value)


def test_real_rejected_event_windows_do_not_cross_measured_discontinuities():
    # Source-relative frame-difference candidates measured on the original
    # event MOVs; descriptions are saved local-model observations, not truth.
    clips = {
        "clip_ad54481e2bb1": _clip("clip_ad54481e2bb1", [
            (5.18, "People engaged in conversation"),
            (6.66, "A man greets another near a bouquet"),
            (8.14, "A person looks at a camera"),
            (9.63, "A man with hands clasped by a camera"),
            (11.11, "A decorated statue"),
        ], duration=20.733, cuts=(4.75, 6.0, 7.25, 8.25, 9.25, 10.75)),
        "clip_79ae16aaf71c": _clip("clip_79ae16aaf71c", [
            (16.6, "People interacting on stage"),
            (19.62, "A crowd under a canopy"),
            (22.64, "A person on stage"),
        ], duration=36.217, cuts=(16.75, 18.5, 22.25)),
    }
    with pytest.raises(BeatMatchError, match="scene beat 1: greetings"):
        match_scene_beats((_beat(0, "greetings"), _beat(1, "stage interaction")), clips, 12.5)
    with pytest.raises(BeatMatchError, match="scene beat 1: stage interaction"):
        match_scene_beats((_beat(0, "stage interaction"),), clips, 6.25)


def test_discontinuity_outside_selected_window_does_not_block_clean_shot():
    clip = _clip("event", [(2, "A venue sign"), (4, "A venue banner")],
                 duration=20, cuts=(9.0,))
    selected, = match_scene_beats((_beat(0, "venue signage"),), {"event": clip}, 6.25)
    assert selected.source_out_s < 9.0


def test_secondary_venue_cue_cannot_replace_requested_signage():
    clip = _clip("venue", [(2, "People gather outside a building"),
                           (5, "A venue hall with seats")], duration=12)
    with pytest.raises(BeatMatchError, match="scene beat 1: venue signage"):
        match_scene_beats((_beat(0, "venue signage"),), {"venue": clip}, 6.25)


def test_stage_screen_without_people_acting_is_not_stage_interaction():
    clip = _clip("venue", [(2, "A stage screen shows a portrait"),
                           (5, "A large screen above an empty stage")], duration=12)
    with pytest.raises(BeatMatchError, match="scene beat 1: stage interaction"):
        match_scene_beats((_beat(0, "stage interaction"),), {"venue": clip}, 6.25)


def test_short_clean_shots_fill_ordered_beats_without_crossing_cuts_or_repeating():
    clip = _clip("event", [
        (0.5, "A venue sign"), (1.5, "A venue banner"),
        (2.5, "A venue poster"), (3.5, "A venue sign"),
        (4.5, "A speaker at a podium"), (5.5, "A speaker at a podium"),
        (6.5, "A man speaking at a podium"), (7.5, "A man speaking at a podium"),
    ], duration=8, cuts=(2, 4, 6))
    windows = match_scene_beats((_beat(0, "venue signage"),
                                 _beat(1, "podium speaker")), {"event": clip}, 6)
    assert [item.beat_index for item in windows] == [0, 0, 1, 1]
    assert sum(item.source_out_s - item.source_in_s for item in windows) == pytest.approx(6)
    assert all(not (item.source_in_s < cut < item.source_out_s)
               for item in windows for cut in clip.scene_cut_times_s)
    assert all(a.source_out_s <= b.source_in_s for a, b in zip(windows, windows[1:]))


def test_short_shots_insufficient_for_duration_report_capacity_instead_of_stretching():
    clip = _clip("event", [
        (0.5, "A venue sign"), (1.5, "A venue banner"),
        (2.5, "A speaker at a podium"), (3.5, "A speaker at a podium"),
    ], duration=4, cuts=(2,))
    with pytest.raises(BeatMatchError, match="Short-shot feasibility: Ordered, nonrepeated"):
        match_scene_beats((_beat(0, "venue signage"),
                           _beat(1, "podium speaker")), {"event": clip}, 6)
