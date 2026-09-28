"""
MONTA — Context Composer (Layer 4)
====================================
Builds the immutable ``ContextPack`` every downstream agent consumes.
"""

from services.context_composer.composer import ContextComposer

__all__ = ["ContextComposer"]
