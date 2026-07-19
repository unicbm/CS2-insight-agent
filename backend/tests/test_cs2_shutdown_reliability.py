from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.env_utils import OBSConfig
from app import obs_director as director_module
from app.obs_director import OBSDirector


def _director() -> OBSDirector:
    return OBSDirector(OBSConfig(), r"C:\fake\cs2.exe")


def _patch_fast_windows_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(director_module.sys, "platform", "win32")
    monkeypatch.setattr(director_module, "release_cs2_synthetic_keys", lambda: None)
    monkeypatch.setattr(director_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(director_module.subprocess, "run", MagicMock())


def test_kill_does_not_restore_when_process_absence_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fast_windows_shutdown(monkeypatch)
    director = _director()
    director._cs2_process = MagicMock(pid=7331)
    director._restore_user_configs = MagicMock()
    monkeypatch.setattr(
        director_module,
        "_wait_for_cs2_absence",
        lambda _timeout: (False, False, None),
    )
    monkeypatch.setattr(
        director_module,
        "_probe_cs2_absence",
        lambda: (False, False, None),
    )

    with pytest.raises(RuntimeError, match="未能确认 CS2 已完全退出"):
        director._kill_cs2()

    director._restore_user_configs.assert_not_called()
    assert director._cs2_process is not None


def test_kill_restores_only_after_hwnd_and_process_are_both_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fast_windows_shutdown(monkeypatch)
    director = _director()
    process = MagicMock(pid=7331)
    director._cs2_process = process
    director._restore_user_configs = MagicMock()
    monkeypatch.setattr(
        director_module,
        "_wait_for_cs2_absence",
        lambda _timeout: (True, False, False),
    )
    monkeypatch.setattr(
        director_module,
        "_probe_cs2_absence",
        lambda: (True, False, False),
    )

    director._kill_cs2()

    director._restore_user_configs.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=1)
    assert director._cs2_process is None
    taskkill = director_module.subprocess.run
    assert taskkill.call_count == 1
    assert taskkill.call_args.args[0] == ["taskkill", "/F", "/T", "/PID", "7331"]


def test_restore_exception_propagates_after_confirmed_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fast_windows_shutdown(monkeypatch)
    director = _director()
    director._cs2_process = MagicMock(pid=7331)
    director._restore_user_configs = MagicMock(side_effect=RuntimeError("partial restore"))
    monkeypatch.setattr(
        director_module,
        "_wait_for_cs2_absence",
        lambda _timeout: (True, False, False),
    )
    monkeypatch.setattr(
        director_module,
        "_probe_cs2_absence",
        lambda: (True, False, False),
    )

    with pytest.raises(RuntimeError, match="partial restore"):
        director._kill_cs2()


def test_shutdown_cleanup_has_no_outer_timeout_that_can_detach_restore_writes() -> None:
    assert director_module._CS2_SHUTDOWN_CLEANUP_TIMEOUT_SEC is None


def test_snapshot_failure_aborts_before_cs2_launch_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.cfg"
    config.write_bytes(b"healthy")
    director = _director()
    monkeypatch.setattr(director, "_candidate_user_config_dirs", lambda: [tmp_path])
    monkeypatch.setattr(director_module, "write_persistent_backup_from_snap", lambda _snap: None)

    with pytest.raises(RuntimeError, match="备份不可用"):
        director._snapshot_user_configs()

    assert config.read_bytes() == b"healthy"
    assert director._user_config_snapshot == {}


def test_complete_memory_fallback_clears_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.cfg"
    config.write_bytes(b"modified")
    director = _director()
    director._user_config_snapshot = {config: b"original"}
    monkeypatch.setattr(director_module, "is_restore_required", lambda: True)
    monkeypatch.setattr(
        director_module,
        "restore_latest_user_config_backup",
        lambda **_kwargs: {"ok": False, "failed": [{"error": "manifest broken"}]},
    )
    mark_recorded = MagicMock()
    monkeypatch.setattr(director_module, "write_recording_state", mark_recorded)

    director._restore_user_configs()

    assert config.read_bytes() == b"original"
    assert director._user_config_snapshot == {}
    mark_recorded.assert_called_once_with(
        "recorded",
        {"recovered_via": "in_memory_snapshot"},
    )


def test_partial_memory_fallback_does_not_clear_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_a_file = tmp_path / "config.cfg"
    not_a_file.mkdir()
    director = _director()
    director._user_config_snapshot = {not_a_file: b"original"}
    monkeypatch.setattr(director_module, "is_restore_required", lambda: True)
    monkeypatch.setattr(
        director_module,
        "restore_latest_user_config_backup",
        lambda **_kwargs: {"ok": False, "failed": [{"error": "manifest broken"}]},
    )
    mark_recorded = MagicMock()
    monkeypatch.setattr(director_module, "write_recording_state", mark_recorded)

    with pytest.raises(RuntimeError, match="部分玩家配置恢复失败"):
        director._restore_user_configs()

    mark_recorded.assert_not_called()
    assert director._user_config_snapshot == {not_a_file: b"original"}
