"""
MONTA — Signal Analysis (Layer 6, model-free)
===============================================
Deterministic pixel statistics over sampled grayscale frames:

* exposure / contrast / clipping
* sharpness — variance of the Laplacian (blur detector)
* motion — mean absolute frame difference (subject + camera activity)
* global camera motion — median of 2×2 tile phase correlations between
  consecutive frames (robust to subject motion), giving speed
  (frame-widths/s), jitter and direction consistency
* hard cuts — outlier frame differences

These measurements ground quality, energy, lighting and camera motion in
evidence a vision model cannot hallucinate, and they work with no model at all.

Production note: thresholds are calibrated for 320px-wide luma at ≤4 fps.
Re-fit ``QUALITY_*``/``ENERGY_*`` constants against a labelled clip set when
changing sampling parameters, and bump ``SIGNAL_VERSION`` (it keys the cache).
"""

import math

import numpy as np

from shared.contracts.clip import SignalMetrics
from shared.contracts.explain import clamp
from shared.contracts.vocab import CameraMotion, Lighting

SIGNAL_VERSION = "signals.v2"
ENERGY_CURVE_POINTS = 24


def _laplacian_var(f: np.ndarray) -> np.ndarray:
    lap = -4 * f[:, 1:-1, 1:-1] + f[:, :-2, 1:-1] + f[:, 2:, 1:-1] + f[:, 1:-1, :-2] + f[:, 1:-1, 2:]
    return lap.reshape(len(f), -1).var(axis=1)


def _phase_shift(a: np.ndarray, b: np.ndarray, window: np.ndarray) -> tuple[float, float]:
    """Integer (dy, dx) translation of b relative to a via phase correlation."""
    fa = np.fft.fft2(a * window)
    fb = np.fft.fft2(b * window)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return float(-dy), float(-dx)


def _robust_shift(a: np.ndarray, b: np.ndarray, windows: dict[tuple[int, int], np.ndarray]) -> tuple[float, float]:
    """Global camera shift as the median of 2×2 tile phase correlations.

    A moving subject corrupts the correlation of the tile it occupies but not the median of four,
    so camera pans are measured from the background rather than from whatever moves in frame.
    """
    h, w = a.shape
    th, tw = h // 2, w // 2
    shifts = []
    for y in (0, th):
        for x in (0, tw):
            ta, tb = a[y:y + th, x:x + tw], b[y:y + th, x:x + tw]
            win = windows.setdefault(ta.shape, np.outer(np.hanning(ta.shape[0]), np.hanning(ta.shape[1])).astype(np.float32))
            shifts.append(_phase_shift(ta, tb, win))
    arr = np.array(shifts)
    return float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))


def _downsample(values: np.ndarray, points: int) -> tuple[float, ...]:
    if len(values) == 0:
        return ()
    chunks = np.array_split(values, min(points, len(values)))
    return tuple(round(float(c.mean()), 4) for c in chunks)


def compute_signals(frames: np.ndarray, sample_fps: float) -> SignalMetrics:
    """Measure a (n, h, w) uint8 luma stack sampled at ``sample_fps``."""
    if frames.ndim != 3 or len(frames) == 0:
        raise ValueError("frames must be a non-empty (n, h, w) array")
    f = frames.astype(np.float32) / 255.0
    n, h, w = f.shape

    brightness = float(f.mean())
    contrast = float(f.reshape(n, -1).std(axis=1).mean())
    clipped = float(((f < 0.02) | (f > 0.98)).mean())
    sharpness = float(np.median(_laplacian_var(f)))

    motion_mean = motion_std = speed = jitter = consistency = 0.0
    axis, cuts, peak_t = "none", 0, 0.0
    curve: tuple[float, ...] = ()
    if n >= 2:
        diffs = np.abs(f[1:] - f[:-1]).mean(axis=(1, 2))
        med = float(np.median(diffs))
        mad = float(np.median(np.abs(diffs - med)))
        cut_mask = diffs > max(0.18, med + 6 * mad + 1e-6)
        cuts = int(cut_mask.sum())
        steady = diffs[~cut_mask] if (~cut_mask).any() else diffs
        motion_mean, motion_std = float(steady.mean()), float(steady.std())

        smooth = np.convolve(diffs, np.ones(3) / 3, mode="same") if len(diffs) >= 3 else diffs
        peak_t = float((int(np.argmax(smooth)) + 1) / sample_fps)
        curve = _downsample(np.clip(diffs / 0.12, 0, 1), ENERGY_CURVE_POINTS)

        windows: dict[tuple[int, int], np.ndarray] = {}
        shifts = np.array([
            _robust_shift(f[i], f[i + 1], windows) for i in range(n - 1) if not cut_mask[i]
        ]).reshape(-1, 2)
        if len(shifts):
            mags = np.hypot(shifts[:, 0], shifts[:, 1])
            speed = float(mags.mean() / w * sample_fps)
            if len(shifts) >= 2:
                jitter = float(np.hypot(*np.diff(shifts, axis=0).T).mean() / w * sample_fps)
            mean_vec = shifts.mean(axis=0)
            consistency = float(clamp(np.hypot(*mean_vec) / (mags.mean() + 1e-9))) if mags.mean() > 0.25 else 0.0
            ady, adx = np.abs(shifts[:, 0]).mean(), np.abs(shifts[:, 1]).mean()
            if max(ady, adx) > 0.25:
                axis = "horizontal" if adx > 1.5 * ady else "vertical" if ady > 1.5 * adx else "none"

    return SignalMetrics(
        signal_version=SIGNAL_VERSION, sample_fps=sample_fps, frames_analyzed=n,
        brightness_mean=round(brightness, 4), contrast=round(contrast, 4), clipped_ratio=round(clipped, 4),
        sharpness=round(sharpness, 7), motion_mean=round(motion_mean, 5), motion_std=round(motion_std, 5),
        camera_speed=round(speed, 4), camera_jitter=round(jitter, 4), direction_consistency=round(consistency, 3),
        dominant_axis=axis, cut_count=cuts, peak_time_s=round(peak_t, 3), energy_curve=curve,
    )


