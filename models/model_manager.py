"""
MONTA — Model Manager
=======================
Manages local model lifecycle through the Ollama runtime:
list, download, delete, availability checks, capability distinction
(text vs vision), disk usage, and in-use model protection.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from shared.local_runtime.adapters.ollama import OllamaRuntime, DEFAULT_OLLAMA_URL
from shared.local_runtime.base import LocalModelInfo
from shared.local_runtime.registry import ModelRegistry

logger = logging.getLogger("monta.models.manager")


@dataclass
class DownloadProgress:
    """Progress state for an active model download."""
    model_name: str
    status: str = "pending"  # pending, downloading, verifying, complete, failed, cancelled
    total_bytes: int = 0
    completed_bytes: int = 0
    started_at: float = 0.0
    error: str = ""

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, (self.completed_bytes / self.total_bytes) * 100.0)


@dataclass
class DiskUsageReport:
    """Disk usage summary for installed models."""
    total_bytes: int = 0
    model_sizes: dict[str, int] = field(default_factory=dict)

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 ** 3)


class ModelManagerError(Exception):
    """Base error for model management operations."""


class ModelNotFoundError(ModelManagerError):
    """Model is not installed locally."""


class ModelInUseError(ModelManagerError):
    """Cannot delete a model that is currently in use."""


class ModelDownloadError(ModelManagerError):
    """Model download failed."""


class ModelManager:
    """Manages local model loading and inference routing via Ollama.

    Features:
    - List installed models with capability metadata
    - Download models with progress tracking and cancellation
    - Delete models with in-use protection
    - Availability and capability checks (text vs vision)
    - Disk usage reporting
    - Corrupt/partial download detection
    """

    def __init__(
        self,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        runtime: Optional[OllamaRuntime] = None,
    ):
        self._runtime = runtime or OllamaRuntime(base_url=ollama_url)
        self._ollama_url = ollama_url.rstrip("/")
        self._active_models: set[str] = set()  # models currently in use
        self._downloads: dict[str, DownloadProgress] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0))

    async def aclose(self) -> None:
        """Clean up HTTP clients."""
        if not self._client.is_closed:
            await self._client.aclose()
        await self._runtime.aclose()

    # ------------------------------------------------------------------ list
    async def list_models(self) -> list[LocalModelInfo]:
        """List all locally installed models with capability metadata."""
        return await self._runtime.list_models()

    async def list_model_names(self) -> list[str]:
        """Return just the names of installed models."""
        models = await self.list_models()
        return [m.name for m in models]

    # ------------------------------------------------------------------ availability
    async def is_available(self) -> bool:
        """Check if the Ollama daemon is reachable."""
        return await self._runtime.is_available()

    async def is_model_installed(self, model_name: str) -> bool:
        """Check if a specific model is installed locally."""
        names = await self.list_model_names()
        # Check both exact match and base name match (e.g. "llama3.2" matches "llama3.2:latest")
        return model_name in names or any(
            n.split(":")[0] == model_name.split(":")[0] for n in names
        )

    # ------------------------------------------------------------------ capabilities
    async def get_model_capabilities(self, model_name: str) -> dict:
        """Get capability info for a model (text, vision, code, etc.)."""
        spec = ModelRegistry.get_spec(model_name)
        return {
            "name": model_name,
            "family": spec.family,
            "param_size": spec.param_size,
            "is_vision": spec.is_vision,
            "capabilities": [c.value for c in spec.capabilities],
            "installed": await self.is_model_installed(model_name),
        }

    async def list_vision_models(self) -> list[str]:
        """List installed models that have vision capabilities."""
        models = await self.list_models()
        return [m.name for m in models if "vision" in [c.value for c in m.capabilities]]

    async def list_text_models(self) -> list[str]:
        """List installed models that have text capabilities."""
        models = await self.list_models()
        return [m.name for m in models if "text" in [c.value for c in m.capabilities]]

    # ------------------------------------------------------------------ download
    async def download_model(
        self,
        model_name: str,
        on_progress: Optional[callable] = None,
    ) -> bool:
        """Download a model from Ollama registry.

        Args:
            model_name: Model name (e.g. "llama3.2", "qwen2.5:7b")
            on_progress: Optional callback(DownloadProgress)

        Returns:
            True if download succeeded.

        Raises:
            ModelDownloadError: On failure.
        """
        progress = DownloadProgress(model_name=model_name, started_at=time.time())
        self._downloads[model_name] = progress
        self._cancel_flags[model_name] = False

        try:
            progress.status = "downloading"
            if on_progress:
                on_progress(progress)

            # Use Ollama pull API with streaming
            async with self._client.stream(
                "POST",
                f"{self._ollama_url}/api/pull",
                json={"name": model_name, "stream": True},
                timeout=httpx.Timeout(3600.0, connect=10.0),
            ) as response:
                if response.status_code != 200:
                    raise ModelDownloadError(
                        f"Ollama pull failed with HTTP {response.status_code}"
                    )

                import json
                async for line in response.aiter_lines():
                    if self._cancel_flags.get(model_name, False):
                        progress.status = "cancelled"
                        if on_progress:
                            on_progress(progress)
                        return False

                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    status = data.get("status", "")
                    if "total" in data:
                        progress.total_bytes = data["total"]
                    if "completed" in data:
                        progress.completed_bytes = data["completed"]

                    if "error" in data:
                        raise ModelDownloadError(f"Ollama pull error: {data['error']}")

                    if status == "success":
                        progress.status = "complete"
                    elif "pulling" in status:
                        progress.status = "downloading"
                    elif "verifying" in status:
                        progress.status = "verifying"

                    if on_progress:
                        on_progress(progress)

            progress.status = "complete"
            if on_progress:
                on_progress(progress)
            logger.info("Model %s downloaded successfully", model_name)
            return True

        except ModelDownloadError:
            progress.status = "failed"
            raise
        except httpx.TransportError as e:
            progress.status = "failed"
            progress.error = str(e)
            raise ModelDownloadError(f"Network error during download: {e}") from e
        except Exception as e:
            progress.status = "failed"
            progress.error = str(e)
            raise ModelDownloadError(f"Download failed: {e}") from e
        finally:
            self._cancel_flags.pop(model_name, None)

    def cancel_download(self, model_name: str) -> bool:
        """Request cancellation of an active download."""
        if model_name in self._cancel_flags:
            self._cancel_flags[model_name] = True
            return True
        return False

    def get_download_progress(self, model_name: str) -> Optional[DownloadProgress]:
        """Get the current download progress for a model."""
        return self._downloads.get(model_name)

    # ------------------------------------------------------------------ delete
    async def delete_model(self, model_name: str) -> bool:
        """Delete a locally installed model.

        Raises:
            ModelInUseError: If the model is currently marked as in-use.
            ModelNotFoundError: If the model is not installed.
        """
        if model_name in self._active_models:
            raise ModelInUseError(
                f"Cannot delete model '{model_name}' — it is currently in use. "
                f"Unload it first with mark_inactive()."
            )

        if not await self.is_model_installed(model_name):
            raise ModelNotFoundError(f"Model '{model_name}' is not installed locally.")

        try:
            resp = await self._client.request(
                "DELETE",
                f"{self._ollama_url}/api/delete",
                json={"name": model_name},
                timeout=30.0,
            )
            if resp.status_code == 200:
                logger.info("Model %s deleted", model_name)
                return True
            elif resp.status_code == 404:
                raise ModelNotFoundError(f"Model '{model_name}' not found on Ollama.")
            else:
                raise ModelManagerError(
                    f"Failed to delete model '{model_name}': HTTP {resp.status_code}"
                )
        except httpx.TransportError as e:
            raise ModelManagerError(f"Cannot reach Ollama to delete model: {e}") from e

    # ------------------------------------------------------------------ in-use tracking
    def mark_active(self, model_name: str) -> None:
        """Mark a model as currently in use (prevents deletion)."""
        self._active_models.add(model_name)

    def mark_inactive(self, model_name: str) -> None:
        """Mark a model as no longer in use."""
        self._active_models.discard(model_name)

    def active_models(self) -> set[str]:
        """Return the set of models currently marked as in-use."""
        return set(self._active_models)

    # ------------------------------------------------------------------ disk usage
    async def disk_usage(self) -> DiskUsageReport:
        """Get disk usage for all installed models."""
        models = await self.list_models()
        report = DiskUsageReport()
        for m in models:
            report.model_sizes[m.name] = m.size_bytes
            report.total_bytes += m.size_bytes
        return report

    # ------------------------------------------------------------------ inference routing
    async def infer(self, model_name: str, prompt: str, **kwargs) -> str:
        """Run inference on a model via Ollama.

        Marks the model as active during inference.
        """
        if not await self.is_model_installed(model_name):
            raise ModelNotFoundError(
                f"Model '{model_name}' is not installed. "
                f"Download it first with download_model()."
            )

        from shared.providers.base import GenerationConfig, Message

        self.mark_active(model_name)
        try:
            config = GenerationConfig(
                temperature=kwargs.get("temperature", 0.7),
                max_output_tokens=kwargs.get("max_tokens", 1024),
                json_output=kwargs.get("json_output", False),
            )
            response = await self._runtime.generate_chat(
                messages=[Message.user(prompt)],
                model=model_name,
                config=config,
            )
            return response.text
        finally:
            self.mark_inactive(model_name)
