from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import obs_config_center as occ


class _Response:
    def __init__(self, **data):
        self.datain = data


class _FakeWs:
    def __init__(
        self,
        parameters: dict[tuple[str, str], str],
        *,
        reject_encoder=False,
        reject_video=False,
        video_settings: dict[str, int] | None = None,
    ):
        self.parameters = dict(parameters)
        self.reject_encoder = reject_encoder
        self.reject_video = reject_video
        self.calls: list[tuple[str, dict]] = []
        self.disconnected = False
        self.video_settings = video_settings or {
            "baseWidth": 1920,
            "baseHeight": 1080,
            "outputWidth": 1920,
            "outputHeight": 1080,
            "fpsNumerator": 60,
            "fpsDenominator": 1,
        }

    def call(self, request):
        name = request.name
        data = dict(request.dataout)
        self.calls.append((name, data))
        if name == "GetVersion":
            return _Response(obsVersion="32.1.1")
        if name == "GetVideoSettings":
            return _Response(videoSettings=dict(self.video_settings))
        if name == "SetVideoSettings":
            if not self.reject_video:
                self.video_settings.update(data)
            return _Response()
        if name == "GetRecordStatus":
            return _Response(outputActive=False)
        if name == "GetSceneList":
            return _Response(scenes=[{"sceneName": "CS2 Insight Recording"}])
        if name == "GetSceneItemList":
            return _Response(
                sceneItems=[
                    {
                        "sourceName": "CS2 Insight Game Capture",
                        "sceneItemId": 7,
                    }
                ]
            )
        if name == "GetSceneItemTransform":
            return _Response(
                sceneItemTransform={
                    "boundsType": "OBS_BOUNDS_STRETCH",
                    "boundsWidth": 1920,
                    "boundsHeight": 1080,
                }
            )
        if name == "GetSceneItemId":
            return _Response(sceneItemId=7)
        if name == "GetRecordDirectory":
            return _Response(recordDirectory="C:/ws/videos")
        if name == "GetProfileParameter":
            key = (data["parameterCategory"], data["parameterName"])
            return _Response(parameterValue=self.parameters.get(key, ""))
        if name == "SetProfileParameter":
            key = (data["parameterCategory"], data["parameterName"])
            if not (self.reject_encoder and key == ("AdvOut", "RecEncoder")):
                self.parameters[key] = data["parameterValue"]
            return _Response()
        return _Response()

    def disconnect(self):
        self.disconnected = True


def _write_advanced_profile(obs_root: Path) -> None:
    obs_root.mkdir(parents=True)
    (obs_root / "global.ini").write_text(
        "[Basic]\n"
        "Profile=显示名称\n"
        "ProfileDir=profile-dir\n"
        "SceneCollection=场景显示名称\n"
        "SceneCollectionFile=scene-file\n",
        encoding="utf-8-sig",
    )
    profile = obs_root / "basic" / "profiles" / "profile-dir"
    profile.mkdir(parents=True)
    (profile / "basic.ini").write_text(
        "[Output]\n"
        "Mode=Advanced\n\n"
        "[SimpleOutput]\n"
        "FilePath=C:/wrong/simple\n"
        "RecFormat2=mkv\n"
        "RecEncoder=nvenc\n\n"
        "[AdvOut]\n"
        "Encoder=obs_nvenc_h264_tex\n"
        "RecEncoder=h265_texture_amf\n"
        "RecFormat2=hybrid_mp4\n"
        "RecFilePath=C:/actual/advanced\n",
        encoding="utf-8-sig",
    )


def _advanced_parameters() -> dict[tuple[str, str], str]:
    return {
        ("Output", "Mode"): "Advanced",
        ("AdvOut", "Encoder"): "obs_nvenc_h264_tex",
        ("AdvOut", "RecEncoder"): "h265_texture_amf",
        ("AdvOut", "RecFormat2"): "hybrid_mp4",
    }


