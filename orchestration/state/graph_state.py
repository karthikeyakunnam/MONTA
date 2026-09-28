"""
MONTA — Graph State
=====================
TypedDict state for the master LangGraph.

Layers 3–7 store their contracts as JSON-mode dicts (``model_dump(mode="json")``)
so any LangGraph checkpointer can persist them; nodes re-validate with
``Model.model_validate`` on read, which also rejects a corrupted checkpoint.
"""

from typing import List, Optional, TypedDict


class ClipInput(TypedDict):
    clip_id: str
    path: str


class MontaState(TypedDict, total=False):
    # --- Input ---
    project_id: str
    user_id: str
    raw_prompt: str
    clips: List[ClipInput]

    # --- Layer 3: Prompt Intelligence (IntentAnalysis) ---
    intent: dict

    # --- Layer 4: Context Composer (ContextPack) ---
    context_pack: dict
    probe_failures: List[dict]

    # --- Layer 5: Director (ExecutionPlan / ExecutionReport) ---
    execution_plan: dict
    execution_report: dict

    # --- Layer 7: Story Architect (StoryPlan / ValidationReport) ---
    story_plan: Optional[dict]
    story_validation: Optional[dict]
    story_valid: bool
    story_judgement: Optional[dict]   # StoryJudgement of the current (best-so-far) story_plan
    story_attempts: List[dict]        # every StoryAttempt, kept or not, with the judge's rationale
    rejected_patterns: List[str]
    # Projection consumed by Layers 8–9 and persisted in timelines.story_acts
    story_acts: List[dict]
    story_timeline: List[dict]

    # --- Layer 8: Style Engine ---
    style_config: dict

    # --- Layer 9: Timeline Generator (final edit sequence) ---
    timeline: List[dict]

    # --- Layer 10: Audio Studio ---
    audio_config: dict

    # --- Layer 11: Edit Executor ---
    edit_result: dict

    # --- Layer 12: Critic System ---
    critic_score: float
    critic_feedback: dict
    should_retry: bool
    retry_count: int
    story_score: float
    pacing_score: float
    music_sync_score: float
    caption_score: float
    visual_score: float

    # --- Layer 13: Render Farm ---
    render_output: dict

    # --- Layer 14: Learning Layer ---
    user_feedback: Optional[dict]

    # --- Errors ---
    error: Optional[str]
