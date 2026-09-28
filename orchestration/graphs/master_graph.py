"""
MONTA — Master Orchestration Graph
=====================================
The main LangGraph that coordinates the entire editing pipeline.

Flow:
    Prompt → Context → Director (plans + executes Intelligence → Story) → Style →
    Timeline → Audio → Execute → Critic → (retry story?) → Render

Layers 3–7 delegate to ``MontaPipeline``; each node reads and writes JSON-mode
contract dicts so the state is checkpointable.
"""

from langgraph.graph import StateGraph, END

from orchestration.pipeline import MontaPipeline
from orchestration.state.graph_state import MontaState
from shared.constants import CRITIC_RETRY_THRESHOLD, MAX_CRITIC_RETRIES
from shared.contracts.clip import ClipSource
from shared.contracts.context import ContextPack
from shared.contracts.intent import IntentAnalysis
from orchestration.agents.story import StoryDesignError
from shared.contracts.evaluation import StoryAttempt, StoryJudgement
from shared.contracts.story import StoryPlan
from shared.exceptions import PipelineError
from shared.observability import catalog as m


def story_projection(plan: StoryPlan) -> dict:
    """Act and story-timeline views consumed by Layers 8–9 and the timelines table.

    Layer 7 writes ``story_timeline``; Layer 9 owns ``timeline`` (the final edit
    sequence) and must derive it from ``story_timeline``/``story_plan``.
    """
    return {
        "story_acts": [
            {"act_number": i + 1, "act_id": a.act_id, "theme": a.name, "clips": list(a.clip_ids), "mood": plan.emotion.value.value}
            for i, a in enumerate(plan.reasoning.acts)
        ],
        "story_timeline": [
            {"clip_id": s.clip_id, "start": s.start, "end": s.end, "source_in": s.source_in, "source_out": s.source_out,
             "act": s.act_id, "is_hero": s.is_hero, "transition": "cut"}
            for s in plan.timeline
        ],
    }


