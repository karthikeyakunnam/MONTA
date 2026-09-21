"""
MONTA — Media Gateway: Video Validation
==========================================
Layer 2: Validates video files against platform constraints.

Checks:
- 1080p → up to 20 clips
- 4K → up to 4 clips  
- Max clip length → 4 min
- Supported formats → mp4, mov
"""


class VideoValidator:
    """Validates uploaded video files."""

    SUPPORTED_FORMATS = {"mp4", "mov"}
    MAX_CLIP_DURATION = 240  # 4 minutes in seconds
    MAX_CLIPS_1080P = 20
    MAX_CLIPS_4K = 4

    def validate_format(self, filename: str) -> bool:
        """Check if file format is supported."""
        ext = filename.rsplit(".", 1)[-1].lower()
        return ext in self.SUPPORTED_FORMATS

    def validate_duration(self, duration: float) -> bool:
        """Check if clip duration is within limits."""
        return duration <= self.MAX_CLIP_DURATION

    def validate_clip_count(self, resolution: str, current_count: int) -> bool:
        """Check if adding another clip exceeds resolution-based limits."""
        if "3840" in resolution or "4k" in resolution.lower():
            return current_count < self.MAX_CLIPS_4K
        return current_count < self.MAX_CLIPS_1080P

    def validate_all(self, filename: str, duration: float, resolution: str, clip_count: int) -> list:
        """Run all validations and return list of errors."""
        errors = []
        if not self.validate_format(filename):
            errors.append(f"Unsupported format. Supported: {self.SUPPORTED_FORMATS}")
        if not self.validate_duration(duration):
            errors.append(f"Clip too long ({duration}s). Max: {self.MAX_CLIP_DURATION}s")
        if not self.validate_clip_count(resolution, clip_count):
            errors.append(f"Too many clips for {resolution}")
        return errors
