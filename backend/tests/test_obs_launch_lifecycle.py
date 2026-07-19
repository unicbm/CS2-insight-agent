from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main
from app.env_utils import AppConfig, OBSConfig


def _configure_endpoint(monkeypatch, obs_exe: Path) -> AppConfig:
    cfg = AppConfig(obs=OBSConfig(obs_path=str(obs_exe)))
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "save_config", lambda _cfg: None)
    monkeypatch.setattr(main, "minimize_obs_window", lambda: None)
    monkeypatch.setattr(
        main.obs_config_center,
        "get_connection_readiness",
        lambda *_args, **_kwargs: {"blockers": [], "connected": False},
    )
    return cfg


def test_config_check_reuses_connected_obs_without_process_probe_or_launch(
    tmp_path: Path,
    monkeypatch,
):
    obs_exe = tmp_path / "obs64.exe"
    obs_exe.write_bytes(b"MZ")
    _configure_endpoint(monkeypatch, obs_exe)
    process_probe = MagicMock(side_effect=AssertionError("must not inspect a connected OBS"))
    popen = MagicMock(side_effect=AssertionError("must not launch a connected OBS"))
    monkeypatch.setattr(main, "_test_obs_websocket_connection", lambda *_args: True)
    monkeypatch.setattr(main, "_obs_process_state", process_probe)
    monkeypatch.setattr(main.subprocess, "Popen", popen)

    result = main.obs_config_check(None)

    assert result["connected"] is True
    assert result["launched_obs"] is False
    process_probe.assert_not_called()
    popen.assert_not_called()


def test_config_check_double_checks_websocket_inside_launch_lock(
    tmp_path: Path,
    monkeypatch,
):
    obs_exe = tmp_path / "obs64.exe"
    obs_exe.write_bytes(b"MZ")
    _configure_endpoint(monkeypatch, obs_exe)
    probes = iter([False, True])
    process_probe = MagicMock(side_effect=AssertionError("second handshake already connected"))
    popen = MagicMock(side_effect=AssertionError("second handshake already connected"))
    monkeypatch.setattr(
        main,
        "_test_obs_websocket_connection",
        lambda *_args: next(probes),
    )
    monkeypatch.setattr(main, "_obs_process_state", process_probe)
    monkeypatch.setattr(main.subprocess, "Popen", popen)

    result = main.obs_config_check(None)

    assert result["connected"] is True
    assert result["launched_obs"] is False
    process_probe.assert_not_called()
    popen.assert_not_called()


def test_config_check_refuses_launch_when_process_state_is_unknown(
    tmp_path: Path,
    monkeypatch,
):
    obs_exe = tmp_path / "obs64.exe"
    obs_exe.write_bytes(b"MZ")
    _configure_endpoint(monkeypatch, obs_exe)
    popen = MagicMock()
    monkeypatch.setattr(main, "_test_obs_websocket_connection", lambda *_args: False)
    monkeypatch.setattr(main, "_obs_process_state", lambda _path: "unknown")
    monkeypatch.setattr(main.subprocess, "Popen", popen)

    result = main.obs_config_check(None)

    assert result["connected"] is False
    assert result["process_state"] == "unknown"
    assert result["launched_obs"] is False
    assert "阻止重复启动" in result["error"]
    popen.assert_not_called()


def test_config_check_launches_once_only_after_confirmed_absence(
    tmp_path: Path,
    monkeypatch,
):
    obs_exe = (
        tmp_path
        / "Steam Library"
        / "steamapps"
        / "common"
        / "OBS Studio"
        / "bin"
        / "64bit"
        / "obs64.exe"
    )
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_bytes(b"MZ")
    _configure_endpoint(monkeypatch, obs_exe)
    probes = iter([False, False, True])
    popen = MagicMock()
    monkeypatch.setattr(
        main,
        "_test_obs_websocket_connection",
        lambda *_args: next(probes),
    )
    monkeypatch.setattr(main, "find_windows_process_pids", lambda _name: set())
    monkeypatch.setattr(main.subprocess, "Popen", popen)

    result = main.obs_config_check(None)

    assert result["connected"] is True
    assert result["process_state"] == "absent"
    assert result["launched_obs"] is True
    popen.assert_called_once_with([str(obs_exe)], cwd=str(obs_exe.parent))


def test_process_probe_reports_running_from_native_pid_set(monkeypatch):
    probe = MagicMock(return_value={1234})
    monkeypatch.setattr(main, "find_windows_process_pids", probe)

    assert main._obs_process_state("C:\\OBS Studio\\bin\\64bit\\obs64.exe") == "running"
    probe.assert_called_once_with("obs64.exe")


def test_process_probe_reports_absent_from_empty_native_pid_set(monkeypatch):
    monkeypatch.setattr(main, "find_windows_process_pids", lambda _name: set())

    assert main._obs_process_state("C:\\OBS\\obs64.exe") == "absent"


def test_process_probe_native_api_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(main, "find_windows_process_pids", lambda _name: None)

    assert main._obs_process_state("C:\\OBS\\obs64.exe") == "unknown"


def test_explicit_launch_guard_rejects_unknown_process_state(
    tmp_path: Path,
    monkeypatch,
):
    obs_exe = tmp_path / "obs64.exe"
    obs_exe.write_bytes(b"MZ")
    _configure_endpoint(monkeypatch, obs_exe)
    popen = MagicMock()
    monkeypatch.setattr(main, "_test_obs_websocket_connection", lambda *_args: False)
    monkeypatch.setattr(main, "_obs_process_state", lambda _path: "unknown")
    monkeypatch.setattr(main.subprocess, "Popen", popen)

    with pytest.raises(HTTPException) as raised:
        main.obs_launch()

    assert raised.value.status_code == 503
    assert "阻止重复启动" in raised.value.detail
    popen.assert_not_called()
