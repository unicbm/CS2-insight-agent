from __future__ import annotations

from pathlib import Path

import pytest

from app import cs2_config_backup as backup
from app.env_utils import OBSConfig
from app.obs_director import OBSDirector


def _legacy_poisoned_keybinds() -> bytes:
    return b"\n".join(
        (
            b"unbindall",
            b"bind F10 toggleconsole",
            b"bind ` toggleconsole",
            b'bind "SPACE" "+jump"',
            b'bind "ESCAPE" "cancelselect"',
            b'bind "w" "+forward"',
            b'bind "a" "+moveleft"',
            b'bind "s" "+back"',
            b'bind "d" "+moveright"',
        )
    )


def _legacy_poisoned_vcfg() -> bytes:
    return b"\n".join(
        (
            b'"config"',
            b"{",
            b'    "bindings"',
            b"    {",
            b'        "MOUSE1" "<unbound>"',
            b'        "MOUSE2" "<unbound>"',
            b'        "MOUSE_X" "<unbound>"',
            b'        "MOUSE_Y" "<unbound>"',
            b'        "F10" "toggleconsole"',
            b'        "`" "toggleconsole"',
            b'        "SPACE" "+jump"',
            b'        "ESCAPE" "cancelselect"',
            b'        "W" "+forward"',
            b'        "A" "+moveleft"',
            b'        "S" "+back"',
            b'        "D" "+moveright"',
            b"    }",
            b"}",
        )
    )


def _healthy_vcfg() -> bytes:
    return b"\n".join(
        (
            b'"config"',
            b"{",
            b'    "bindings"',
            b"    {",
            b'        "MOUSE1" "+attack"',
            b'        "MOUSE2" "+attack2"',
            b'        "MOUSE_X" "yaw"',
            b'        "MOUSE_Y" "pitch"',
            b'        "W" "+forward"',
            b"    }",
            b"}",
        )
    )


def _fake_cs2_install(tmp_path: Path, *, with_default: bool = True) -> tuple[Path, Path]:
    cs2 = tmp_path / "Counter-Strike Global Offensive" / "game" / "bin" / "win64" / "cs2.exe"
    cs2.parent.mkdir(parents=True)
    cs2.write_bytes(b"fake exe")
    default = cs2.parents[2] / "csgo" / "cfg" / "user_keys_default.vcfg"
    if with_default:
        default.parent.mkdir(parents=True)
        default.write_bytes(_healthy_vcfg())
    return cs2, default


def _write_poisoned_vcfg_manifest(backup_root: Path, target: Path) -> None:
    rel = "saved/cs2_user_keys_0_slot0.vcfg"
    source = backup_root / rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_legacy_poisoned_vcfg())
    backup.write_manifest(
        {"entries": [{"original": str(target), "existed": True, "backup_relpath": rel}]}
    )
    backup.write_recording_state("recording")


@pytest.fixture
def backup_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "backup"
    root.mkdir()
    monkeypatch.setattr(backup, "get_backup_root", lambda: root)
    return root


@pytest.mark.parametrize(
    ("pids", "tri_state", "safe_bool"),
    [({4312}, True, True), (set(), False, False), (None, None, True)],
)
def test_native_process_probe_preserves_unknown_and_bool_wrapper_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    pids: set[int] | None,
    tri_state: bool | None,
    safe_bool: bool,
) -> None:
    monkeypatch.setattr(backup.sys, "platform", "win32")
    monkeypatch.setattr(backup, "find_cs2_hwnd", lambda: None)
    monkeypatch.setattr(backup, "find_windows_process_pids", lambda _name: pids)

    assert backup.probe_cs2_running() is tri_state
    assert backup.is_cs2_running() is safe_bool


def test_restore_refuses_unknown_process_state(
    backup_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.cfg"
    rel = "Steam/730/local/cfg/config.cfg"
    source = backup_root / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b"healthy")
    backup.write_manifest(
        {"entries": [{"original": str(target), "existed": True, "backup_relpath": rel}]}
    )
    monkeypatch.setattr(backup, "probe_cs2_running", lambda: None)

    result = backup.restore_latest_user_config_backup()

    assert result["ok"] is False
    assert result["code"] == "CS2_PROCESS_STATE_UNKNOWN"
    assert not target.exists()


