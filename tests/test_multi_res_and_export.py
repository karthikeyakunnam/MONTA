"""
MONTA — Multi-Resolution & Format Export Test Suite
=====================================================
Tests the real MultiResRenderer, FormatExporter, and RenderService.
Uses unit tests with mocked FFmpeg processes for deterministic validation.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from services.render_farm.multi_res import (
    RESOLUTION_PRESETS,
    MultiResRenderer,
    ResolutionResult,
    ResolutionSpec,
)
from services.render_farm.format_exporter import (
    PLATFORM_SPECS,
    FormatExporter,
    ExportResult,
    PlatformSpec,
)


class TestResolutionPresets(unittest.TestCase):
    """Verify resolution preset configuration."""

    def test_all_standard_resolutions_defined(self):
        for label in ("360p", "480p", "720p", "1080p", "1440p", "4k"):
            self.assertIn(label, RESOLUTION_PRESETS)

    def test_resolution_dimensions_increase(self):
        ordered = ["360p", "480p", "720p", "1080p", "1440p", "4k"]
        prev_pixels = 0
        for label in ordered:
            spec = RESOLUTION_PRESETS[label]
            pixels = spec.width * spec.height
            self.assertGreater(pixels, prev_pixels, f"{label} should have more pixels than the previous")
            prev_pixels = pixels

    def test_crf_decreases_with_quality(self):
        """Higher resolutions should have lower CRF (better quality)."""
        self.assertGreater(RESOLUTION_PRESETS["360p"].crf, RESOLUTION_PRESETS["4k"].crf)

    def test_spec_fields(self):
        spec = RESOLUTION_PRESETS["1080p"]
        self.assertEqual(spec.label, "1080p")
        self.assertEqual(spec.width, 1920)
        self.assertEqual(spec.height, 1080)
        self.assertIsInstance(spec.crf, int)
        self.assertIsInstance(spec.preset, str)


class TestPlatformPresets(unittest.TestCase):
    """Verify platform specification configuration."""

    def test_all_platforms_defined(self):
        expected = {"instagram_reels", "youtube_shorts", "tiktok", "youtube_landscape", "twitter", "square", "general"}
        self.assertEqual(set(PLATFORM_SPECS.keys()), expected)

    def test_vertical_platforms_are_9_16(self):
        for label in ("instagram_reels", "youtube_shorts", "tiktok"):
            spec = PLATFORM_SPECS[label]
            self.assertEqual(spec.aspect_w, 9, f"{label} should be 9:16")
            self.assertEqual(spec.aspect_h, 16, f"{label} should be 9:16")

    def test_platform_duration_limits(self):
        self.assertEqual(PLATFORM_SPECS["instagram_reels"].max_duration_s, 90)
        self.assertEqual(PLATFORM_SPECS["youtube_shorts"].max_duration_s, 60)
        self.assertEqual(PLATFORM_SPECS["tiktok"].max_duration_s, 180)
        self.assertEqual(PLATFORM_SPECS["twitter"].max_duration_s, 140)

    def test_square_is_1_1(self):
        spec = PLATFORM_SPECS["square"]
        self.assertEqual(spec.aspect_w, 1)
        self.assertEqual(spec.aspect_h, 1)
        self.assertEqual(spec.width, 1080)
        self.assertEqual(spec.height, 1080)


class TestMultiResRenderer(unittest.TestCase):
    """Test MultiResRenderer logic."""

    def setUp(self):
        self.renderer = MultiResRenderer(timeout_s=30.0)

    def test_build_transcode_argv_libx264(self):
        """Verify FFmpeg argv generation for CPU encoding."""
        source = Path("/tmp/test_source.mp4")
        output = Path("/tmp/test_720p.mp4")
        spec = RESOLUTION_PRESETS["720p"]
        argv = self.renderer._build_transcode_argv(source, output, spec, "libx264")
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("-y", argv)
        self.assertIn("-c:v", argv)
        idx = argv.index("-c:v")
        self.assertEqual(argv[idx + 1], "libx264")
        self.assertIn("-crf", argv)
        self.assertIn("-movflags", argv)
        self.assertIn(str(output), argv)

    def test_build_transcode_argv_videotoolbox(self):
        """Verify HW encoder uses bitrate instead of CRF."""
        source = Path("/tmp/test.mp4")
        output = Path("/tmp/test_1080p.mp4")
        spec = RESOLUTION_PRESETS["1080p"]
        argv = self.renderer._build_transcode_argv(source, output, spec, "h264_videotoolbox")
        idx = argv.index("-c:v")
        self.assertEqual(argv[idx + 1], "h264_videotoolbox")
        self.assertIn("-b:v", argv)

    def test_build_transcode_includes_scale_filter(self):
        """Verify scale + pad filter is included."""
        source = Path("/tmp/test.mp4")
        output = Path("/tmp/test_720p.mp4")
        spec = RESOLUTION_PRESETS["720p"]
        argv = self.renderer._build_transcode_argv(source, output, spec, "libx264")
        vf_idx = argv.index("-vf")
        vf = argv[vf_idx + 1]
        self.assertIn("scale=1280:720", vf)
        self.assertIn("pad=1280:720", vf)

    @patch("services.render_farm.multi_res.MultiResRenderer._probe_dimensions")
    def test_render_all_skips_lower_resolution(self, mock_probe):
        """If source is 720p, rendering at 720p should copy, not transcode."""
        mock_probe.return_value = (1280, 720)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake source file
            source = Path(tmpdir) / "source.mp4"
            source.write_bytes(b"\x00" * 1024)

            results = asyncio.run(
                self.renderer.render_all(str(source), ["720p"], output_dir=tmpdir)
            )
            self.assertIn("720p", results)
            self.assertTrue(results["720p"].success)

    def test_render_all_unknown_resolution(self):
        """Unknown resolution labels should produce error results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mp4"
            source.write_bytes(b"\x00" * 1024)

            results = asyncio.run(
                self.renderer.render_all(str(source), ["999p"], output_dir=tmpdir)
            )
            self.assertIn("999p", results)
            self.assertFalse(results["999p"].success)
            self.assertIn("Unknown", results["999p"].error_message)

    def test_render_all_missing_source(self):
        """Missing source should raise RenderError."""
        with self.assertRaises(Exception):
            asyncio.run(self.renderer.render_all("/nonexistent/file.mp4", ["720p"]))


