"""video_composer 单元测试（无需真实 FFmpeg 文件）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.video_composer import (
    MontageComposerError,
    _finalize_mp4_for_common_players,
    _is_hard_cut,
    _normalized_audio_filter,
    _select_audible_audio_stream,
    _validate_finalized_mp4,
    build_bgm_filter,
    compose_montage,
    inspect_media_audio,
    resolve_ffmpeg_binary,
    validate_output_path,
)


class TestValidateOutput(unittest.TestCase):
    def test_rejects_relative(self):
        with self.assertRaises(MontageComposerError):
            validate_output_path("out.mp4")

    def test_rejects_non_mp4(self):
        with self.assertRaises(MontageComposerError):
            validate_output_path("C:\\a\\b.mkv")

    def test_accepts_absolute_mp4(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "x.mp4"
            out = validate_output_path(str(p))
            self.assertTrue(out.is_absolute())
            self.assertTrue(out.parent.is_dir())


class TestResolveFfmpeg(unittest.TestCase):
    def test_missing_config_and_path(self):
        with patch("app.video_composer.shutil.which", return_value=None):
            with self.assertRaises(MontageComposerError):
                resolve_ffmpeg_binary("")

    def test_config_path_invalid(self):
        with self.assertRaises(MontageComposerError):
            resolve_ffmpeg_binary("__no_such_ffmpeg__.exe")


class TestResolveFfmpegBundled(unittest.TestCase):
    def test_bundled_third_party_before_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "third_party" / "ffmpeg").mkdir(parents=True)
            exe = root / "third_party" / "ffmpeg" / "ffmpeg.exe"
            exe.write_bytes(b"")
            data = root / "data"
            data.mkdir()

            def fake_get_data_dir():
                return data

            with patch("app.env_utils.get_data_dir", fake_get_data_dir):
                with patch("app.video_composer.shutil.which", return_value=None):
                    p = resolve_ffmpeg_binary("")
                    self.assertEqual(p.resolve(), exe.resolve())


class TestBgmFilter(unittest.TestCase):
    def test_contains_loop_and_trim(self):
        s = build_bgm_filter(120.5)
        self.assertIn("aloop", s)
        self.assertIn("atrim=0:120.500000", s)


class TestAudioStreamSelection(unittest.TestCase):
    def test_prefers_audible_first_stream_even_when_later_stream_is_louder(self):
        stderr = """
[Parsed_volumedetect_0 @ 000001] mean_volume: -24.0 dB
[Parsed_volumedetect_0 @ 000001] max_volume: -3.0 dB
[Parsed_volumedetect_1 @ 000002] mean_volume: -18.0 dB
[Parsed_volumedetect_1 @ 000002] max_volume: 1.2 dB
[Parsed_volumedetect_2 @ 000003] mean_volume: -12.0 dB
[Parsed_volumedetect_2 @ 000003] max_volume: 0.5 dB
[Parsed_volumedetect_3 @ 000004] mean_volume: -8.0 dB
[Parsed_volumedetect_3 @ 000004] max_volume: 1.5 dB
"""
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)
        with patch("app.video_composer.subprocess.run", return_value=completed):
            selected = _select_audible_audio_stream(
                Path("ffmpeg.exe"),
                Path("obs-four-tracks.mp4"),
                [1, 2, 3, 4],
            )

        self.assertEqual(selected, 1)

    def test_selects_audible_stream_instead_of_first_silent_stream(self):
        stderr = """
[Parsed_volumedetect_0 @ 000001] mean_volume: -91.0 dB
[Parsed_volumedetect_0 @ 000001] max_volume: -91.0 dB
[Parsed_volumedetect_1 @ 000002] mean_volume: -20.0 dB
[Parsed_volumedetect_1 @ 000002] max_volume: -4.0 dB
"""
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)
        with patch("app.video_composer.subprocess.run", return_value=completed) as run:
            selected = _select_audible_audio_stream(
                Path("ffmpeg.exe"),
                Path("clip.mp4"),
                [1, 3],
            )

        self.assertEqual(selected, 3)
        cmd = run.call_args.args[0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:1]volumedetect[aud0]", graph)
        self.assertIn("[0:3]volumedetect[aud1]", graph)

    def test_all_silent_streams_return_none(self):
        stderr = """
