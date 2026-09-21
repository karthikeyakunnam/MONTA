"""MONTA — Few-Shot Examples for Prompt Intelligence."""

FEW_SHOT_EXAMPLES = [
    {
        "prompt": "make this feel like a dark cinematic transformation story, slow beginning, emotional middle, aggressive ending, orange-teal grade, dramatic bass music",
        "intent": {
            "genre": "transformation",
            "pacing": "dynamic",
            "color": "orange_teal",
            "emotion": "motivational",
            "target": "instagram",
        }
    },
    {
        "prompt": "quick hype gym montage with heavy bass and fast cuts",
        "intent": {
            "genre": "montage",
            "pacing": "fast",
            "color": "dark",
            "emotion": "hype",
            "target": "instagram",
        }
    },
    {
        "prompt": "calm travel vlog with warm colors and soft transitions",
        "intent": {
            "genre": "vlog",
            "pacing": "slow",
            "color": "vibrant",
            "emotion": "calm",
            "target": "youtube_short",
        }
    },
]