def _patch_runtime(monkeypatch, tmp_path: Path, ws: _FakeWs) -> Path:
    obs_root = tmp_path / "obs-studio"
    _write_advanced_profile(obs_root)
    monkeypatch.setattr(occ, "_obs_studio_root", lambda: obs_root)
    monkeypatch.setattr(occ, "_ws_connect", lambda _cfg: ws)
    monkeypatch.setattr(occ, "_latest_backup_summary", lambda: None)
    monkeypatch.setattr(
        "app.env_utils.get_primary_monitor_resolution",
        lambda: (1920, 1080),
    )
    return obs_root


def test_modern_global_ini_and_advanced_profile_are_parsed_without_simple_fallback(tmp_path: Path):
    obs_root = tmp_path / "obs-studio"
    _write_advanced_profile(obs_root)

    assert occ._read_global_profile_names(obs_root) == ("profile-dir", "scene-file")
    mode, simple, advanced = occ._parse_output_profile(
        obs_root / "basic" / "profiles" / "profile-dir" / "basic.ini"
    )
    assert mode == "advanced"
    assert simple["RecEncoder"] == "nvenc"
    assert advanced["RecEncoder"] == "h265_texture_amf"
    assert advanced["RecFilePath"] == "C:/actual/advanced"


def test_status_reports_active_advanced_encoder_format_and_path(monkeypatch, tmp_path: Path):
    ws = _FakeWs(_advanced_parameters())
    _patch_runtime(monkeypatch, tmp_path, ws)

    status = occ.get_status_payload(SimpleNamespace())

    assert status["active_profile"] == "profile-dir"
    assert status["recording"] == {
        "output_mode": "advanced",
        "use_stream_encoder": False,
        "encoder": "h265_texture_amf",
        "format": "hybrid_mp4",
        "output_path": "C:/actual/advanced",
        "rec_quality": "Advanced",
    }
    assert ws.disconnected is True


def test_diagnose_flags_advanced_amf_when_nvenc_is_configured(monkeypatch, tmp_path: Path):
    ws = _FakeWs(_advanced_parameters())
    _patch_runtime(monkeypatch, tmp_path, ws)

    result = occ.diagnose(SimpleNamespace())

    assert result["disk_profile_checked"] is True
    assert result["recording"]["output_mode"] == "advanced"
    assert result["recording"]["encoder"] == "h265_texture_amf"
    assert result["recording"]["format"] == "hybrid_mp4"
    assert result["recording"]["output_path"] == "C:/actual/advanced"
    assert {issue["code"] for issue in result["issues"]} == {"ADVANCED_AMF_ON_NVENC"}
    assert result["level"] == "error"


def test_advanced_calibration_switches_amf_to_verified_nvenc_hevc():
    ws = _FakeWs(_advanced_parameters())

    changed, already_ok, restart = occ._calibrate_advanced_output(ws, {})

    assert ws.parameters[("AdvOut", "RecEncoder")] == "obs_nvenc_hevc_tex"
    assert ws.parameters[("AdvOut", "RecFormat2")] == "hybrid_mp4"
    assert any("h265_texture_amf" in item and "obs_nvenc_hevc_tex" in item for item in changed)
    assert "高级输出录像格式正确（混合 MP4）" in already_ok
    assert restart is True
    assert not any(
        call[1].get("parameterCategory") == "SimpleOutput"
        for call in ws.calls
        if call[0] in {"GetProfileParameter", "SetProfileParameter"}
    )


def test_advanced_calibration_rolls_back_when_encoder_readback_fails():
    ws = _FakeWs(_advanced_parameters(), reject_encoder=True)

    with pytest.raises(ValueError, match="未接受 AdvOut/RecEncoder"):
        occ._calibrate_advanced_output(ws, {})

    assert ws.parameters[("AdvOut", "RecEncoder")] == "h265_texture_amf"
    encoder_sets = [
        data["parameterValue"]
        for name, data in ws.calls
        if name == "SetProfileParameter"
        and data.get("parameterCategory") == "AdvOut"
        and data.get("parameterName") == "RecEncoder"
    ]
    assert encoder_sets == ["obs_nvenc_hevc_tex", "h265_texture_amf"]