# ---------------------------------------------------------------------------- interpretation


def quality_from_signals(m: SignalMetrics) -> tuple[float, float, str]:
    """0–10 technical quality, confidence, reasoning."""
    sharp_s = clamp((math.log10(max(m.sharpness, 1e-7)) + 3.7) / 1.7)
    exposure_s = clamp(1 - abs(m.brightness_mean - 0.48) / 0.4) * (1 - clamp(m.clipped_ratio * 3))
    stability_s = 1 - clamp(m.camera_jitter / 0.4)
    contrast_s = clamp(m.contrast / 0.2)
    score = 10 * (0.4 * sharp_s + 0.25 * exposure_s + 0.2 * stability_s + 0.15 * contrast_s)
    confidence = clamp(0.5 + 0.4 * min(1.0, m.frames_analyzed / 12), 0, 0.9)
    reasoning = (
        f"sharpness {sharp_s:.2f}, exposure {exposure_s:.2f}, stability {stability_s:.2f}, "
        f"contrast {contrast_s:.2f} over {m.frames_analyzed} frames"
    )
    return round(score, 2), round(confidence, 3), reasoning


def energy_from_signals(m: SignalMetrics, duration_s: float) -> tuple[float, float, str]:
    """0–10 visual energy, confidence, reasoning. Visual-only: blind to audio and semantic intensity."""
    motion_s = clamp(m.motion_mean / 0.1)
    speed_s = clamp(m.camera_speed / 0.5)
    var_s = clamp(m.motion_std / 0.06)
    cut_s = clamp((m.cut_count / max(duration_s, 1e-3)) / 0.5)
    score = 10 * (0.45 * motion_s + 0.25 * speed_s + 0.15 * var_s + 0.15 * cut_s)
    confidence = clamp(0.35 + 0.35 * min(1.0, m.frames_analyzed / 12), 0, 0.7)
    reasoning = f"motion {motion_s:.2f}, camera speed {speed_s:.2f}, variability {var_s:.2f}, cut rate {cut_s:.2f}"
    return round(score, 2), round(confidence, 3), reasoning


def lighting_from_signals(m: SignalMetrics) -> tuple[Lighting, str]:
    if m.brightness_mean < 0.2:
        return Lighting.LOW, f"mean luma {m.brightness_mean:.2f} < 0.20"
    if m.brightness_mean > 0.72 or (m.clipped_ratio > 0.12 and m.brightness_mean > 0.55):
        return Lighting.OVEREXPOSED, f"mean luma {m.brightness_mean:.2f}, clipped {m.clipped_ratio:.0%}"
    if m.contrast < 0.05:
        return Lighting.FLAT, f"contrast {m.contrast:.3f} < 0.05"
    return Lighting.GOOD, f"mean luma {m.brightness_mean:.2f}, contrast {m.contrast:.2f}"


def camera_motion_from_signals(m: SignalMetrics) -> tuple[CameraMotion, str]:
    s, j, c = m.camera_speed, m.camera_jitter, m.direction_consistency
    detail = f"speed {s:.3f} w/s, jitter {j:.3f} w/s, consistency {c:.2f}"
    if m.frames_analyzed < 2:
        return CameraMotion.UNKNOWN, "fewer than 2 frames"
    if s < 0.03 and j < 0.05:
        return CameraMotion.STATIC, detail
    if c >= 0.65 and s >= 0.05:
        if s > 0.6:
            return CameraMotion.DYNAMIC, detail
        motion = {"horizontal": CameraMotion.PAN, "vertical": CameraMotion.TILT}.get(m.dominant_axis, CameraMotion.TRACKING)
        return motion, detail
    if j >= 0.2:
        return CameraMotion.SHAKY, detail
    if m.motion_mean > 0.08 or s > 0.4:
        return CameraMotion.DYNAMIC, detail
    return CameraMotion.HANDHELD, detail
