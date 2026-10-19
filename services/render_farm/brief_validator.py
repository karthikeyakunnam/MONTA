"""Check explicit creator requirements against the saved IR and encoded critic evidence."""

from __future__ import annotations

from shared.contracts.timeline import ColorAdjustment


def validate_brief_output(brief, timeline, encoded_report: dict, *,
                          requested_duration_s: float | None = None,
                          captions_burned: bool = False) -> dict:
    """Do not infer scene meaning or exact OCR from technical measurements."""
    checks = []

    def add(name, passed, evidence, *, unavailable=False):
        checks.append({"name": name, "status": ("unavailable" if unavailable else
                       "passed" if passed else "failed"), "evidence": evidence})

    encoded = {item["name"]: item for item in encoded_report.get("checks", ())}
    duration = encoded.get("duration_and_frame", {})
    actual = duration.get("evidence", {}).get("encoded_duration_s")
    if requested_duration_s is not None:
        tolerance = 0.5
        add("requested_duration", actual is not None and
            abs(actual - requested_duration_s) <= tolerance,
            {"requested_s": requested_duration_s, "encoded_s": actual,
             "planned_s": timeline.render_duration_s, "tolerance_s": tolerance},
            unavailable=actual is None)

    beats = tuple(brief.scene_beats)
    if beats:
        roles = [segment.narrative_role for segment in timeline.video_segments]
        ordered_roles = [role for index, role in enumerate(roles)
                         if index == 0 or role != roles[index - 1]]
        expected = [f"Requested scene beat {index + 1}: {beat.description}"
                    for index, beat in enumerate(beats)]
        add("beat_plan_order", ordered_roles == expected,
            {"expected": expected, "actual": ordered_roles,
             "segment_roles": roles})
        add("encoded_scene_meaning", False,
            {"reason": "Frame descriptions and planned roles cannot independently verify scene meaning in the MP4"},
            unavailable=True)

    required = {(item.category, item.value) for item in brief.directives
                if item.priority == "required"}
    required_text = list(brief.text_overlays)
    actual_text = list(timeline.text_overlays)
    add("planned_overlays", len(required_text) == len(actual_text) and all(
        any(overlay.role == requirement.role and overlay.text == requirement.text and
            (requirement.position is None or overlay.position == requirement.position) and
            (requirement.start_s is None or overlay.start_ms == round(requirement.start_s * 1000)) and
            (requirement.end_s is None or overlay.end_ms == round(requirement.end_s * 1000))
            for overlay in actual_text)
        for requirement in required_text),
        {"required": [(item.role, item.text, item.position, item.start_s, item.end_s)
                      for item in required_text],
         "actual": [(item.role, item.text, item.position, item.start_ms, item.end_ms)
                    for item in actual_text]})
    for requirement in required_text:
        overlay = next((item for item in actual_text
                        if item.role == requirement.role and item.text == requirement.text), None)
        pixel_check = encoded.get(f"text:{overlay.overlay_id}") if overlay else None
        add(f"encoded_text_asset:{requirement.role}",
            pixel_check is not None and pixel_check.get("status") == "passed",
            pixel_check.get("evidence", {}) if pixel_check else {},
            unavailable=pixel_check is None or pixel_check.get("status") == "unavailable")
    if required_text:
        add("independent_text_ocr", False,
            {"reason": "OCR is unavailable; pixel matching verifies the rasterized asset, not an independent reading"},
            unavailable=True)

    if ("text", "captions_off") in required:
        add("no_generated_captions", not captions_burned and
            not any(item.role == "caption" for item in actual_text),
            {"caption_burn_pass": captions_burned,
             "caption_overlay_count": sum(item.role == "caption" for item in actual_text)})
    if ("audio", "music_off") in required:
        add("no_added_music", not timeline.audio_segments,
            {"added_audio_segment_count": len(timeline.audio_segments)})
    if ("audio", "source_audio") in required:
        add("source_audio_plan", all(item.has_audio and item.volume > 0
                                     for item in timeline.video_segments),
            {"source_audio": [(item.clip_id, item.has_audio, item.volume)
                              for item in timeline.video_segments]})
        add("encoded_audio_stream", encoded.get("audio_peak", {}).get("status") in ("passed", "warning"),
            encoded.get("audio_peak", {}).get("evidence", {}),
            unavailable=encoded.get("audio_peak", {}).get("status") == "unavailable" or
            "audio_peak" not in encoded)
    if ("color", "natural") in required:
        neutral = ColorAdjustment()
        add("source_faithful_grade", all(item.color == neutral for item in timeline.video_segments),
            {"per_shot_grade": [item.color.model_dump(mode="json") for item in timeline.video_segments]})
        add("encoded_color_fidelity", False,
            {"reason": "No calibrated source-to-output color comparison was performed"}, unavailable=True)
    if ("transition", "cut") in required:
        add("calm_cut_plan", all(item.transition_in.value == "cut" and
                                item.fade_in_ms == 0 and item.fade_out_ms == 0 and
                                abs(item.speed - 1.0) < 0.001
                                for item in timeline.video_segments),
            {"transitions": [item.transition_in.value for item in timeline.video_segments],
             "speeds": [item.speed for item in timeline.video_segments]})
        add("calm_pacing", False,
            {"reason": "Straight cuts alone cannot establish calm pacing",
             "shot_count": len(timeline.video_segments),
             "encoded_duration_s": actual,
             "mean_shot_duration_s": (round(timeline.render_duration_s /
                                            len(timeline.video_segments), 3)
                                      if timeline.video_segments else None)},
            unavailable=True)

    for name in ("duration_and_frame", "black_frames", "audio_peak", "audio_clipping"):
        item = encoded.get(name)
        add(f"encoded_{name}", item is not None and item.get("status") == "passed",
            item.get("evidence", {}) if item else {},
            unavailable=item is None or item.get("status") in ("unavailable", "warning"))
    statuses = {item["status"] for item in checks}
    return {"status": "failed" if "failed" in statuses else
            "unavailable" if "unavailable" in statuses else "passed", "checks": checks}
