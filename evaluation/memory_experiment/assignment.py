"""
MONTA — Experiment Arm Assignment
===================================
Stable hash of (salt, user_id) → [0, 1). Users below ``control_share`` are
held out of narrative memory. Changing the salt starts a fresh experiment;
changing ``control_share`` only moves users at the boundary.
"""

import hashlib
from typing import Literal

from shared.observability import catalog as m

Arm = Literal["memory", "control"]


class MemoryExperiment:
    def __init__(self, *, control_share: float = 0.1, salt: str = "narrative-memory-v1"):
        if not 0 <= control_share <= 1:
            raise ValueError("control_share must be in [0, 1]")
        self.control_share = control_share
        self.salt = salt

    def bucket(self, user_id: str) -> float:
        digest = hashlib.sha256(f"{self.salt}:{user_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def assign(self, user_id: str) -> Arm:
        arm: Arm = "control" if self.bucket(user_id) < self.control_share else "memory"
        m.MEMORY_ARM.inc(arm=arm)
        return arm
