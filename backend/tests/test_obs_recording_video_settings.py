from types import SimpleNamespace

import pytest

from app.recording.executor.obs_client import OBSClient, OBSRecordError


class _FakeWs:
    def __init__(self, *, accept_video: bool = True):
        self.accept_video = accept_video
        self.video = {
            "baseWidth": 1280,
            "baseHeight": 960,
            "outputWidth": 1280,
            "outputHeight": 960,
            "fpsNumerator": 480,
            "fpsDenominator": 1,
        }
        self.calls = []

    def call(self, request):
        name = request.name
        data = request.data()
        self.calls.append((name, data))
        if name == "GetVideoSettings":
            return SimpleNamespace(datain=dict(self.video))
        if name == "SetVideoSettings":
            if self.accept_video:
                self.video.update(data)
            return SimpleNamespace(datain={})
        if name == "GetSceneItemId":
            return SimpleNamespace(datain={"sceneItemId": 7})
        if name == "SetSceneItemTransform":
            return SimpleNamespace(datain={})
        raise AssertionError(f"unexpected OBS request: {name}")


def _client(ws: _FakeWs) -> OBSClient:
    client = object.__new__(OBSClient)
    client._ws = ws
    return client


def test_applies_custom_resolution_and_fps_and_refits_capture():
    ws = _FakeWs()
    result = _client(ws).apply_recording_video_settings(width=1920, height=1440, fps=480)

    assert result == {
        "base_width": 1920,
        "base_height": 1440,
        "output_width": 1920,
        "output_height": 1440,
        "fps_num": 480,
        "fps_den": 1,
    }
    video_call = next(data for name, data in ws.calls if name == "SetVideoSettings")
    assert video_call == {
        "fpsNumerator": 480,
        "fpsDenominator": 1,
        "baseWidth": 1920,
        "baseHeight": 1440,
        "outputWidth": 1920,
        "outputHeight": 1440,
    }
    transform = next(data for name, data in ws.calls if name == "SetSceneItemTransform")
    assert transform["sceneItemTransform"]["boundsWidth"] == 1920
    assert transform["sceneItemTransform"]["boundsHeight"] == 1440


def test_can_change_only_fps_while_preserving_current_resolution():
    ws = _FakeWs()
    _client(ws).apply_recording_video_settings(fps=240)

    video_call = next(data for name, data in ws.calls if name == "SetVideoSettings")
    assert video_call["baseWidth"] == 1280
    assert video_call["baseHeight"] == 960
    assert video_call["fpsNumerator"] == 240


def test_rejected_video_settings_are_rolled_back():
    ws = _FakeWs(accept_video=False)

    with pytest.raises(OBSRecordError, match="rolled back"):
        _client(ws).apply_recording_video_settings(width=1920, height=1440, fps=480)

    video_calls = [data for name, data in ws.calls if name == "SetVideoSettings"]
    assert len(video_calls) == 2
    assert video_calls[-1]["baseWidth"] == 1280
    assert video_calls[-1]["baseHeight"] == 960
