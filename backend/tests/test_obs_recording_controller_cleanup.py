"""Regression tests for OBS recording hot-path connection cleanup."""

import asyncio
import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.env_utils import OBSConfig
from app.recording.executor.obs_recording_controller import OBSRecordingController
from app.recording.executor.recording_executor import RecordingExecutor


def _run(coro):
    return asyncio.run(coro)


def _controller() -> OBSRecordingController:
    cfg = OBSConfig(host="localhost", port=4455, password="")
    return OBSRecordingController(cfg, MagicMock())


@pytest.mark.parametrize(
    ("method_name", "status", "expected"),
    [
        ("start_record_safe", {"outputActive": True, "outputPaused": False}, "ok"),
        ("resume_record_safe", {"outputActive": True, "outputPaused": False}, "ok"),
        ("pause_record_safe", {"outputActive": True, "outputPaused": True}, "ok"),
    ],
)
def test_state_hit_does_not_wait_for_blocking_disconnect(method_name, status, expected):
    """A slow obswebsocket recv-thread join must not delay Start/Resume/Pause."""

    disconnect_started = threading.Event()
    release_disconnect = threading.Event()
    poll_client = MagicMock()
    poll_client.get_record_status.return_value = status

    def blocking_disconnect():
        disconnect_started.set()
        release_disconnect.wait(timeout=2.0)

    poll_client.disconnect.side_effect = blocking_disconnect

    async def scenario():
        ctrl = _controller()
        try:
            with (
                patch.object(ctrl, "_new_client", return_value=poll_client),
                patch.object(ctrl, "_fire_bg", return_value=MagicMock()),
            ):
                started = time.perf_counter()
                result = await getattr(ctrl, method_name)()
                elapsed = time.perf_counter() - started

            assert result == expected
            assert elapsed < 0.5, f"{method_name} waited {elapsed:.3f}s for disconnect"
            assert await asyncio.to_thread(disconnect_started.wait, 0.5)
        finally:
            release_disconnect.set()
            await ctrl._wait_for_background_tasks()

        assert not ctrl._background_tasks

    _run(scenario())


def test_stop_record_returns_output_path_without_waiting_for_disconnect():
    """StopRecord keeps its response path while its client closes asynchronously."""

    disconnect_started = threading.Event()
    release_disconnect = threading.Event()
    command_client = MagicMock()
    command_client.stop_record.return_value = r"C:\Videos\clip.mp4"

    def blocking_disconnect():
        disconnect_started.set()
        release_disconnect.wait(timeout=2.0)

    command_client.disconnect.side_effect = blocking_disconnect

    async def scenario():
        ctrl = _controller()
        try:
            with (
                patch.object(ctrl, "_new_client", return_value=command_client),
                patch.object(
                    ctrl,
                    "_observe_state",
                    new=AsyncMock(return_value={"outputActive": False, "outputPaused": False}),
                ),
            ):
                started = time.perf_counter()
                output_path = await ctrl.stop_record_safe()
                elapsed = time.perf_counter() - started

            assert output_path == r"C:\Videos\clip.mp4"
            assert elapsed < 0.5, f"StopRecord waited {elapsed:.3f}s for disconnect"
            assert await asyncio.to_thread(disconnect_started.wait, 0.5)
        finally:
            release_disconnect.set()
            await ctrl._wait_for_background_tasks()

        assert not ctrl._background_tasks

    _run(scenario())


def test_queue_executor_does_not_disconnect_demo_group_client():
    obs_client = MagicMock()
    executor = RecordingExecutor(obs_client, disconnect_obs_on_finish=False)

    _run(executor._disconnect_obs_if_owned())

    obs_client.disconnect.assert_not_called()


def test_standalone_executor_keeps_historical_disconnect_ownership():
    obs_client = MagicMock()
    executor = RecordingExecutor(obs_client)

    _run(executor._disconnect_obs_if_owned())

    obs_client.disconnect.assert_called_once_with()
