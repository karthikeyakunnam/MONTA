"""
MONTA — Director Agent
========================
Layer 5: The CEO. Never edits. Never renders. Only thinks.
"""


class DirectorAgent:
    """
    The Director Agent is the strategic planner of MONTA.
    
    It receives the composed context (Layer 4 output) and creates
    a structured task plan for all downstream agents.
    
    Tasks it creates:
    1. Understand footage — what clips are available
    2. Build story — narrative arc design
    3. Design pacing — tempo and rhythm
    4. Choose edit style — visual treatment
    5. Generate timeline — ordered clip sequence
    """

    def __init__(self, llm=None):
        self.llm = llm
        # TODO: Initialize with appropriate LLM (Qwen 3 or Llama 3)

    async def plan(self, context: dict) -> list:
        """
        Create a task plan based on the composed context.
        
        Args:
            context: Layer 4 output containing user intent, clips, preferences
            
        Returns:
            List of tasks for downstream agents
        """
        # TODO: Use LLM to analyze context and create task plan
        tasks = [
            {"id": 1, "type": "understand_footage", "status": "pending"},
            {"id": 2, "type": "build_story", "status": "pending"},
            {"id": 3, "type": "design_pacing", "status": "pending"},
            {"id": 4, "type": "choose_edit_style", "status": "pending"},
            {"id": 5, "type": "generate_timeline", "status": "pending"},
        ]
        return tasks

    async def evaluate_plan(self, plan: list, context: dict) -> dict:
        """Self-evaluate the quality of the plan before executing."""
        # TODO: LLM self-critique of the plan
        return {"plan_quality": 0.0, "suggestions": []}
