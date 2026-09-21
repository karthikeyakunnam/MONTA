"""
MONTA — Director Graph (Layer 5)
==================================
The Director Agent's planning subgraph.
Never edits. Never renders. Only thinks.
Creates the task plan for the entire pipeline.
"""

from langgraph.graph import StateGraph, END
from orchestration.state.graph_state import MontaState


def build_director_graph() -> StateGraph:
    """Build the Director Agent's planning subgraph."""
    graph = StateGraph(MontaState)

    graph.add_node("understand_footage", understand_footage_task)
    graph.add_node("build_story_plan", build_story_plan_task)
    graph.add_node("design_pacing", design_pacing_task)
    graph.add_node("choose_edit_style", choose_edit_style_task)
    graph.add_node("plan_timeline", plan_timeline_task)

    graph.set_entry_point("understand_footage")
    graph.add_edge("understand_footage", "build_story_plan")
    graph.add_edge("build_story_plan", "design_pacing")
    graph.add_edge("design_pacing", "choose_edit_style")
    graph.add_edge("choose_edit_style", "plan_timeline")
    graph.add_edge("plan_timeline", END)

    return graph


async def understand_footage_task(state: MontaState) -> dict:
    """Task 1: Understand what footage is available."""
    # TODO: Analyze clip metadata and scene tags
    return {"director_task_1": "footage_understood"}


async def build_story_plan_task(state: MontaState) -> dict:
    """Task 2: Design the story arc."""
    # TODO: Use LLM to plan narrative
    return {"director_task_2": "story_planned"}


async def design_pacing_task(state: MontaState) -> dict:
    """Task 3: Design pacing based on intent."""
    # TODO: Map emotion/genre to pacing rules
    return {"director_task_3": "pacing_designed"}


async def choose_edit_style_task(state: MontaState) -> dict:
    """Task 4: Choose editing style."""
    # TODO: Select style preset or generate custom
    return {"director_task_4": "style_chosen"}


async def plan_timeline_task(state: MontaState) -> dict:
    """Task 5: Create high-level timeline plan."""
    # TODO: Produce ordered task list for downstream agents
    return {"director_task_5": "timeline_planned"}
