"""Post-record OBS file audio checks."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException

from app.env_utils import OBSConfig
from app.recording.api import _annotate_v3_audio_health, _require_obs_audio_ready


def test_silent_recording_is_annotated_and_warned() -> None:
    results = [{"success": True, "output_path": "C:\\Videos\\silent.mp4", "warnings": []}]
    health = {
        "status": "silent",
        "audible": False,
        "stream_index": None,
        "audio_stream_count": 4,
    }

    with (
        patch("app.video_composer.resolve_ffmpeg_binary", return_value=Path("ffmpeg.exe")),
        patch("app.video_composer.inspect_media_audio", return_value=health) as inspect,
    ):
        asyncio.run(_annotate_v3_audio_health(results, None))

    assert results[0]["audio_health"] == health
    assert results[0]["audio_warning_code"] == "RECORDING_OUTPUT_AUDIO_SILENT"
    assert "RECORDING_OUTPUT_AUDIO_SILENT" in results[0]["warnings"]
    assert inspect.call_args.kwargs["timeout_sec"] == 10.0


def test_audible_recording_keeps_success_without_warning() -> None:
    results = [{"success": True, "output_path": "C:\\Videos\\audible.mp4", "warnings": []}]
    health = {
        "status": "audible",
        "audible": True,
        "stream_index": 3,
        "audio_stream_count": 4,
    }

    with (
        patch("app.video_composer.resolve_ffmpeg_binary", return_value=Path("ffmpeg.exe")),
        patch("app.video_composer.inspect_media_audio", return_value=health),
    ):
        asyncio.run(_annotate_v3_audio_health(results, None))

    assert results[0]["audio_health"] == health
    assert "audio_warning_code" not in results[0]
    assert results[0]["warnings"] == []


def test_probe_unavailable_is_not_silently_treated_as_success() -> None:
    results = [{"success": True, "output_path": "C:\\Videos\\unknown.mp4", "warnings": []}]

    with patch(
        "app.video_composer.resolve_ffmpeg_binary",
        side_effect=RuntimeError("ffmpeg unavailable"),
    ):
        asyncio.run(_annotate_v3_audio_health(results, None))

    assert results[0]["audio_health"] == {"status": "unavailable", "audible": None}
    assert results[0]["audio_warning_code"] == "RECORDING_OUTPUT_AUDIO_UNVERIFIED"
    assert "RECORDING_OUTPUT_AUDIO_UNVERIFIED" in results[0]["warnings"]


def test_server_audio_gate_rejects_not_ready_obs() -> None:
    with patch(
        "app.obs_config_center.get_status_payload",
        return_value={"audio": {"ready": False}},
    ):
        try:
            asyncio.run(_require_obs_audio_ready(OBSConfig()))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail["code"] == "RECORDING_OBS_AUDIO_NOT_READY"
        else:
            raise AssertionError("audio gate did not reject an unready OBS path")


def test_server_audio_gate_accepts_ready_obs() -> None:
    with patch(
        "app.obs_config_center.get_status_payload",
        return_value={"audio": {"ready": True}},
    ):
        asyncio.run(_require_obs_audio_ready(OBSConfig()))
