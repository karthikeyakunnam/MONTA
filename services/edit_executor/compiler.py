"""
MONTA — Deterministic FFmpeg Compiler
======================================
Compiles a validated Timeline IR into a safe, structured FFmpeg argv command list
and complex filtergraph. Never executes shell strings.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from shared.contracts.timeline import (
    ScaleMode,
    TimelineIR,
    TransitionType,
    VideoSegmentIR,
)
from shared.hardware.profile import DeviceType, HardwareProfile, VideoEncoder


@dataclass(frozen=True)
class FFmpegCommand:
    """Structured FFmpeg invocation specification."""

    argv: tuple[str, ...]
    input_files: tuple[str, ...]
    output_path: str
    expected_duration_s: float
    filter_graph: str
    encoder_used: str

    def to_list(self) -> list[str]:
        return list(self.argv)


class FFmpegCompiler:
    """Compiles Timeline IR into deterministic FFmpeg arguments."""

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        hardware_profile: Optional[HardwareProfile] = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.hardware = hardware_profile

    def compile(
        self,
        timeline: TimelineIR,
        output_path: str | Path,
        force_cpu: bool = False,
    ) -> FFmpegCommand:
        """Translates Timeline IR into a structured FFmpeg argv execution array."""
        out_path = str(Path(output_path).resolve())
        unique_inputs: list[str] = []
        input_index_map: dict[str, int] = {}

        # Collect unique input video files
        for seg in timeline.video_segments:
            if seg.source_path not in input_index_map:
                input_index_map[seg.source_path] = len(unique_inputs)
                unique_inputs.append(seg.source_path)

        # Collect unique external audio files
        for audio in timeline.audio_segments:
            if audio.source_path not in input_index_map:
                input_index_map[audio.source_path] = len(unique_inputs)
                unique_inputs.append(audio.source_path)

        # Build filtergraph
        filters: list[str] = []
        video_nodes: list[str] = []
        audio_nodes: list[str] = []

        w = timeline.width
        h = timeline.height
        fps = timeline.fps

        # Process each video segment
        for i, seg in enumerate(timeline.video_segments):
            in_idx = input_index_map[seg.source_path]
            start_s = seg.source_in_ms / 1000.0
            end_s = seg.source_out_ms / 1000.0
            v_node = f"v{i}"
            a_node = f"a{i}"

            # 1. Video stream filters
            v_chain = [f"[{in_idx}:v]trim=start={start_s:.4f}:end={end_s:.4f}"]
            v_chain.append("setpts=PTS-STARTPTS")

            # Speed change
            if abs(seg.speed - 1.0) > 0.001:
                v_chain.append(f"setpts={1.0 / seg.speed:.4f}*PTS")

            # Scale and aspect ratio
            if timeline.scale_mode == ScaleMode.FILL:
                v_chain.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1")
            elif timeline.scale_mode == ScaleMode.STRETCH:
                v_chain.append(f"scale={w}:{h},setsar=1")
            else:  # FIT default
                v_chain.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")

            v_chain.append(f"fps={fps:.4f}")

            # Color adjustments
            if seg.color is not None:
                eq_parts = []
                if seg.color.brightness != 0.0:
                    eq_parts.append(f"brightness={seg.color.brightness:.3f}")
                if seg.color.contrast != 1.0:
                    eq_parts.append(f"contrast={seg.color.contrast:.3f}")
                if seg.color.saturation != 1.0:
                    eq_parts.append(f"saturation={seg.color.saturation:.3f}")
                if seg.color.gamma != 1.0:
                    eq_parts.append(f"gamma={seg.color.gamma:.3f}")
                if eq_parts:
                    v_chain.append(f"eq={':'.join(eq_parts)}")

            # Video Fades
            if seg.fade_in_ms > 0:
                d = seg.fade_in_ms / 1000.0
                v_chain.append(f"fade=t=in:st=0:d={d:.3f}")
            if seg.fade_out_ms > 0:
                d = seg.fade_out_ms / 1000.0
                dur = seg.timeline_duration_ms / 1000.0
                st = max(0.0, dur - d)
                v_chain.append(f"fade=t=out:st={st:.3f}:d={d:.3f}")

            v_chain.append("format=yuv420p")
            filters.append(f"{','.join(v_chain)}[{v_node}]")
            video_nodes.append(f"[{v_node}]")

            # 2. Audio stream filters
            a_chain = [f"[{in_idx}:a]atrim=start={start_s:.4f}:end={end_s:.4f}"]
            a_chain.append("asetpts=PTS-STARTPTS")

            # Audio speed (atempo handles 0.5 to 2.0; chain if needed)
            if abs(seg.speed - 1.0) > 0.001:
                a_chain.extend(self._build_atempo_filters(seg.speed))

            # Audio volume
            if abs(seg.volume - 1.0) > 0.001:
                a_chain.append(f"volume={seg.volume:.3f}")

            # Audio Fades
            if seg.fade_in_ms > 0:
                d = seg.fade_in_ms / 1000.0
                a_chain.append(f"afade=t=in:ss=0:d={d:.3f}")
            if seg.fade_out_ms > 0:
                d = seg.fade_out_ms / 1000.0
                dur = seg.timeline_duration_ms / 1000.0
                st = max(0.0, dur - d)
                a_chain.append(f"afade=t=out:st={st:.3f}:d={d:.3f}")

            a_chain.append("aformat=sample_rates=48000:channel_layouts=stereo")
            filters.append(f"{','.join(a_chain)}[{a_node}]")
            audio_nodes.append(f"[{a_node}]")

        # 3. Concatenation / Transitions
        has_xfade = any(
            seg.transition_in == TransitionType.CROSSFADE and seg.transition_in_ms > 0
            for seg in timeline.video_segments[1:]
        )

        final_v = "v_concat"
        final_a = "a_concat"

        if has_xfade and len(timeline.video_segments) > 1:
            # Construct progressive xfade chain
            cur_v = video_nodes[0]
            cur_a = audio_nodes[0]
            offset = timeline.video_segments[0].timeline_duration_ms / 1000.0

            for j in range(1, len(timeline.video_segments)):
                seg = timeline.video_segments[j]
                next_v = video_nodes[j]
                next_a = audio_nodes[j]
                nxt_v_out = f"xfv_{j}"
                nxt_a_out = f"xfa_{j}"

                trans_dur = (seg.transition_in_ms / 1000.0) if (seg.transition_in == TransitionType.CROSSFADE and seg.transition_in_ms > 0) else 0.001
                xfade_offset = max(0.0, offset - trans_dur)

                filters.append(
                    f"{cur_v}{next_v}xfade=transition=fade:duration={trans_dur:.3f}:offset={xfade_offset:.3f}[{nxt_v_out}]"
                )
                filters.append(
                    f"{cur_a}{next_a}acrossfade=d={trans_dur:.3f}[{nxt_a_out}]"
                )

                cur_v = f"[{nxt_v_out}]"
                cur_a = f"[{nxt_a_out}]"
                offset = xfade_offset + (seg.timeline_duration_ms / 1000.0)

            final_v = cur_v.strip("[]")
            final_a = cur_a.strip("[]")
        else:
            # Standard seamless concat filter
            concat_inputs = "".join(f"{v}{a}" for v, a in zip(video_nodes, audio_nodes))
            num_segs = len(timeline.video_segments)
            filters.append(f"{concat_inputs}concat=n={num_segs}:v=1:a=1[{final_v}][{final_a}]")

        # 4. External Background Audio Tracks (if present)
        final_out_v = final_v
        final_out_a = final_a

        if timeline.audio_segments:
            ext_audio_nodes = []
            for k, bg_audio in enumerate(timeline.audio_segments):
                in_idx = input_index_map[bg_audio.source_path]
                bg_node = f"bg_a{k}"
                bg_start = bg_audio.source_in_ms / 1000.0
                bg_dur = (bg_audio.timeline_end_ms - bg_audio.timeline_start_ms) / 1000.0
                
                bg_chain = [f"[{in_idx}:a]atrim=start={bg_start:.4f}:duration={bg_dur:.4f}"]
                bg_chain.append("asetpts=PTS-STARTPTS")
                if abs(bg_audio.volume - 1.0) > 0.001:
                    bg_chain.append(f"volume={bg_audio.volume:.3f}")
                if bg_audio.fade_in_ms > 0:
                    bg_chain.append(f"afade=t=in:ss=0:d={bg_audio.fade_in_ms / 1000.0:.3f}")
                if bg_audio.fade_out_ms > 0:
                    st = max(0.0, bg_dur - (bg_audio.fade_out_ms / 1000.0))
                    bg_chain.append(f"afade=t=out:st={st:.3f}:d={bg_audio.fade_out_ms / 1000.0:.3f}")
                bg_chain.append("aformat=sample_rates=48000:channel_layouts=stereo")
                filters.append(f"{','.join(bg_chain)}[{bg_node}]")
                ext_audio_nodes.append(f"[{bg_node}]")

            # Mix timeline audio with external background tracks
            all_a_nodes = f"[{final_a}]" + "".join(ext_audio_nodes)
            filters.append(f"{all_a_nodes}amix=inputs={1 + len(ext_audio_nodes)}:duration=longest:dropout_transition=0[a_mixed]")
            final_out_a = "a_mixed"

        full_filtergraph = ";".join(filters)

        # 5. Determine encoder and parameters
        encoder = self._resolve_encoder(timeline, force_cpu=force_cpu)
        encoding_args = self._build_encoder_args(encoder, timeline)

        # 6. Build command array
        argv = [self.ffmpeg_bin, "-y", "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe"]

        for inp in unique_inputs:
            argv.extend(["-i", inp])

        argv.extend([
            "-filter_complex", full_filtergraph,
            "-map", f"[{final_out_v}]",
            "-map", f"[{final_out_a}]",
            *encoding_args,
            out_path,
        ])

        return FFmpegCommand(
            argv=tuple(argv),
            input_files=tuple(unique_inputs),
            output_path=out_path,
            expected_duration_s=timeline.duration_s,
            filter_graph=full_filtergraph,
            encoder_used=encoder,
        )

    def _build_atempo_filters(self, speed: float) -> list[str]:
        """FFmpeg atempo filter accepts values between 0.5 and 2.0. Chains filters for extreme speeds."""
        filters = []
        rem = speed
        while rem > 2.0:
            filters.append("atempo=2.0")
            rem /= 2.0
        while rem < 0.5:
            filters.append("atempo=0.5")
            rem /= 0.5
        filters.append(f"atempo={rem:.4f}")
        return filters

    def _resolve_encoder(self, timeline: TimelineIR, force_cpu: bool = False) -> str:
        if force_cpu:
            return timeline.render_profile.fallback_encoder or "libx264"
        if timeline.render_profile.preferred_encoder:
            return timeline.render_profile.preferred_encoder
        if self.hardware:
            return self.hardware.recommended_encoder.value
        return "libx264"

    def _build_encoder_args(self, encoder: str, timeline: TimelineIR) -> list[str]:
        prof = timeline.render_profile
        args: list[str] = ["-c:v", encoder]

        if encoder == "h264_videotoolbox":
            args.extend(["-b:v", prof.video_bitrate or "8000k", "-pix_fmt", "yuv420p"])
        elif encoder == "h264_nvenc":
            args.extend(["-preset", prof.preset or "p4", "-cq", str(prof.crf or 23), "-pix_fmt", "yuv420p"])
        elif encoder == "h264_vaapi":
            args.extend(["-qp", str(prof.crf or 23)])
        else:  # libx264 fallback
            args.extend([
                "-preset", prof.preset or "medium",
                "-crf", str(prof.crf or 23),
                "-pix_fmt", "yuv420p",
            ])

        args.extend([
            "-c:a", "aac",
            "-b:a", prof.audio_bitrate,
            "-ar", str(prof.audio_sample_rate),
            "-movflags", "+faststart",
        ])

        return args