[Parsed_volumedetect_0 @ 000001] mean_volume: -91.0 dB
[Parsed_volumedetect_0 @ 000001] max_volume: -91.0 dB
[Parsed_volumedetect_1 @ 000002] mean_volume: -91.0 dB
[Parsed_volumedetect_1 @ 000002] max_volume: -91.0 dB
"""
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)
        with patch("app.video_composer.subprocess.run", return_value=completed):
            selected = _select_audible_audio_stream(
                Path("ffmpeg.exe"),
                Path("clip.mp4"),
                [1, 3],
            )

        self.assertIsNone(selected)

    def test_single_transient_does_not_count_as_audible_program_audio(self):
        # FFmpeg volumedetect values measured from a five-second track with one
        # full-scale sample followed by digital silence.
        stderr = """
[Parsed_volumedetect_0 @ 000001] mean_volume: -53.8 dB
[Parsed_volumedetect_0 @ 000001] max_volume: 0.0 dB
"""
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)
        with patch("app.video_composer.subprocess.run", return_value=completed):
            selected = _select_audible_audio_stream(
                Path("ffmpeg.exe"),
                Path("click-only.mp4"),
                [1],
            )

        self.assertIsNone(selected)

    def test_normalization_maps_exact_selected_global_stream(self):
        graph = _normalized_audio_filter(3, 2.0)
        self.assertIn("[0:3]", graph)
        self.assertNotIn("[0:a]", graph)

    def test_media_audio_inspection_reports_selected_stream(self):
        summary = {"audio_stream_indices": [1, 3]}
        with (
            patch("app.video_composer.resolve_ffprobe_binary", return_value=Path("ffprobe.exe")),
            patch("app.video_composer.probe_video_audio_summary", return_value=summary),
            patch("app.video_composer._select_audible_audio_stream", return_value=3),
        ):
            health = inspect_media_audio(Path("ffmpeg.exe"), Path("clip.mp4"))

        self.assertEqual(health["status"], "audible")
        self.assertTrue(health["audible"])
        self.assertEqual(health["stream_index"], 3)
        self.assertEqual(health["audio_stream_count"], 2)


class TestHardCut(unittest.TestCase):
    def test_cut_is_hard_even_with_nonzero_default_duration(self):
        self.assertTrue(_is_hard_cut("cut", 0.25, 60.0))
        self.assertTrue(_is_hard_cut("none", 0.25, 60.0))
        self.assertFalse(_is_hard_cut("fade", 0.25, 60.0))


class TestFinalizeMp4(unittest.TestCase):
    def test_ffprobe_timeout_marks_copy_invalid_instead_of_aborting(self):
        with tempfile.TemporaryDirectory() as td:
            final = Path(td) / "final.mp4"
            final.write_bytes(b"not-empty")
            with patch(
                "app.video_composer.probe_video_audio_summary",
                side_effect=subprocess.TimeoutExpired(["ffprobe"], 120),
            ) as probe:
                self.assertFalse(
                    _validate_finalized_mp4(
                        Path(td) / "mid.mp4",
                        final,
                        Path("ffprobe.exe"),
                    )
                )
            probe.assert_called_once()

    def test_uses_stream_copy_when_copy_passes_ffprobe_validation(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("app.video_composer.subprocess.run", return_value=completed) as run:
                with patch("app.video_composer._validate_finalized_mp4", return_value=True):
                    mode = _finalize_mp4_for_common_players(
                        Path("ffmpeg.exe"),
                        Path("ffprobe.exe"),
                        root / "mid.mp4",
                        root / "final.mp4",
                        ["-c:v", "libx264"],
                    )

        self.assertEqual(mode, "stream_copy")
        self.assertEqual(run.call_count, 1)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_falls_back_to_reencode_when_copy_fails_validation(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("app.video_composer.subprocess.run", return_value=completed) as run:
                with patch(
                    "app.video_composer._validate_finalized_mp4",
                    side_effect=[False, True],
                ) as validate:
                    mode = _finalize_mp4_for_common_players(
                        Path("ffmpeg.exe"),
                        Path("ffprobe.exe"),
                        root / "mid.mp4",
                        root / "final.mp4",
                        ["-c:v", "libx264"],
                    )

        self.assertEqual(mode, "reencode")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(validate.call_count, 2)
        fallback_cmd = run.call_args_list[1].args[0]
        self.assertIn("libx264", fallback_cmd)


class TestComposeMontageAudioAndProgress(unittest.TestCase):
    @staticmethod
    def _summary() -> dict:
        return {
            "width": 1920,
            "height": 1080,
            "fps": 60.0,
            "has_audio": True,
            "audio_stream_indices": [1, 3],
            "duration": 1.0,
        }

    def test_selected_stream_is_used_and_progress_reports_all_stages(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        progress: list[dict] = []

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clip = root / "clip.mp4"
            clip.write_bytes(b"clip")
            output = root / "montage.mp4"

            def fake_finalize(_ffmpeg, _ffprobe, _src, dst, _encode):
                dst.write_bytes(b"final")
                return "stream_copy"

            with (
                patch("app.video_composer.resolve_h264_codec_name", return_value="libx264"),
                patch("app.video_composer.h264_encode_cli_args", return_value=["-c:v", "libx264"]),
                patch("app.video_composer.resolve_ffprobe_binary", return_value=Path("ffprobe.exe")),
                patch("app.video_composer.resolve_name_card_font", return_value=None),
                patch("app.video_composer.resolve_rajdhani_fonts", return_value=(None, None)),
                patch("app.video_composer.probe_video_audio_summary", return_value=self._summary()),
                patch("app.video_composer._select_audible_audio_stream", return_value=3),
                patch("app.video_composer.subprocess.run", return_value=completed) as run,
                patch("app.video_composer._finalize_mp4_for_common_players", side_effect=fake_finalize),
                patch(
                    "app.video_composer.ffprobe_streams",
                    return_value={"format": {"duration": "1.0"}},
                ),
            ):
                compose_montage(
                    ffmpeg_bin=Path("ffmpeg.exe"),
                    clip_paths=[clip],
                    intro_path=None,
                    outro_path=None,
                    bgm_path=None,
                    output_path=output,
                    progress_callback=progress.append,
                )

            self.assertTrue(output.is_file())

        normalize_cmd = next(
            call.args[0]
            for call in run.call_args_list
            if "-filter_complex" in call.args[0]
            and any("norm_000.ts" in str(part) for part in call.args[0])
        )
        graph = normalize_cmd[normalize_cmd.index("-filter_complex") + 1]
        self.assertIn("[0:3]aresample", graph)
        stages = {update["stage"] for update in progress}
        self.assertTrue(
            {"validate", "audio_preflight", "normalize", "transitions", "concat", "finalize", "done"}
            <= stages
        )

    def test_all_silent_recording_fails_before_encoding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clip = root / "silent.mp4"
            clip.write_bytes(b"clip")

            with (
                patch("app.video_composer.resolve_h264_codec_name", return_value="libx264"),
                patch("app.video_composer.h264_encode_cli_args", return_value=["-c:v", "libx264"]),
                patch("app.video_composer.resolve_ffprobe_binary", return_value=Path("ffprobe.exe")),
                patch("app.video_composer.resolve_name_card_font", return_value=None),
                patch("app.video_composer.resolve_rajdhani_fonts", return_value=(None, None)),
                patch("app.video_composer.probe_video_audio_summary", return_value=self._summary()),
                patch("app.video_composer._select_audible_audio_stream", return_value=None),
                patch("app.video_composer.subprocess.run") as run,
            ):
                with self.assertRaises(MontageComposerError) as raised:
                    compose_montage(
                        ffmpeg_bin=Path("ffmpeg.exe"),
                        clip_paths=[clip],
                        intro_path=None,
                        outro_path=None,
                        bgm_path=None,
                        output_path=root / "montage.mp4",
                    )

        self.assertEqual(raised.exception.code, "MONTAGE_CLIP_AUDIO_SILENT")
        self.assertEqual(raised.exception.params["name"], "silent.mp4")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
