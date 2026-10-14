"""
MONTA — User Preferences Store (Layer 14)
============================================
Stores and retrieves user feedback and preferences for personalization.

Architecture:
- JSON-file based for local-first, offline operation (no Qdrant/PG required)
- Thread-safe via asyncio locks
- Privacy: per-user reset, bounded storage, no PII in feedback schema
- Dedup: one rating per (user, project, edit) triple
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monta.memory.preferences")

DEFAULT_STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".monta", "preferences")


class FeedbackRecord:
    """A single user feedback entry."""

    __slots__ = (
        "user_id", "project_id", "edit_id", "liked", "rating",
        "tags", "comment", "model_used", "style", "prompt_summary",
        "timestamp",
    )

    def __init__(
        self,
        user_id: str,
        project_id: str,
        edit_id: str,
        liked: bool,
        rating: float = 0.0,
        tags: Optional[list[str]] = None,
        comment: str = "",
        model_used: str = "",
        style: str = "",
        prompt_summary: str = "",
        timestamp: Optional[str] = None,
    ):
        self.user_id = user_id
        self.project_id = project_id
        self.edit_id = edit_id
        self.liked = liked
        self.rating = max(0.0, min(10.0, rating))
        self.tags = tags or []
        self.comment = comment
        self.model_used = model_used
        self.style = style
        self.prompt_summary = prompt_summary
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "project_id": self.project_id,
            "edit_id": self.edit_id,
            "liked": self.liked,
            "rating": self.rating,
            "tags": self.tags,
            "comment": self.comment,
            "model_used": self.model_used,
            "style": self.style,
            "prompt_summary": self.prompt_summary,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FeedbackRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__slots__})

    @property
    def dedup_key(self) -> str:
        return f"{self.user_id}:{self.project_id}:{self.edit_id}"


class UserPreferencesStore:
    """Store and retrieve user editing preferences.

    Local-first, JSON file-backed. One file per user.
    Max records per user is bounded (default 1000) to prevent unbounded growth.
    """

    MAX_RECORDS_PER_USER = 1000

    def __init__(self, storage_dir: Optional[str] = None):
        self._storage_dir = Path(storage_dir or DEFAULT_STORAGE_DIR)
        self._lock = asyncio.Lock()
        self._cache: dict[str, list[FeedbackRecord]] = {}

    def _user_file(self, user_id: str) -> Path:
        # Sanitize user_id to prevent path traversal
        safe_id = "".join(c for c in user_id if c.isalnum() or c in "-_.")
        if not safe_id:
            safe_id = "anonymous"
        return self._storage_dir / f"{safe_id}.json"

    async def _load_user(self, user_id: str) -> list[FeedbackRecord]:
        """Load user feedback records from disk."""
        if user_id in self._cache:
            return self._cache[user_id]

        fp = self._user_file(user_id)
        if not fp.exists():
            self._cache[user_id] = []
            return []

        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            records = [FeedbackRecord.from_dict(d) for d in data.get("records", [])]
            self._cache[user_id] = records
            return records
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Corrupt preferences file for user %s: %s — starting fresh", user_id, e)
            self._cache[user_id] = []
            return []

    async def _save_user(self, user_id: str) -> None:
        """Persist user feedback records to disk."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        records = self._cache.get(user_id, [])
        # Enforce bounded storage
        if len(records) > self.MAX_RECORDS_PER_USER:
            records = records[-self.MAX_RECORDS_PER_USER:]
            self._cache[user_id] = records

        fp = self._user_file(user_id)
        data = {
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "records": [r.to_dict() for r in records],
        }
        fp.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def record_feedback(
        self,
        user_id: str,
        edit_id: str,
        liked: bool,
        tags: Optional[list[str]] = None,
        rating: float = 0.0,
        project_id: str = "",
        comment: str = "",
        model_used: str = "",
        style: str = "",
        prompt_summary: str = "",
    ) -> FeedbackRecord:
        """Record user feedback on an edit. Deduplicates by (user, project, edit)."""
        async with self._lock:
            records = await self._load_user(user_id)
            new_record = FeedbackRecord(
                user_id=user_id,
                project_id=project_id,
                edit_id=edit_id,
                liked=liked,
                rating=rating,
                tags=tags,
                comment=comment,
                model_used=model_used,
                style=style,
                prompt_summary=prompt_summary,
            )

            # Dedup: replace existing record for same (user, project, edit)
            dedup_key = new_record.dedup_key
            records = [r for r in records if r.dedup_key != dedup_key]
            records.append(new_record)
            self._cache[user_id] = records
            await self._save_user(user_id)
            return new_record

    async def get_preferences(self, user_id: str) -> dict:
        """Get aggregated user preferences derived from feedback history."""
        async with self._lock:
            records = await self._load_user(user_id)

        if not records:
            return {
                "preferred_style": "",
                "preferred_tags": [],
                "feedback_count": 0,
                "like_ratio": 0.0,
                "average_rating": 0.0,
            }

        liked = [r for r in records if r.liked]
        # Aggregate preferred styles
        style_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        total_rating = 0.0
        rated_count = 0

        for r in liked:
            if r.style:
                style_counts[r.style] = style_counts.get(r.style, 0) + 1
            for t in r.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if r.rating > 0:
                total_rating += r.rating
                rated_count += 1

        preferred_style = max(style_counts, key=style_counts.get) if style_counts else ""
        preferred_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:5]

        return {
            "preferred_style": preferred_style,
            "preferred_tags": preferred_tags,
            "feedback_count": len(records),
            "like_ratio": len(liked) / len(records) if records else 0.0,
            "average_rating": total_rating / rated_count if rated_count else 0.0,
        }

    async def get_liked_examples(self, user_id: str, style: str = "", limit: int = 10) -> list[dict]:
        """Get liked edit examples for retrieval-based learning."""
        async with self._lock:
            records = await self._load_user(user_id)

        liked = [r for r in records if r.liked]
        if style:
            liked = [r for r in liked if r.style == style]

        # Sort by rating (highest first), then by recency
        liked.sort(key=lambda r: (r.rating, r.timestamp), reverse=True)
        return [r.to_dict() for r in liked[:limit]]

    async def find_similar_preferences(self, user_id: str) -> list:
        """Find users with similar preferences (future: collaborative filtering).

        Not implemented — requires multi-user deployment. Returns empty list.
        """
        return []

    async def reset_user(self, user_id: str) -> bool:
        """Delete all feedback data for a user (privacy/reset)."""
        async with self._lock:
            self._cache.pop(user_id, None)
            fp = self._user_file(user_id)
            if fp.exists():
                fp.unlink()
                logger.info("User %s preference data deleted", user_id)
                return True
            return False

    async def export_user_data(self, user_id: str) -> dict:
        """Export all user data (GDPR-style data export)."""
        async with self._lock:
            records = await self._load_user(user_id)
        return {
            "user_id": user_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "records": [r.to_dict() for r in records],
        }