def test_simple_calibration_keeps_existing_simple_output_path():
    ws = _FakeWs(
        {
            ("SimpleOutput", "RecQuality"): "Stream",
            ("SimpleOutput", "RecEncoder"): "none",
            ("SimpleOutput", "StreamEncoder"): "obs_nvenc_h264_tex",
            ("SimpleOutput", "RecFormat2"): "mkv",
        }
    )

    changed, _, restart = occ._calibrate_simple_output(ws)

    assert ws.parameters[("SimpleOutput", "RecQuality")] == "Small"
    assert ws.parameters[("SimpleOutput", "RecEncoder")] == "obs_nvenc_h264_tex"
    assert ws.parameters[("SimpleOutput", "RecFormat2")] == "hybrid_mp4"
    assert len(changed) == 3
    assert restart is True


def test_pro_video_preset_calibration_sets_exact_4x3_480_target(monkeypatch, tmp_path: Path):
    ws = _FakeWs(_advanced_parameters())
    _patch_runtime(monkeypatch, tmp_path, ws)

    result = occ.calibrate(
        SimpleNamespace(recording_video_preset="pro_4x3_480")
    )

    video_sets = [data for name, data in ws.calls if name == "SetVideoSettings"]
    assert video_sets == [
        {
            "fpsNumerator": 480,
            "fpsDenominator": 1,
            "baseWidth": 1280,
            "baseHeight": 960,
            "outputWidth": 1280,
            "outputHeight": 960,
        }
    ]
    transforms = [data for name, data in ws.calls if name == "SetSceneItemTransform"]
    assert transforms[0]["sceneItemTransform"]["boundsWidth"] == 1280
    assert transforms[0]["sceneItemTransform"]["boundsHeight"] == 960
    assert result["recording_video_preset"] == "pro_4x3_480"
    assert result["video_target"]["fps"] == 480
    assert any("480 FPS" in item for item in result["changed"])


def test_display_video_preset_raises_low_fps_to_sixty_without_lowering_resolution(
    monkeypatch,
    tmp_path: Path,
):
    ws = _FakeWs(
        _advanced_parameters(),
        video_settings={
            "baseWidth": 1920,
            "baseHeight": 1080,
            "outputWidth": 1920,
            "outputHeight": 1080,
            "fpsNumerator": 30,
            "fpsDenominator": 1,
        },
    )
    _patch_runtime(monkeypatch, tmp_path, ws)

    occ.calibrate(SimpleNamespace(recording_video_preset="display"))

    video_set = next(data for name, data in ws.calls if name == "SetVideoSettings")
    assert video_set == {
        "fpsNumerator": 60,
        "fpsDenominator": 1,
        "baseWidth": 1920,
        "baseHeight": 1080,
        "outputWidth": 1920,
        "outputHeight": 1080,
    }


def test_pro_video_preset_diagnosis_uses_preset_instead_of_monitor(monkeypatch, tmp_path: Path):
    ws = _FakeWs(_advanced_parameters())
    _patch_runtime(monkeypatch, tmp_path, ws)

    result = occ.diagnose(
        SimpleNamespace(recording_video_preset="pro_4x3_480")
    )

    codes = {issue["code"] for issue in result["issues"]}
    assert "CANVAS_RESOLUTION_MISMATCH" in codes
    assert "OUTPUT_RESOLUTION_MISMATCH" in codes
    assert "FPS_PRESET_MISMATCH" in codes
    assert "HIGH_FPS_NVENC_REQUIRED" in codes
    assert result["video_target"] == {
        "preset": "pro_4x3_480",
        "width": 1280,
        "height": 960,
        "fps_num": 480,
        "fps_den": 1,
        "fps": 480,
    }


def test_pro_preset_rejects_non_nvenc_before_changing_video(monkeypatch, tmp_path: Path):
    parameters = _advanced_parameters()
    parameters[("AdvOut", "RecEncoder")] = "obs_x264"
    parameters[("AdvOut", "Encoder")] = "obs_x264"
    ws = _FakeWs(parameters)
    _patch_runtime(monkeypatch, tmp_path, ws)

    with pytest.raises(ValueError, match="需要 NVIDIA NVENC"):
        occ.calibrate(SimpleNamespace(recording_video_preset="pro_4x3_480"))

    assert not any(name == "SetVideoSettings" for name, _ in ws.calls)