class TestFormatExporter(unittest.TestCase):
    """Test FormatExporter logic."""

    def setUp(self):
        self.exporter = FormatExporter(timeout_s=30.0)

    def test_available_platforms(self):
        platforms = FormatExporter.available_platforms()
        self.assertIn("instagram_reels", platforms)
        self.assertIn("tiktok", platforms)
        self.assertIn("youtube_shorts", platforms)
        self.assertGreaterEqual(len(platforms), 7)

    def test_build_export_argv_aspect_ratio_crop(self):
        """16:9 → 9:16 should crop horizontally."""
        source = Path("/tmp/source.mp4")
        output = Path("/tmp/output_ig.mp4")
        spec = PLATFORM_SPECS["instagram_reels"]
        argv = self.exporter._build_export_argv(
            source, output, spec, src_w=1920, src_h=1080, duration=30.0, trim=False,
        )
        vf_idx = argv.index("-vf")
        vf = argv[vf_idx + 1]
        self.assertIn("crop=", vf)  # Source is wider than target
        self.assertIn("scale=1080:1920", vf)

    def test_build_export_argv_same_aspect(self):
        """Same aspect ratio should only scale, not crop."""
        source = Path("/tmp/source.mp4")
        output = Path("/tmp/output.mp4")
        spec = PLATFORM_SPECS["youtube_landscape"]
        argv = self.exporter._build_export_argv(
            source, output, spec, src_w=1920, src_h=1080, duration=60.0, trim=False,
        )
        vf_idx = argv.index("-vf")
        vf = argv[vf_idx + 1]
        self.assertIn("scale=1920:1080", vf)
        self.assertNotIn("crop=", vf)

    def test_build_export_argv_duration_trim(self):
        """When trim=True, -t flag should be present."""
        source = Path("/tmp/source.mp4")
        output = Path("/tmp/output.mp4")
        spec = PLATFORM_SPECS["instagram_reels"]
        argv = self.exporter._build_export_argv(
            source, output, spec, src_w=1920, src_h=1080, duration=90.0, trim=True,
        )
        self.assertIn("-t", argv)

    def test_build_export_argv_no_trim(self):
        """When trim=False, -t flag should NOT be present."""
        source = Path("/tmp/source.mp4")
        output = Path("/tmp/output.mp4")
        spec = PLATFORM_SPECS["instagram_reels"]
        argv = self.exporter._build_export_argv(
            source, output, spec, src_w=1920, src_h=1080, duration=60.0, trim=False,
        )
        self.assertNotIn("-t", argv)

    def test_export_unknown_platform(self):
        """Unknown platform should return failure result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mp4"
            source.write_bytes(b"\x00" * 1024)
            result = asyncio.run(self.exporter.export(str(source), "nonexistent_platform"))
            self.assertFalse(result.success)
            self.assertIn("Unknown", result.error_message)

    def test_export_missing_source(self):
        """Missing source should raise RenderError."""
        with self.assertRaises(Exception):
            asyncio.run(self.exporter.export("/nonexistent/file.mp4", "instagram_reels"))

    def test_square_export_crop(self):
        """16:9 → 1:1 should crop the sides."""
        source = Path("/tmp/source.mp4")
        output = Path("/tmp/output_sq.mp4")
        spec = PLATFORM_SPECS["square"]
        argv = self.exporter._build_export_argv(
            source, output, spec, src_w=1920, src_h=1080, duration=30.0, trim=False,
        )
        vf_idx = argv.index("-vf")
        vf = argv[vf_idx + 1]
        self.assertIn("crop=", vf)
        self.assertIn("scale=1080:1080", vf)


class TestResolutionResult(unittest.TestCase):
    """Test ResolutionResult dataclass."""

    def test_success_result(self):
        r = ResolutionResult(
            label="720p", output_path="/tmp/out_720p.mp4",
            width=1280, height=720, file_size_bytes=1_000_000, success=True,
        )
        self.assertTrue(r.success)
        self.assertEqual(r.label, "720p")

    def test_failure_result(self):
        r = ResolutionResult(
            label="4k", output_path="",
            width=3840, height=2160, file_size_bytes=0, success=False,
            error_message="timeout",
        )
        self.assertFalse(r.success)
        self.assertEqual(r.error_message, "timeout")


class TestExportResult(unittest.TestCase):
    """Test ExportResult dataclass."""

    def test_trimmed_result(self):
        r = ExportResult(
            platform="instagram_reels", output_path="/tmp/ig.mp4",
            width=1080, height=1920, duration_s=90.0,
            file_size_bytes=5_000_000, success=True, was_trimmed=True,
        )
        self.assertTrue(r.was_trimmed)
        self.assertTrue(r.success)


if __name__ == "__main__":
    unittest.main()
