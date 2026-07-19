import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import obs_config_center
from app.env_utils import OBSConfig


def _write_websocket_config(root: Path, **values) -> None:
    path = root / "plugin_config" / "obs-websocket" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(values), encoding="utf-8")


def test_connection_readiness_reports_disabled_server_without_exposing_password(tmp_path: Path):
    _write_websocket_config(
        tmp_path,
        server_enabled=False,
        server_port=4455,
        auth_required=True,
        server_password="do-not-expose",
    )

    readiness = obs_config_center.get_connection_readiness(
        OBSConfig(host="localhost", port=4455, password=""),
        obs_root=tmp_path,
        probe_port=False,
    )

    assert readiness["settings_found"] is True
    assert readiness["server_enabled"] is False
    assert readiness["auth_required"] is True
    assert readiness["obs_password_present"] is True
    assert readiness["app_password_present"] is False
    assert "OBS_WEBSOCKET_DISABLED" in readiness["blockers"]
    assert "OBS_PASSWORD_REQUIRED" in readiness["blockers"]
    assert "do-not-expose" not in json.dumps(readiness)


def test_diagnose_keeps_disk_checks_when_websocket_connection_fails(tmp_path: Path, monkeypatch):
    _write_websocket_config(
        tmp_path,
        server_enabled=False,
        server_port=4455,
        auth_required=True,
        server_password="configured-in-obs",
    )
    (tmp_path / "global.ini").write_text("[Basic]\nCurrentProfile=Test\n", encoding="utf-8")
    profile_ini = tmp_path / "basic" / "profiles" / "Test" / "basic.ini"
    profile_ini.parent.mkdir(parents=True)
    profile_ini.write_text(
        "[SimpleOutput]\nRecEncoder=\nRecFormat2=hybrid_mp4\nFilePath=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(obs_config_center, "_obs_studio_root", lambda: tmp_path)
    monkeypatch.setattr(
        obs_config_center,
        "_ws_connect",
        lambda _cfg: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    result = obs_config_center.diagnose(OBSConfig(host="localhost", port=4455, password=""))
    codes = {item["code"] for item in result["issues"]}

    assert result["obs_connected"] is False
    assert result["disk_profile_checked"] is True
    assert "OBS_WEBSOCKET_DISABLED" in codes
    assert "OBS_PASSWORD_REQUIRED" in codes
    assert "NO_OUTPUT_PATH" in codes
    assert "ENCODER_UNAVAILABLE" in codes
    assert "OBS_NOT_CONNECTED" in codes
    assert "offline" not in json.dumps(result)


def test_profile_recording_settings_uses_advanced_section(tmp_path: Path):
    profile_ini = tmp_path / "basic.ini"
    profile_ini.write_text(
        """[Output]
Mode=Advanced
[SimpleOutput]
RecEncoder=obs_x264
RecFormat2=mkv
RecQuality=Stream
FilePath=C:\\wrong
RecTracks=1
[AdvOut]
RecEncoder=h265_texture_amf
RecFormat2=mp4
RecFilePath=C:\\right
RecTracks=15
""",
        encoding="utf-8",
    )

    result = obs_config_center._profile_recording_settings(profile_ini)

    assert result["output_mode"] == "Advanced"
    assert result["encoder"] == "h265_texture_amf"
    assert result["format"] == "mp4"
    assert result["rec_quality"] == "Advanced"
    assert result["output_path"] == "C:\\right"
    assert result["audio_tracks"] == [1, 2, 3, 4]
    assert result["output_track1_enabled"] is True
    assert result["use_stream_encoder"] is False


def test_modern_obs_global_ini_prefers_profile_and_scene_directory_keys(tmp_path: Path):
    (tmp_path / "global.ini").write_text(
        """[Basic]
Profile=显示名称
ProfileDir=未命名
SceneCollection=场景显示名称
SceneCollectionFile=未命名
""",
        encoding="utf-8",
    )

    assert obs_config_center._read_global_profile_names(tmp_path) == ("未命名", "未命名")


class _AudioHealthWs:
    def __init__(self):
        self.capture_audio = False
        self.muted = True
        self.tracks = {"1": False, "2": True, "3": False}
        self.set_track_payload = None
        self.special_inputs = {}
        self.other_input_tracks = {}
        self.other_input_muted = {}
        self.other_input_active = {}
        self.other_input_settings = {}
        self.scene_inputs = []

    def call(self, request):
        name = request.name
        if name == "GetInputSettings":
            input_name = request.dataout["inputName"]
            if input_name in self.other_input_settings:
                return SimpleNamespace(
                    datain={"inputSettings": dict(self.other_input_settings[input_name])}
                )
            return SimpleNamespace(datain={"inputSettings": {"capture_audio": self.capture_audio}})
        if name == "SetInputSettings":
            self.capture_audio = bool(request.dataout["inputSettings"].get("capture_audio"))
            return SimpleNamespace(datain={})
        if name == "GetInputMute":
            input_name = request.dataout["inputName"]
            if input_name in self.other_input_muted:
                return SimpleNamespace(
                    datain={"inputMuted": self.other_input_muted[input_name]}
                )
            return SimpleNamespace(datain={"inputMuted": self.muted})
        if name == "SetInputMute":
            self.muted = bool(request.dataout["inputMuted"])
            return SimpleNamespace(datain={})
        if name == "GetInputAudioTracks":
            input_name = request.dataout["inputName"]
            if input_name in self.other_input_tracks:
                return SimpleNamespace(
                    datain={
                        "inputAudioTracks": dict(self.other_input_tracks[input_name])
                    }
                )
            return SimpleNamespace(datain={"inputAudioTracks": dict(self.tracks)})
        if name == "SetInputAudioTracks":
            self.set_track_payload = dict(request.dataout["inputAudioTracks"])
            self.tracks = dict(self.set_track_payload)
            return SimpleNamespace(datain={})
        if name == "GetSpecialInputs":
            return SimpleNamespace(datain=dict(self.special_inputs))
        if name == "GetSceneItemList":
            return SimpleNamespace(
                datain={"sceneItems": [{"sourceName": item} for item in self.scene_inputs]}
            )
        if name == "GetInputActiveState":
            input_name = request.dataout["inputName"]
            return SimpleNamespace(
                datain={"videoActive": self.other_input_active.get(input_name, True)}
            )
        raise AssertionError(f"unexpected request: {name}")


def test_ensure_exclusive_input_audio_track_disables_every_extra_route():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False

    assert obs_config_center._ensure_exclusive_input_audio_track(
        ws,
        "CS2 Insight Game Capture",
        1,
    ) is True
    assert ws.set_track_payload == {
        "1": True,
        "2": False,
        "3": False,
        "4": False,
        "5": False,
        "6": False,
    }
    health = obs_config_center._dedicated_audio_health(
        ws,
        "CS2 Insight Game Capture",
    )
    assert health["track1_enabled"] is True
    assert health["enabled_tracks"] == [1]
    assert health["exclusive_track1"] is True
    assert health["duplicate_track_risk"] is False
    assert health["ready"] is True


def test_ensure_exclusive_input_audio_track_is_idempotent():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False
    ws.tracks = {str(track): track == 1 for track in range(1, 7)}

    assert obs_config_center._ensure_exclusive_input_audio_track(
        ws,
        "CS2 Insight Game Capture",
        1,
    ) is False
    assert ws.set_track_payload is None


def _configure_desktop_audio(ws: _AudioHealthWs, *, track1: bool) -> None:
    ws.special_inputs = {"desktop1": "Desktop Audio", "desktop2": None}
    ws.other_input_tracks["Desktop Audio"] = {
        "1": track1,
        "2": False,
        "3": not track1,
        "4": False,
        "5": False,
        "6": False,
    }
    ws.other_input_muted["Desktop Audio"] = False
    ws.other_input_active["Desktop Audio"] = True


def test_desktop_audio_on_track1_blocks_clean_audio_readiness():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False
    ws.tracks = {str(track): track == 1 for track in range(1, 7)}
    _configure_desktop_audio(ws, track1=True)

    health = obs_config_center._dedicated_audio_health(
        ws,
        "CS2 Insight Game Capture",
    )

    assert health["track1_conflict_scan_available"] is True
    assert health["track1_conflict_names"] == ["Desktop Audio"]
    assert health["track1_isolated"] is False
    assert health["duplicate_track_risk"] is True
    assert health["ready"] is False


def test_desktop_audio_track1_does_not_trust_video_active_false():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False
    ws.tracks = {str(track): track == 1 for track in range(1, 7)}
    _configure_desktop_audio(ws, track1=True)
    ws.other_input_active["Desktop Audio"] = False

    health = obs_config_center._dedicated_audio_health(
        ws,
        "CS2 Insight Game Capture",
    )

    assert health["track1_conflict_names"] == ["Desktop Audio"]
    assert health["duplicate_track_risk"] is True
    assert health["ready"] is False


def test_desktop_audio_on_track3_keeps_track1_ready():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False
    ws.tracks = {str(track): track == 1 for track in range(1, 7)}
    _configure_desktop_audio(ws, track1=False)

    health = obs_config_center._dedicated_audio_health(
        ws,
        "CS2 Insight Game Capture",
    )

    assert health["track1_conflict_names"] == []
    assert health["track1_isolated"] is True
    assert health["duplicate_track_risk"] is False
    assert health["ready"] is True


def test_silent_keyboard_overlay_does_not_block_track1_isolation():
    ws = _AudioHealthWs()
    ws.capture_audio = True
    ws.muted = False
    ws.tracks = {str(track): track == 1 for track in range(1, 7)}
    ws.scene_inputs = ["CS2 Insight Game Capture", "CS2 Keyboard Overlay"]
    ws.other_input_settings["CS2 Keyboard Overlay"] = {"reroute_audio": False}
    ws.other_input_tracks["CS2 Keyboard Overlay"] = {
        str(track): track == 1 for track in range(1, 7)
    }
    ws.other_input_muted["CS2 Keyboard Overlay"] = False

    health = obs_config_center._dedicated_audio_health(
        ws,
        "CS2 Insight Game Capture",
        scene_name="CS2 Insight Recording",
    )

    assert health["track1_conflict_names"] == []
    assert health["track1_isolated"] is True
    assert health["ready"] is True


class _DiagnoseWs(_AudioHealthWs):
    def call(self, request):
        name = request.name
        if name == "GetVersion":
            return SimpleNamespace(datain={"obsVersion": "32.1.1"})
        if name == "GetRecordStatus":
            return SimpleNamespace(datain={"outputActive": False})
        if name == "GetVideoSettings":
            return SimpleNamespace(datain={
                "baseWidth": 1920,
                "baseHeight": 1080,
                "outputWidth": 1920,
                "outputHeight": 1080,
                "fpsNumerator": 60,
                "fpsDenominator": 1,
            })
        if name == "GetSceneList":
            return SimpleNamespace(datain={"scenes": [{"sceneName": "CS2 Insight Recording"}]})
        if name == "GetSceneItemList":
            return SimpleNamespace(datain={"sceneItems": [{"sourceName": "CS2 Insight Game Capture"}]})
        return super().call(request)

    def disconnect(self):
        pass


def test_diagnose_reports_dedicated_audio_failures_in_advanced_mode(tmp_path: Path, monkeypatch):
    (tmp_path / "global.ini").write_text("CurrentProfile=Test\n", encoding="utf-8")
    profile_ini = tmp_path / "basic" / "profiles" / "Test" / "basic.ini"
    profile_ini.parent.mkdir(parents=True)
    profile_ini.write_text(
        """[Output]
Mode=Advanced
[AdvOut]
RecEncoder=h265_texture_amf
RecFormat2=hybrid_mp4
RecFilePath=C:\\Videos
RecTracks=15
""",
        encoding="utf-8",
    )
    ws = _DiagnoseWs()
    _configure_desktop_audio(ws, track1=True)
    monkeypatch.setattr(obs_config_center, "_obs_studio_root", lambda: tmp_path)
    monkeypatch.setattr(obs_config_center, "get_connection_readiness", lambda *_args, **_kwargs: {
        "host": "localhost",
        "configured_port": 4455,
        "blockers": [],
        "connected": False,
        "port_open": True,
    })
    monkeypatch.setattr(obs_config_center, "_ws_connect", lambda _cfg: ws)
    monkeypatch.setattr(obs_config_center, "_source_fits_canvas", lambda *_args: True)
    monkeypatch.setattr("app.env_utils.get_primary_monitor_resolution", lambda: (1920, 1080))

    result = obs_config_center.diagnose(OBSConfig())
    codes = {issue["code"] for issue in result["issues"]}

    assert result["recording"]["output_mode"] == "Advanced"
    assert result["recording"]["encoder"] == "h265_texture_amf"
    assert result["audio"]["ready"] is False
    assert {"CAPTURE_AUDIO_DISABLED", "CAPTURE_AUDIO_MUTED", "CAPTURE_AUDIO_TRACK1_DISABLED"} <= codes
    assert "CAPTURE_AUDIO_EXTRA_TRACK_ROUTES" in codes
    assert "AUDIO_TRACK1_CONFLICTING_INPUTS" in codes


class _CalibrateWs(_AudioHealthWs):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.profile_params = {
            ("AdvOut", "RecFormat2"): "hybrid_mp4",
            ("AdvOut", "RecTracks"): "15",
        }

    def call(self, request):
        self.requests.append(request)
        name = request.name
        if name == "GetVideoSettings":
            return SimpleNamespace(datain={
                "baseWidth": 1920,
                "baseHeight": 1080,
                "outputWidth": 1920,
                "outputHeight": 1080,
                "fpsNumerator": 60,
                "fpsDenominator": 1,
            })
        if name == "GetSceneList":
            return SimpleNamespace(datain={"scenes": [{"sceneName": "CS2 Insight Recording"}]})
        if name == "GetSceneItemList":
            return SimpleNamespace(datain={"sceneItems": [{"sourceName": "CS2 Insight Game Capture"}]})
        if name == "GetSceneItemId":
            return SimpleNamespace(datain={"sceneItemId": 1})
        if name == "SetSceneItemTransform":
            return SimpleNamespace(datain={})
        if name == "GetProfileParameter":
            key = (
                request.dataout["parameterCategory"],
                request.dataout["parameterName"],
            )
            return SimpleNamespace(datain={"parameterValue": self.profile_params.get(key, "")})
        if name == "SetProfileParameter":
            key = (
                request.dataout["parameterCategory"],
                request.dataout["parameterName"],
            )
            self.profile_params[key] = str(request.dataout["parameterValue"])
            return SimpleNamespace(datain={})
        return super().call(request)

    def disconnect(self):
        pass


def test_calibrate_repairs_existing_capture_audio_and_uses_advanced_section(tmp_path: Path, monkeypatch):
    (tmp_path / "global.ini").write_text("CurrentProfile=Test\n", encoding="utf-8")
    profile_ini = tmp_path / "basic" / "profiles" / "Test" / "basic.ini"
    profile_ini.parent.mkdir(parents=True)
    profile_ini.write_text(
        """[Output]
Mode=Advanced
[AdvOut]
RecEncoder=h265_texture_amf
RecFormat2=hybrid_mp4
RecFilePath=C:\\Videos
RecTracks=15
""",
        encoding="utf-8",
    )
    ws = _CalibrateWs()
    monkeypatch.setattr(obs_config_center, "_obs_studio_root", lambda: tmp_path)
    monkeypatch.setattr(obs_config_center, "_ws_connect", lambda _cfg: ws)
    monkeypatch.setattr(obs_config_center, "_obs_is_recording", lambda _ws: False)
    monkeypatch.setattr("app.env_utils.get_primary_monitor_resolution", lambda: (1920, 1080))

    result = obs_config_center.calibrate(OBSConfig())

    assert result["success"] is True
    assert ws.capture_audio is True
    assert ws.muted is False
    assert ws.tracks == {
        "1": True,
        "2": False,
        "3": False,
        "4": False,
        "5": False,
        "6": False,
    }
    set_settings = next(req for req in ws.requests if req.name == "SetInputSettings")
    assert set_settings.dataout["inputSettings"] == {
        "capture_audio": True,
        "capture_cursor": False,
    }
    assert any("高级输出模式" in item for item in result["already_ok"])


def test_calibrate_reports_track1_conflict_without_rerouting_desktop_audio(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "global.ini").write_text("CurrentProfile=Test\n", encoding="utf-8")
    profile_ini = tmp_path / "basic" / "profiles" / "Test" / "basic.ini"
    profile_ini.parent.mkdir(parents=True)
    profile_ini.write_text(
        """[Output]
Mode=Advanced
[AdvOut]
RecEncoder=h265_texture_amf
RecFormat2=hybrid_mp4
RecFilePath=C:\\Videos
RecTracks=15
""",
        encoding="utf-8",
    )
    ws = _CalibrateWs()
    _configure_desktop_audio(ws, track1=True)
    desktop_tracks_before = dict(ws.other_input_tracks["Desktop Audio"])
    monkeypatch.setattr(obs_config_center, "_obs_studio_root", lambda: tmp_path)
    monkeypatch.setattr(obs_config_center, "_ws_connect", lambda _cfg: ws)
    monkeypatch.setattr(obs_config_center, "_obs_is_recording", lambda _ws: False)
    monkeypatch.setattr(
        "app.env_utils.get_primary_monitor_resolution",
        lambda: (1920, 1080),
    )

    result = obs_config_center.calibrate(OBSConfig())

    assert result["success"] is False
    assert any("Desktop Audio" in item for item in result["manual_actions"])
    assert ws.other_input_tracks["Desktop Audio"] == desktop_tracks_before


def test_calibrate_preserves_other_output_tracks_and_enables_track1(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "global.ini").write_text(
        "[Basic]\nProfileDir=Test\n",
        encoding="utf-8",
    )
    profile_ini = tmp_path / "basic" / "profiles" / "Test" / "basic.ini"
    profile_ini.parent.mkdir(parents=True)
    profile_ini.write_text(
        """[Output]
Mode=Advanced
[AdvOut]
RecEncoder=h265_texture_amf
RecFormat2=hybrid_mp4
RecFilePath=C:\\Videos
RecTracks=14
""",
        encoding="utf-8",
    )
    ws = _CalibrateWs()
    ws.profile_params[("AdvOut", "RecTracks")] = "14"
    monkeypatch.setattr(obs_config_center, "_obs_studio_root", lambda: tmp_path)
    monkeypatch.setattr(obs_config_center, "_ws_connect", lambda _cfg: ws)
    monkeypatch.setattr(obs_config_center, "_obs_is_recording", lambda _ws: False)
    monkeypatch.setattr(
        "app.env_utils.get_primary_monitor_resolution",
        lambda: (1920, 1080),
    )

    result = obs_config_center.calibrate(OBSConfig())

    assert result["success"] is True
    assert ws.profile_params[("AdvOut", "RecTracks")] == "15"
    assert "已在录像输出中启用音轨 1" in result["changed"]
