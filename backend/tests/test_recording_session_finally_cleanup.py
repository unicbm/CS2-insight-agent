import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.env_utils import OBSConfig
from app.obs_director import OBSDirector


def test_plan_queue_finally_kills_cs2_after_unexpected_post_launch_error():
    """A generic failure after launch must not leave the modified CS2 session alive."""
    request = SimpleNamespace(
        request_id="req-cleanup",
        demo=SimpleNamespace(demo_path=r"C:\demos\cleanup.dem", demo_filename="cleanup.dem"),
        options=SimpleNamespace(kb_overlay_enabled=False),
    )
    director = OBSDirector(OBSConfig(), r"C:\fake\cs2.exe")
    director._launch_cs2 = MagicMock()
    director._await_gsi_startup_gate = AsyncMock(side_effect=RuntimeError("post-launch failure"))
    director._kill_cs2 = MagicMock()
    director._cleanup_cs2_artifacts = MagicMock()

    cleanup_labels: list[str] = []

    async def run_cleanup(label, func, timeout=20.0):
        cleanup_labels.append(label)
        func()

    director._run_cleanup_step = run_cleanup

    obs_client = MagicMock()
    obs_client.config = OBSConfig()
    final_controller = MagicMock()
    final_controller.force_stop_recording = AsyncMock()

    with (
        patch("app.recording.executor.obs_client.OBSClient", return_value=obs_client),
        patch("app.recording.plan_builder.build_plan", return_value=MagicMock()),
        patch(
            "app.recording.executor.obs_recording_controller.OBSRecordingController",
            return_value=final_controller,
        ),
        pytest.raises(RuntimeError, match="post-launch failure"),
    ):
        asyncio.run(director.execute_plan_queue([request]))

    director._kill_cs2.assert_called_once_with()
    director._cleanup_cs2_artifacts.assert_called_once_with()
    assert "CS2 shutdown in final plan-queue cleanup" in cleanup_labels
    assert "CS2 artifact cleanup in final plan-queue cleanup" in cleanup_labels
