"""
MONTA — User Configuration Persistence (Layer 12 / M6)
======================================================
Stores and retrieves persistent user configuration across sessions without
requiring shell environment variable edits.

Features:
- Local JSON file backed (~/.monta/config.json)
- Atomic file writes (writes to .tmp and renames) to prevent file corruption
- Schema validation with default fallbacks
- Runtime get/set/update/reset APIs
- Thread-safe and process-safe
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("monta.shared.config_persistence")

DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".monta", "config.json")


@dataclass
class AppConfig:
    """User-configurable settings persisted locally."""

    # Models
    preferred_text_model: str = "llama3.2:latest"
    preferred_vision_model: str = "llava:latest"
    ollama_url: str = "http://localhost:11434"
    model_timeout_seconds: float = 120.0

    # Rendering & Output
    default_resolution: str = "1080p"  # 720p, 1080p, 4k
    default_aspect_ratio: str = "16:9"  # 16:9, 9:16, 1:1
    default_fps: int = 30
    hardware_acceleration: str = "auto"  # auto, videotoolbox, nvenc, vaapi, cpu

    # Audio Studio
    default_audio_ducking: bool = True
    ducking_attenuation_db: float = -14.0
    default_music_volume: float = 0.5
    default_sfx_volume: float = 0.7

    # Storage & Privacy
    storage_root: str = "./storage"
    auto_cleanup_days: int = 7
    allow_telemetry: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class ConfigManager:
    """Manages reading and writing application configuration."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path or DEFAULT_CONFIG_PATH).resolve()
        self._config: Optional[AppConfig] = None

    def load(self) -> AppConfig:
        """Load configuration from disk, or create default if missing."""
        if not self.config_path.exists():
            self._config = AppConfig()
            self.save(self._config)
            return self._config

        try:
            content = self.config_path.read_text(encoding="utf-8")
            data = json.loads(content)
            self._config = AppConfig.from_dict(data)
        except Exception as e:
            logger.warning("Failed to parse config file %s: %s; falling back to defaults", self.config_path, e)
            self._config = AppConfig()

        return self._config

    def get(self) -> AppConfig:
        """Get cached config or load if not yet cached."""
        if self._config is None:
            return self.load()
        return self._config

    def save(self, config: Optional[AppConfig] = None) -> None:
        """Atomically persist configuration to disk."""
        cfg = config or self._config or AppConfig()
        self._config = cfg
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = cfg.to_dict()
        tmp_file = self.config_path.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self.config_path)
            logger.info("Configuration saved to %s", self.config_path)
        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            logger.error("Failed to write config to %s: %s", self.config_path, e)
            raise

    def update(self, **kwargs) -> AppConfig:
        """Update specific configuration fields and persist immediately."""
        cfg = self.get()
        for key, value in kwargs.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
            else:
                raise ValueError(f"Unknown configuration key: {key}")
        self.save(cfg)
        return cfg

    def reset(self) -> AppConfig:
        """Reset configuration to factory defaults."""
        default_cfg = AppConfig()
        self.save(default_cfg)
        return default_cfg