def build_master_graph(pipeline: MontaPipeline) -> StateGraph:
    """
    Build the master orchestration graph.

    This graph coordinates all 14 layers of the MONTA pipeline.
    """

    async def parse_prompt_node(state: MontaState) -> dict:
        """Layer 3: free-form prompt → IntentAnalysis."""
        intent = await pipeline.interpret(state.get("raw_prompt", ""))
        return {"intent": intent.model_dump(mode="json")}

    async def compose_context_node(state: MontaState) -> dict:
        """Layer 4: probe clips and assemble the ContextPack."""
        sources = [ClipSource(**c) for c in state.get("clips", [])]
        if not sources:
            raise PipelineError("no clips in state")
        metas, failures = await pipeline.probe(sources)
        if not metas:
            raise PipelineError("no clip could be read: " + "; ".join(f"{f.clip_id}: {f.reason}" for f in failures))
        pack = await pipeline.compose(
            project_id=state["project_id"], user_id=state["user_id"],
            intent=IntentAnalysis.model_validate(state["intent"]), metadata=metas,
        )
        return {"context_pack": pack.model_dump(mode="json"),
                "probe_failures": [{"clip_id": f.clip_id, "reason": f.reason} for f in failures]}

    async def director_node(state: MontaState) -> dict:
        """Layers 5–7: Director plans the task graph and runs Video Intelligence → Story Architect."""
        pack = ContextPack.model_validate(state["context_pack"])
        plan, report, ctx = await pipeline.execute(pack)
        story = ctx.results.get("design_story")
        validation = ctx.results.get("validate_story")
        out = {
            "execution_plan": plan.model_dump(mode="json"),
            "execution_report": report.model_dump(mode="json"),
            "context_pack": ctx.pack.model_dump(mode="json"),
            "intent": ctx.pack.intent.model_dump(mode="json"),
            "story_plan": story.model_dump(mode="json") if story else None,
            "story_validation": validation.model_dump(mode="json") if validation else None,
            "story_valid": bool(validation and validation.passed),
            "rejected_patterns": [],
        }
        if story is None:
            out["error"] = f"story design failed: {report.reasoning}"
            return out
        judgement = await pipeline.judge_story(story, ctx.pack)
        attempt = StoryAttempt(attempt=0, story_pattern=story.story_pattern, judgement=judgement, kept=True,
                               reason="initial story")
        return out | {
            "story_judgement": judgement.model_dump(mode="json"),
            "story_attempts": [attempt.model_dump(mode="json")],
        } | story_projection(story)

    async def build_story_node(state: MontaState) -> dict:
        """Layer 7 (Critic retry): try an alternative story; keep whichever the independent judge ranks higher.

        The best plan seen so far is never replaced by a worse one, so retries can only
        improve (or preserve) the final story.
        """
        pack = ContextPack.model_validate(state["context_pack"])
        attempts = list(state.get("story_attempts", []))
        rejected = list(dict.fromkeys(state.get("rejected_patterns", []) + [a["story_pattern"] for a in attempts]))
        retry_count = state.get("retry_count", 0) + 1
        try:
            candidate, validation = await pipeline.redesign_story(pack, avoid_patterns=rejected)
        except StoryDesignError as e:
            note = StoryAttempt(attempt=retry_count, story_pattern="none", kept=False, reason=f"no alternative story: {e}",
                                judgement=StoryJudgement.model_validate(state["story_judgement"]))
            return {"retry_count": retry_count, "story_attempts": attempts + [note.model_dump(mode="json")]}
        judgement = await pipeline.judge_story(candidate, pack)
        best = StoryJudgement.model_validate(state["story_judgement"]) if state.get("story_judgement") else None
        improved = best is None or judgement.rank_key > best.rank_key
        reason = (f"judge {judgement.overall_score:.2f} (valid={judgement.valid_plan}) vs best "
                  f"{best.overall_score:.2f} (valid={best.valid_plan})" if best else "no previous story")
        attempt = StoryAttempt(attempt=retry_count, story_pattern=candidate.story_pattern, judgement=judgement,
                               kept=improved, reason=("replaced best: " if improved else "kept previous best: ") + reason)
        m.STORY_RETRY.inc(result="replaced" if improved else "kept_best")
        out = {
            "rejected_patterns": rejected + [candidate.story_pattern],
            "retry_count": retry_count,
            "story_attempts": attempts + [attempt.model_dump(mode="json")],
        }
        if not improved:
            return out
        return out | {
            "story_plan": candidate.model_dump(mode="json"),
            "story_validation": validation.model_dump(mode="json"),
            "story_valid": validation.passed,
            "story_judgement": judgement.model_dump(mode="json"),
        } | story_projection(candidate)

    graph = StateGraph(MontaState)

    # Layer 3: Prompt Intelligence
    graph.add_node("parse_prompt", parse_prompt_node)
    # Layer 4: Context Composer
    graph.add_node("compose_context", compose_context_node)
    # Layers 5-7: Director executes Video Intelligence and Story Architect
    graph.add_node("director", director_node)
    # Layer 7: Story Architect (critic retry path)
    graph.add_node("build_story", build_story_node)
    # Layer 8: Style Engine
    graph.add_node("apply_style", apply_style_node)
    # Layer 9: Timeline Generator
    graph.add_node("generate_timeline", generate_timeline_node)
    # Layer 10: Audio Studio
    graph.add_node("process_audio", process_audio_node)
    # Layer 11: Edit Executor
    graph.add_node("execute_edits", execute_edits_node)
    # Layer 12: Critic System
    graph.add_node("evaluate", evaluate_node)
    # Layer 13: Render Farm
    graph.add_node("render", render_node)

    graph.set_entry_point("parse_prompt")
    graph.add_edge("parse_prompt", "compose_context")
    graph.add_edge("compose_context", "director")
    graph.add_conditional_edges("director", story_ready, {"continue": "apply_style", "stop": END})
    graph.add_edge("build_story", "apply_style")
    graph.add_edge("apply_style", "generate_timeline")
    graph.add_edge("generate_timeline", "process_audio")
    graph.add_edge("process_audio", "execute_edits")
    graph.add_edge("execute_edits", "evaluate")

    # Critic → conditional: retry story or render
    graph.add_conditional_edges(
        "evaluate",
        should_retry,
        {
            "retry": "build_story",
            "render": "render",
        }
    )

    graph.add_edge("render", END)

    return graph


def story_ready(state: MontaState) -> str:
    """Stop the graph when Layers 5–7 could not produce a story."""
    return "continue" if state.get("story_plan") else "stop"


# --- Layers 8-13 node implementations ---

async def apply_style_node(state: MontaState) -> dict:
    """Layer 8: Style Engine maps style parameters."""
    # TODO: Call style_engine service
    return {"style_config": {}}


async def generate_timeline_node(state: MontaState) -> dict:
    """Layer 9: Timeline Generator produces edit sequence."""
    # TODO: Call timeline_generator service
    return {"timeline": []}


async def process_audio_node(state: MontaState) -> dict:
    """Layer 10: Audio Studio generates/processes audio."""
    # TODO: Call audio_studio service
    return {"audio_config": {}}


async def execute_edits_node(state: MontaState) -> dict:
    """Layer 11: Edit Executor performs actual video editing."""
    # TODO: Call edit_executor service
    return {"edit_result": {}}


async def evaluate_node(state: MontaState) -> dict:
    """Layer 12: Critic System evaluates edit quality."""
    # TODO: Call critic agent
    return {"critic_score": 0.0, "critic_feedback": {}}


async def render_node(state: MontaState) -> dict:
    """Layer 13: Render Farm generates final output."""
    # TODO: Call render_farm service
    return {"render_output": {}}


def should_retry(state: MontaState) -> str:
    """Decide whether to retry the story or proceed to render."""
    score = state.get("critic_score", 0)
    retries = state.get("retry_count", 0)

    if score < CRITIC_RETRY_THRESHOLD and retries < MAX_CRITIC_RETRIES:
        return "retry"
    return "render"