def test_partial_restore_keeps_recording_state_for_retry(
    backup_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.cfg"
    second = tmp_path / "second.cfg"
    entries = []
    for target, payload in ((first, b"first"), (second, b"second")):
        rel = f"saved/{target.name}"
        source = backup_root / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        entries.append({"original": str(target), "existed": True, "backup_relpath": rel})
    backup.write_manifest({"entries": entries})
    backup.write_recording_state("recording")
    real_atomic_write = backup._atomic_write_bytes

    def fail_second(target: Path, data: bytes) -> None:
        if target == second:
            raise OSError("locked")
        real_atomic_write(target, data)

    monkeypatch.setattr(backup, "_atomic_write_bytes", fail_second)

    result = backup.restore_latest_user_config_backup(skip_cs2_running_check=True)

    assert result["ok"] is False
    assert result["restored"] == 1
    assert first.read_bytes() == b"first"
    assert not second.exists()
    assert backup.read_recording_state()["status"] == "recording"


def test_poisoned_backup_is_rejected_before_any_player_file_is_written(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.cfg"
    target.write_bytes(b"current-safe-data")
    rel = "saved/config.cfg"
    source = backup_root / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(_legacy_poisoned_keybinds())
    backup.write_manifest(
        {"entries": [{"original": str(target), "existed": True, "backup_relpath": rel}]}
    )
    backup.write_recording_state("recording")

    result = backup.restore_latest_user_config_backup(skip_cs2_running_check=True)

    assert result["ok"] is False
    assert result["code"] == "CONFIG_BACKUP_POISONED"
    assert target.read_bytes() == b"current-safe-data"
    assert backup.read_recording_state()["status"] == "recording"


def test_poisoned_key_vcfg_backup_preserves_healthy_current_and_clears_marker(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "userdata" / "123" / "730" / "local" / "cfg" / "cs2_user_keys_0_slot0.vcfg"
    target.parent.mkdir(parents=True)
    current = _healthy_vcfg() + b'\n"F9" "say custom"\n'
    target.write_bytes(current)
    _write_poisoned_vcfg_manifest(backup_root, target)

    result = backup.restore_latest_user_config_backup(skip_cs2_running_check=True)

    assert result["ok"] is True
    assert result["restored"] == 0
    assert result["preserved"] == [str(target)]
    assert target.read_bytes() == current
    assert backup.read_recording_state()["status"] == "recorded"


def test_poisoned_current_key_vcfg_is_replaced_by_local_official_default(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    cs2, default = _fake_cs2_install(tmp_path)
    target = tmp_path / "userdata" / "123" / "730" / "local" / "cfg" / "cs2_user_keys_0_slot0.vcfg"
    target.parent.mkdir(parents=True)
    target.write_bytes(_legacy_poisoned_vcfg())
    _write_poisoned_vcfg_manifest(backup_root, target)

    result = backup.restore_latest_user_config_backup(
        skip_cs2_running_check=True,
        cs2_path=str(cs2),
    )

    assert result["ok"] is True
    assert result["restored"] == 1
    assert target.read_bytes() == default.read_bytes() == _healthy_vcfg()
    quarantined = list(
        (backup_root / backup._POISONED_QUARANTINE_DIR_NAME / "123").glob(
            "cs2_user_keys_0_slot0.before-default-*.vcfg"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == _legacy_poisoned_vcfg()
    assert backup.read_recording_state()["status"] == "recorded"


def test_poisoned_current_key_vcfg_without_official_default_fails_and_keeps_marker(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    cs2, default = _fake_cs2_install(tmp_path, with_default=False)
    target = tmp_path / "userdata" / "123" / "730" / "local" / "cfg" / "cs2_user_keys_0_slot0.vcfg"
    target.parent.mkdir(parents=True)
    target.write_bytes(_legacy_poisoned_vcfg())
    _write_poisoned_vcfg_manifest(backup_root, target)

    result = backup.restore_latest_user_config_backup(
        skip_cs2_running_check=True,
        cs2_path=str(cs2),
    )

    assert result["ok"] is False
    assert result["code"] == "CONFIG_DEFAULT_KEY_VCFG_UNAVAILABLE"
    assert str(default) in result["failed"][0]["error"]
    assert target.read_bytes() == _legacy_poisoned_vcfg()
    assert backup.read_recording_state()["status"] == "recording"


def test_poisoned_current_key_vcfg_rejects_default_missing_mouse_binds(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    cs2, default = _fake_cs2_install(tmp_path)
    default.write_bytes(b'"MOUSE_X" "yaw"\n')
    target = tmp_path / "userdata" / "123" / "730" / "local" / "cfg" / "cs2_user_keys_0_slot0.vcfg"
    target.parent.mkdir(parents=True)
    target.write_bytes(_legacy_poisoned_vcfg())
    _write_poisoned_vcfg_manifest(backup_root, target)

    result = backup.restore_latest_user_config_backup(
        skip_cs2_running_check=True,
        cs2_path=str(cs2),
    )

    assert result["ok"] is False
    assert result["code"] == "CONFIG_DEFAULT_KEY_VCFG_UNAVAILABLE"
    assert "MOUSE1=+attack" in result["failed"][0]["error"]
    assert target.read_bytes() == _legacy_poisoned_vcfg()
    assert backup.read_recording_state()["status"] == "recording"


def test_restore_rejects_backup_relpath_outside_backup_root(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.cfg"
    outside = tmp_path / "outside.cfg"
    outside.write_bytes(b"do-not-restore")
    backup.write_manifest(
        {
            "entries": [
                {
                    "original": str(target),
                    "existed": True,
                    "backup_relpath": str(outside),
                }
            ]
        }
    )
    backup.write_recording_state("recording")

    result = backup.restore_latest_user_config_backup(skip_cs2_running_check=True)

    assert result["ok"] is False
    assert "越出备份目录" in result["failed"][0]["error"]
    assert not target.exists()
    assert backup.read_recording_state()["status"] == "recording"


def test_restore_rejects_manifest_entry_without_original(
    backup_root: Path,
) -> None:
    source = backup_root / "saved" / "config.cfg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"healthy")
    backup.write_manifest(
        {"entries": [{"existed": True, "backup_relpath": "saved/config.cfg"}]}
    )
    backup.write_recording_state("recording")

    result = backup.restore_latest_user_config_backup(skip_cs2_running_check=True)

    assert result["ok"] is False
    assert "缺少 original" in result["failed"][0]["error"]
    assert backup.read_recording_state()["status"] == "recording"


def test_recorded_state_poisoned_snapshot_migrates_before_new_backup(
    backup_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cs2, default = _fake_cs2_install(tmp_path)
    cfg_dir = tmp_path / "userdata" / "123" / "730" / "local" / "cfg"
    cfg_dir.mkdir(parents=True)
    target = cfg_dir / "cs2_user_keys_0_slot0.vcfg"
    target.write_bytes(_legacy_poisoned_vcfg())
    backup.write_recording_state("recorded")
    director = OBSDirector(OBSConfig(), str(cs2))
    monkeypatch.setattr(director, "_candidate_user_config_dirs", lambda: [cfg_dir])

    director._snapshot_user_configs()

    assert target.read_bytes() == default.read_bytes() == _healthy_vcfg()
    assert director._user_config_snapshot[target] == _healthy_vcfg()
    assert backup.find_poisoned_keybind_backup_paths(director._user_config_snapshot) == []
    manifest_entry = next(
        ent for ent in backup.read_manifest()["entries"] if ent["original"] == str(target)
    )
    assert (backup_root / manifest_entry["backup_relpath"]).read_bytes() == _healthy_vcfg()
    assert backup.read_recording_state()["status"] == "recording"
    assert list((backup_root / backup._POISONED_QUARANTINE_DIR_NAME / "123").glob("*.vcfg"))


def test_poisoned_snapshot_cannot_overwrite_last_healthy_backup(
    backup_root: Path,
    tmp_path: Path,
) -> None:
    sentinel = backup_root / "last-healthy-backup"
    sentinel.write_bytes(b"keep")
    config = tmp_path / "config.cfg"

    result = backup.write_persistent_backup_from_snap(
        {config: _legacy_poisoned_keybinds()}
    )

    assert result is None
    assert sentinel.read_bytes() == b"keep"


def test_mouse_axis_binding_prevents_false_poison_match(tmp_path: Path) -> None:
    config = tmp_path / "config.cfg"
    payload = _legacy_poisoned_keybinds() + b'\nbind "MOUSE_X" "yaw"\n'

    assert backup.find_poisoned_keybind_backup_paths({config: payload}) == []


def test_real_quoted_vcfg_unbound_marker_is_recognised(tmp_path: Path) -> None:
    key_file = tmp_path / "cs2_user_keys_0_slot0.vcfg"

    assert backup.find_poisoned_keybind_backup_paths(
        {key_file: _legacy_poisoned_vcfg()}
    ) == [key_file]


def test_healthy_second_account_does_not_mask_poisoned_first_account(tmp_path: Path) -> None:
    poisoned = tmp_path / "account1" / "cs2_user_keys_0_slot0.vcfg"
    healthy = tmp_path / "account2" / "cs2_user_keys_0_slot0.vcfg"
    healthy_payload = _legacy_poisoned_vcfg() + b'\n"MOUSE_X" "yaw"\n'

    assert backup.find_poisoned_keybind_backup_paths(
        {poisoned: _legacy_poisoned_vcfg(), healthy: healthy_payload}
    ) == [poisoned]
