from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.env_utils import OBSConfig
from app.recording.executor.obs_client import OBSClient, OBSRecordError


def _connected_client() -> tuple[OBSClient, MagicMock]:
    client = OBSClient(OBSConfig())
    ws = MagicMock()
    client._ws = ws
    return client, ws


def test_ensure_input_audio_track_preserves_other_tracks_and_verifies():
    client, ws = _connected_client()
    ws.call.side_effect = [
        SimpleNamespace(datain={"inputAudioTracks": {"1": False, "2": True, "3": False}}),
        SimpleNamespace(datain={}),
        SimpleNamespace(datain={"inputAudioTracks": {"1": True, "2": True, "3": False}}),
    ]

    assert client.ensure_input_audio_track("CS2 Insight Game Capture", 1) is True

    set_request = ws.call.call_args_list[1].args[0]
    assert set_request.name == "SetInputAudioTracks"
    assert set_request.dataout["inputName"] == "CS2 Insight Game Capture"
    assert set_request.dataout["inputAudioTracks"] == {
        "1": True,
        "2": True,
        "3": False,
    }


def test_ensure_input_audio_track_is_noop_when_already_enabled():
    client, ws = _connected_client()
    ws.call.return_value = SimpleNamespace(datain={"inputAudioTracks": {"1": True}})

    assert client.ensure_input_audio_track("CS2 Insight Game Capture", 1) is False
    assert ws.call.call_count == 1


def test_ensure_input_audio_track_rejects_silent_set_failure():
    client, ws = _connected_client()
    ws.call.side_effect = [
        SimpleNamespace(datain={"inputAudioTracks": {"1": False, "2": True}}),
        SimpleNamespace(datain={}),
        SimpleNamespace(datain={"inputAudioTracks": {"1": False, "2": True}}),
    ]

    with pytest.raises(OBSRecordError, match="did not enable audio track 1"):
        client.ensure_input_audio_track("CS2 Insight Game Capture", 1)
