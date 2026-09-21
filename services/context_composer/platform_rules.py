"""MONTA — Platform Rules."""

PLATFORM_RULES = {
    "instagram": {
        "max_duration": 90,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "max_file_size_mb": 100,
    },
    "youtube_short": {
        "max_duration": 60,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "max_file_size_mb": 256,
    },
    "tiktok": {
        "max_duration": 180,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "max_file_size_mb": 287,
    },
    "general": {
        "max_duration": 600,
        "aspect_ratio": "16:9",
        "resolution": "1920x1080",
        "max_file_size_mb": 500,
    },
}
