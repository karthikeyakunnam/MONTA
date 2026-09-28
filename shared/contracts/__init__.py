"""
MONTA — Shared Contracts
==========================
Versioned, type-safe schemas exchanged between Layers 3–7 and consumed by the
Critic (Layer 12) and Learning Layer (Layer 14).

Every decision-bearing object carries a value, a confidence, and a reasoning
string (see ``explain.py``). Contracts are frozen pydantic models so a layer can
never mutate another layer's output in place; enrichment always produces a new
object via ``shared.contracts.base.revalidate`` (never ``model_copy``,
which skips validation).
"""
