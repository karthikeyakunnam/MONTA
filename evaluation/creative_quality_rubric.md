# MONTA creative quality review rubric

Use this rubric to review rendered versions independently. Record an evidence note and a 0–4 rating for each dimension; mark a dimension **N/A** when the footage or requested feature does not provide evidence. Do not convert MONTA's heuristic story score into these ratings or aggregate them unless a project defines and publishes fixed weights in advance.

| Dimension | Evidence to inspect | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- | --- |
| Prompt adherence | Prompt constraints/preferences against selected shots, order, look, duration, audio and captions | Contradicts the request | Major requested elements missing | Mixed or incomplete | Meets explicit requirements | Meets requirements with clear, intentional choices |
| Narrative coherence | Opening, escalation, requested emotional beats, ending | No coherent order | Contradictory or confusing arc | Some beats land, others do not | Ordered and understandable | Deliberate progression with a strong payoff |
| Shot relevance | Source timestamps, visible content, role, quality and redundancy | Mostly irrelevant | Frequent irrelevant/repeated material | Some useful shots, notable misses | Relevant, varied selections | Every shot earns its place and supports the beat |
| Pacing | Cut durations and transitions versus requested pace/energy | Disruptive or unusable | Frequently mismatched | Uneven | Fits the requested rhythm | Rhythm changes intentionally support the story |
| Framing | Subject size/visibility, crop, embedded matte, aspect handling | Subject unusable | Subject obscured or mostly wasted frame | Mixed framing | Subject clear with acceptable crop | Consistent, intentional composition without unintended borders |
| Audio | Source/music selection, speech clarity, sync, loudness and ducking | Missing or unusable | Major sync/level conflict | Partially suitable | Clear, synchronized and balanced | Mix actively supports the edit without masking important audio |
| Captions | Requested text, transcript accuracy, timing, legibility and style | Missing/unreadable when requested | Major text/timing errors | Partly accurate or inconsistent | Accurate, synchronized and legible | Accurate and well integrated with the visual rhythm |
| Technical validity | FFprobe, full decode/playback, expected dimensions, duration tolerance, accessible file | Invalid or inaccessible | Major playback/stream defect | Plays with material defect | Valid MP4 and within declared tolerance | Valid, accessible export with all requested technical properties |

## Review protocol

1. Review the same source clips and prompt for both versions. Write the evidence before assigning a rating.
2. Keep human judgments separate from deterministic checks. FFprobe can establish codecs, dimensions, timestamps and stream duration; it cannot establish whether a story feels hopeful or a shot is emotionally effective.
3. For duration, compare requested duration to the planned duration and the encoded video duration separately. MONTA's current material-shortfall tolerance is `max(0.5 seconds, 5% of requested duration)`; planned duration also accounts for transition overlap. A footage/platform cap outside tolerance must be shown to the user.
4. For framing, inspect decoded frames across the render and measure unintended black-area coverage. Dimensions alone do not prove that the subject fills the frame.
5. Report every N/A and every failed/skipped check. Do not claim an edit improved unless the paired human review or predeclared objective metric supports that conclusion.

## Current session evidence boundary

The available live browser fixture is FFmpeg `testsrc` footage, not the absent 233.4-second user project. Its prompts and output support workflow, inference-provenance, stream, duration and persistence checks. They do not support ratings for prompt adherence, narrative coherence, shot relevance, emotional pacing or captions. The portrait-matte integration fixture supports a deterministic framing check only; it is not a human-subject composition study.

## Recorded live fixture review — 2026-10-16

Source: Chromium-created 8-second FFmpeg `testsrc` + sine fixture; revised prompt “Make the ending happier”; 6-second target. The browser preview loaded metadata and the downloaded MP4 passed FFprobe as H.264 1920×1080 + AAC, both 6.000s, 1,145,829 bytes. A separate 6-second three-cut revision had a 6.0s timeline, 0.25s transition overlap, 5.766667s video, and 5.750000s audio (16.7ms stream-duration skew), within MONTA's 0.5s tolerance.

| Dimension | Review |
| --- | --- |
| Prompt adherence | N/A for creative content: color bars are not travel/story footage. The UI and job snapshot did apply the revised prompt and requested options. |
| Narrative coherence | N/A: no scenes or human story evidence in the synthetic source. |
| Shot relevance | N/A: no real-world shots to assess. |
| Pacing | N/A: this fixture cannot establish whether the cuts support a human story or mood. |
| Framing | N/A for subject composition. Technical output canvas is 1920×1080; no subject is present to size or protect. |
| Audio | N/A for mix quality. FFprobe confirms a synchronized 6.000s AAC stream carrying the synthetic tone. |
| Captions | N/A: the live prompt revision turned captions off; configured Whisper large-v3 weights are absent (see `PROGRESS.md`). |
| Technical validity | 3/4: browser loaded the preview and the downloaded file is accessible, has valid H.264/AAC streams, and matches the target duration. A separate full decode test was not run. |

No aggregate score or creative improvement claim is made from this synthetic fixture.
