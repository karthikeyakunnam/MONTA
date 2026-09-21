"""
MONTA — Graph State
=====================
TypedDict state definitions for LangGraph.
"""

from typing import TypedDict, List, Optional, Any


class MontaState(TypedDict, total=False):
    """
    Global state flowing through the MONTA orchestration graph.
    Each layer adds its outputs to this state.
    """
    
    # --- Input ---
    project_id: str
    user_id: str
    raw_prompt: str
    clip_paths: List[str]
    
    # --- Layer 3: Prompt Intelligence ---
    editing_intent: dict  # genre, pacing, color, emotion, target
    
    # --- Layer 4: Context Composer ---
    context: dict  # user_style, target_platform, video_set, intent
    
    # --- Layer 5: Director Agent ---
    task_plan: List[dict]
    
    # --- Layer 6: Video Intelligence ---
    clip_analysis: dict  # per-clip scene/action/quality/emotion
    clip_profiles: dict
    scene_tags: List[str]
    actions: List[str]
    quality_scores: dict
    emotions: List[str]
    
    # --- Layer 7: Story Architect ---
    story_acts: List[dict]  # act_number, theme, clips, mood
    narrative_candidates: List[dict]
    act_assignments: dict
    story_valid: bool
    
    # --- Layer 8: Style Engine ---
    style_config: dict  # cut_speed, zoom, music, captions
    
    # --- Layer 9: Timeline Generator ---
    timeline: List[dict]  # clip_id, start, end, transition
    
    # --- Layer 10: Audio Studio ---
    audio_config: dict
    
    # --- Layer 11: Edit Executor ---
    edit_result: dict
    
    # --- Layer 12: Critic System ---
    critic_score: float
    critic_feedback: dict
    should_retry: bool
    retry_count: int
    
    # --- Layer 13: Render Farm ---
    render_output: dict
    
    # --- Layer 14: Learning Layer ---
    user_feedback: Optional[dict]
    
    # --- Director subtasks ---
    director_task_1: str
    director_task_2: str
    director_task_3: str
    director_task_4: str
    director_task_5: str
    
    # --- Critic subscores ---
    story_score: float
    pacing_score: float
    music_sync_score: float
    caption_score: float
    visual_score: float
