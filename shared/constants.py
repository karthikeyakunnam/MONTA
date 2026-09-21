"""MONTA — Constants."""

# Video constraints
MAX_CLIP_DURATION_SECONDS = 240
MAX_CLIPS_1080P = 20
MAX_CLIPS_4K = 4
SUPPORTED_VIDEO_FORMATS = {"mp4", "mov"}

# Quality thresholds
CRITIC_RETRY_THRESHOLD = 7.0
MAX_CRITIC_RETRIES = 3

# Render
RENDER_RESOLUTIONS = ["720p", "1080p", "4k"]
DEFAULT_RENDER_FORMAT = "mp4"

# Platform limits
PLATFORM_MAX_DURATION = {
    "instagram": 90,
    "youtube_short": 60,
    "tiktok": 180,
    "general": 600,
}
