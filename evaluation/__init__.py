"""
MONTA — Evaluation
====================
Everything that *measures* Layers 3–7 lives here, outside the generation
path: the independent Story Judge, the Layer 6 video-intelligence evaluation
suite, golden-project comparison, and the narrative-memory experiment.

Independence rule (enforced by ``tests/test_story_judge.py``): nothing in
``evaluation/`` may import ``orchestration.agents.story`` — a judge that
reuses the architect's scoring would be grading the architect with its own
answer key.
"""