def test_pro_preset_encoder_readback_failure_does_not_change_video(monkeypatch, tmp_path: Path):
    ws = _FakeWs(_advanced_parameters(), reject_encoder=True)
    _patch_runtime(monkeypatch, tmp_path, ws)

    with pytest.raises(ValueError, match="未接受 AdvOut/RecEncoder"):
        occ.calibrate(SimpleNamespace(recording_video_preset="pro_4x3_480"))

    assert not any(name == "SetVideoSettings" for name, _ in ws.calls)


def test_failed_video_change_rolls_back_output_and_keeps_original_video(monkeypatch, tmp_path: Path):
    parameters = _advanced_parameters()
    parameters[("AdvOut", "RecEncoder")] = "obs_nvenc_hevc_tex"
    ws = _FakeWs(parameters, reject_video=True)
    _patch_runtime(monkeypatch, tmp_path, ws)
    original_video = dict(ws.video_settings)
    original_parameters = dict(ws.parameters)

    with pytest.raises(ValueError, match="已恢复本次校准修改"):
        occ.calibrate(SimpleNamespace(recording_video_preset="pro_4x3_480"))

    assert ws.video_settings == original_video
    assert ws.parameters == original_parameters


def test_pro_preset_switches_x264_when_stream_nvenc_proves_availability():
    parameters = _advanced_parameters()
    parameters[("AdvOut", "RecEncoder")] = "obs_x264"
    ws = _FakeWs(parameters)

    changed, _, restart = occ._calibrate_advanced_output(
        ws,
        {},
        require_nvenc=True,
    )

    assert ws.parameters[("AdvOut", "RecEncoder")] == "obs_nvenc_hevc_tex"
    assert any("obs_x264" in item and "obs_nvenc_hevc_tex" in item for item in changed)
    assert restart is True


def test_pro_preset_uses_current_obs_log_as_nvenc_capability_proof(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "current.txt").write_text(
        "[obs-nvenc] NVENC version: 12.2 (compiled) / 13.0 (driver)\n",
        encoding="utf-8",
    )
    assert occ._obs_runtime_log_confirms_nvenc(tmp_path) is True

    parameters = _advanced_parameters()
    parameters[("AdvOut", "RecEncoder")] = "obs_x264"
    parameters[("AdvOut", "Encoder")] = "obs_x264"
    ws = _FakeWs(parameters)
    changed, _, restart = occ._calibrate_advanced_output(
        ws,
        {},
        require_nvenc=True,
        nvenc_available=True,
    )

    assert ws.parameters[("AdvOut", "RecEncoder")] == "obs_nvenc_hevc_tex"
    assert any("obs_x264" in item for item in changed)
    assert restart is True


def test_standard_advanced_output_replaces_use_stream_with_independent_encoder():
    parameters = _advanced_parameters()
    parameters[("AdvOut", "RecEncoder")] = "none"
    parameters[("AdvOut", "Encoder")] = "obs_x264"
    ws = _FakeWs(parameters)

    changed, _, restart = occ._calibrate_advanced_output(ws, {})

    assert ws.parameters[("AdvOut", "RecEncoder")] == "obs_x264"
    assert any("与串流一致" in item and "obs_x264" in item for item in changed)
    assert restart is True


def test_pro_preset_simple_output_requires_and_applies_nvenc():
    ws = _FakeWs(
        {
            ("SimpleOutput", "RecQuality"): "Small",
            ("SimpleOutput", "RecEncoder"): "x264",
            ("SimpleOutput", "StreamEncoder"): "nvenc",
            ("SimpleOutput", "RecFormat2"): "hybrid_mp4",
        }
    )

    changed, _, restart = occ._calibrate_simple_output(ws, require_nvenc=True)

    assert ws.parameters[("SimpleOutput", "RecEncoder")] == "nvenc"
    assert any("x264" in item and "nvenc" in item for item in changed)
    assert restart is True
