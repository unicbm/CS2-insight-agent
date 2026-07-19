"""CS2 玩家配置磁盘备份、manifest 与 recording_state 持久化（录制异常恢复）。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .env_utils import find_windows_process_pids, get_data_dir
from .win_cs2_console import find_cs2_hwnd

logger = logging.getLogger(__name__)

_BACKUP_DIR_NAME = ".cs2_config_backup"
RECORDING_STATE_FILENAME = "recording_state.json"
MANIFEST_FILENAME = "manifest.json"
README_FILENAME = "恢复说明.txt"
RECORDING_STATE_VERSION = 1
MANIFEST_VERSION = 4

RECOVERY_README_TEXT = """这是 CS2 Insight Agent 在录制前自动保存的玩家原始配置。

如果软件异常退出，导致 CS2 键位、画面设置或控制台参数没有恢复，请使用软件内的“一键恢复玩家配置”功能。

恢复前请注意：

1. 请先关闭 CS2。
2. 然后打开 CS2 Insight Agent。
3. 在软件提示中点击“一键恢复玩家配置”。
4. 恢复完成后重新进入 CS2。

请不要手动修改本目录下的 manifest.json。
请不要删除本目录，否则可能无法恢复录制前的玩家配置。
"""

CONFIG_RESTORE_REQUIRED = {
    "code": "RECORDING_CONFIG_RESTORE_REQUIRED",
}

_POISONED_KEYBIND_CODE = "CONFIG_BACKUP_POISONED"
_DEFAULT_KEY_VCFG_UNAVAILABLE_CODE = "CONFIG_DEFAULT_KEY_VCFG_UNAVAILABLE"
_POISONED_QUARANTINE_DIR_NAME = "legacy_poisoned_key_vcfg"
_POISONED_MOUSE_KEYS = frozenset({"MOUSE1", "MOUSE2", "MOUSE_X", "MOUSE_Y"})
_OFFICIAL_DEFAULT_MOUSE_BINDS = {
    "MOUSE1": "+attack",
    "MOUSE2": "+attack2",
    "MOUSE_X": "yaw",
    "MOUSE_Y": "pitch",
}
_POISONED_EXPECTED_BINDS = {
    "F10": "toggleconsole",
    "W": "+forward",
    "A": "+moveleft",
    "S": "+back",
    "D": "+moveright",
    "SPACE": "+jump",
    "ESCAPE": "cancelselect",
}
_BIND_COMMAND_RE = re.compile(
    r'^\s*bind\s+(?:"([^"]+)"|(\S+))\s+(?:"([^"]*)"|(\S+))',
    re.IGNORECASE,
)
_UNBIND_COMMAND_RE = re.compile(
    r'^\s*unbind\s+(?:"([^"]+)"|(\S+))',
    re.IGNORECASE,
)
_QUOTED_BIND_PAIR_RE = re.compile(r'^\s*"([^"]+)"\s+"([^"]*)"')


def get_backup_root() -> Path:
    """``<repo>/data/.cs2_config_backup``（旧版本曾为仓库根下的同名目录，启动时自动迁入 data）。"""
    return get_data_dir() / _BACKUP_DIR_NAME


def get_recording_state_path() -> Path:
    return get_backup_root() / RECORDING_STATE_FILENAME


def get_manifest_path() -> Path:
    return get_backup_root() / MANIFEST_FILENAME


def read_recording_state() -> dict[str, Any]:
    p = get_recording_state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read recording_state failed: %s", e)
        return {}


def write_recording_state(status: str, extra: Optional[dict[str, Any]] = None) -> None:
    state_path = get_recording_state_path()
    backup_dir = state_path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    payload: dict[str, Any] = {
        "version": RECORDING_STATE_VERSION,
        "status": status,
        "started_at": now,
        "started_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "backup_dir": str(backup_dir.resolve()),
    }
    if status == "recording":
        payload["message"] = "录制中，玩家配置已备份，等待正常恢复"
    elif status == "recorded":
        payload["message"] = "玩家配置状态正常"
    if extra:
        payload.update(extra)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)


def is_restore_required() -> bool:
    return read_recording_state().get("status") == "recording"


def extract_steam_account_id(path: Path) -> Optional[str]:
    parts = path.parts
    for i, part in enumerate(parts):
        if part.lower() == "userdata" and i + 1 < len(parts):
            cand = parts[i + 1]
            if cand.isdigit():
                return cand
    return None


def _steam_backup_prefix(account_id: Optional[str]) -> str:
    if account_id:
        return f"Steam账号_{account_id}"
    return "未知账号"


def make_backup_relpath(original: Path, used: set[str]) -> str:
    account_id = extract_steam_account_id(original)
    prefix = _steam_backup_prefix(account_id)
    fname = original.name
    base = f"{prefix}/730/local/cfg/{fname}"
    if base not in used:
        used.add(base)
        return base
    stem = original.stem
    suf = original.suffix
    h = hex(abs(hash(str(original.resolve()))))[-8:]
    candidate = f"{prefix}/730/local/cfg/{stem}_{h}{suf}"
    n = 0
    while candidate in used:
        n += 1
        candidate = f"{prefix}/730/local/cfg/{stem}_{h}_{n}{suf}"
    used.add(candidate)
    return candidate


def read_manifest() -> dict[str, Any]:
    p = get_manifest_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read manifest failed: %s", e)
        return {}


def write_manifest(manifest: dict[str, Any]) -> None:
    p = get_manifest_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def probe_cs2_running() -> Optional[bool]:
    """Return CS2 presence as ``True`` / ``False`` / ``None`` (probe unknown).

    A failed process enumeration is deliberately *not* collapsed to ``False``:
    restoring cfg files while CS2 may still be alive lets its exit autosave
    immediately overwrite the restored data again.
    """
    window_probe_failed = False
    try:
        if find_cs2_hwnd():
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not query the CS2 window: %s", e)
        window_probe_failed = True

    if sys.platform != "win32":
        return None if window_probe_failed else False

    pids = find_windows_process_pids("cs2.exe")
    if pids:
        return True
    if pids is None or window_probe_failed:
        return None
    return False


def is_cs2_running() -> bool:
    """Fail-closed compatibility wrapper for existing boolean callers.

    Startup/restore call sites written as ``if is_cs2_running()`` must never
    interpret a native enumeration failure as permission to mutate cfg files.
    """
    return probe_cs2_running() is not False


def _normalise_bind_key(raw: str) -> str:
    return str(raw or "").strip().strip('"').upper()


def _normalise_bind_action(raw: str) -> str:
    return " ".join(str(raw or "").strip().strip('"').lower().split())


def _keybind_candidate(path: Path) -> bool:
    name = path.name.lower()
    return name in {"config.cfg", "cs2_user.cfg"} or (
        "key" in name and path.suffix.lower() in {".cfg", ".vcfg", ".txt"}
    )


def _parse_keybinds(data: bytes) -> dict[str, set[Optional[str]]]:
    """Extract key bindings from console cfg and Valve quoted key/value files."""
    text = data.decode("utf-8-sig", errors="ignore").replace("\x00", "")
    bindings: dict[str, set[Optional[str]]] = {}

    def add(key: str, action: Optional[str]) -> None:
        normal_key = _normalise_bind_key(key)
        if not normal_key:
            return
        normal_action = None if action is None else _normalise_bind_action(action)
        bindings.setdefault(normal_key, set()).add(normal_action)

    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0]
        match = _BIND_COMMAND_RE.match(line)
        if match:
            add(match.group(1) or match.group(2) or "", match.group(3) or match.group(4) or "")
            continue
        match = _UNBIND_COMMAND_RE.match(line)
        if match:
            add(match.group(1) or match.group(2) or "", None)
            continue
        match = _QUOTED_BIND_PAIR_RE.match(line)
        if match:
            add(match.group(1), match.group(2))
    return bindings


def find_poisoned_keybind_backup_paths(
    files: dict[Path, Optional[bytes]],
) -> list[Path]:
    """Recognise the exact legacy ``unbindall`` minimal-bind fingerprint.

    This intentionally does not invent replacement bindings.  It only rejects
    a key file when every old recorder-owned default is present, both console
    toggles are present, and MOUSE1/MOUSE2/MOUSE_X/MOUSE_Y have no live binding.
    Files are evaluated independently so a healthy second Steam account cannot
    hide a poisoned first account (or vice versa).
    """
    poisoned: list[Path] = []
    for path, data in files.items():
        if data is None or not _keybind_candidate(path):
            continue
        parsed = _parse_keybinds(data)
        if not parsed:
            continue
        if any(
            expected not in parsed.get(key, set())
            for key, expected in _POISONED_EXPECTED_BINDS.items()
        ):
            continue
        tilde_actions = parsed.get("`", set()) | parsed.get("~", set())
        if "toggleconsole" not in tilde_actions:
            continue
        if any(
            any(action not in {None, "", "none", "<unbound>"} for action in parsed.get(key, set()))
            for key in _POISONED_MOUSE_KEYS
        ):
            continue
        poisoned.append(path)
    return poisoned


def _is_user_key_vcfg(path: Path) -> bool:
    """Only CS2's per-user key table is schema-compatible with the shipped default."""
    name = path.name.lower()
    return path.suffix.lower() == ".vcfg" and name.startswith("cs2_user_keys")


def _official_default_key_vcfg(cs2_path: Optional[str]) -> tuple[Optional[Path], Optional[bytes], str]:
    """Read ``game/csgo/cfg/user_keys_default.vcfg`` from the configured install.

    Deliberately do not search other Steam libraries or manufacture bindings:
    recovery is allowed only from the local game payload that owns ``cs2.exe``.
    """
    raw = str(cs2_path or "").strip()
    if not raw:
        return None, None, "未配置 cs2_path，无法定位游戏自带默认键位文件"
    try:
        cs2 = Path(raw)
        default_path = cs2.parents[2] / "csgo" / "cfg" / "user_keys_default.vcfg"
    except (IndexError, OSError, ValueError) as e:
        return None, None, f"cs2_path 无法定位 game/csgo/cfg: {e}"
    if not cs2.is_file():
        return default_path, None, f"cs2_path 不存在或不是文件: {cs2}"
    if not default_path.is_file():
        return default_path, None, f"游戏自带默认键位文件不存在: {default_path}"
    try:
        data = default_path.read_bytes()
    except OSError as e:
        return default_path, None, f"游戏自带默认键位文件无法读取: {default_path}: {e}"
    if not data:
        return default_path, None, f"游戏自带默认键位文件为空: {default_path}"
    if find_poisoned_keybind_backup_paths({default_path: data}):
        return default_path, None, f"游戏自带默认键位文件也命中旧版污染指纹: {default_path}"
    parsed = _parse_keybinds(data)
    missing_mouse_binds = [
        f"{key}={action}"
        for key, action in _OFFICIAL_DEFAULT_MOUSE_BINDS.items()
        if action not in parsed.get(key, set())
    ]
    if missing_mouse_binds:
        return (
            default_path,
            None,
            "游戏自带默认键位文件缺少核心鼠标绑定，拒绝用它覆盖玩家配置: "
            + ", ".join(missing_mouse_binds),
        )
    return default_path, data, ""


def _quarantine_poisoned_key_vcfg(path: Path, data: bytes) -> Path:
    """Keep the exact poisoned bytes before replacing them with Valve's default."""
    account = extract_steam_account_id(path) or "unknown-account"
    quarantine = get_backup_root() / _POISONED_QUARANTINE_DIR_NAME / account
    stamp = time.time_ns()
    target = quarantine / f"{path.stem}.before-default-{stamp}{path.suffix}"
    _atomic_write_bytes(target, data)
    return target


def migrate_legacy_poisoned_key_vcfg_targets(
    targets: list[Path],
    *,
    cs2_path: Optional[str],
) -> dict[str, Any]:
    """Make exact legacy-poisoned key VCFG targets safe without guessing binds.

    A target that no longer matches the fingerprint is preserved byte-for-byte.
    A target that still matches is quarantined and atomically replaced only by
    the configured CS2 install's ``user_keys_default.vcfg``.  All paths and the
    default source are validated before the first target is changed.
    """
    unique_targets = list(dict.fromkeys(Path(path) for path in targets))
    invalid = [path for path in unique_targets if not _is_user_key_vcfg(path)]
    if invalid:
        return {
            "ok": False,
            "code": _POISONED_KEYBIND_CODE,
            "migrated": [],
            "preserved": [],
            "failed": [
                {
                    "original": str(path),
                    "error": "旧版 unbindall 污染只允许从官方默认文件迁移 cs2_user_keys*.vcfg；该目标不是可安全迁移的 key VCFG",
                }
                for path in invalid
            ],
        }

    current_data: dict[Path, bytes] = {}
    failed: list[dict[str, str]] = []
    for path in unique_targets:
        try:
            if not path.is_file():
                failed.append({"original": str(path), "error": "当前 key VCFG 不存在，拒绝猜测用户原键位"})
                continue
            current_data[path] = path.read_bytes()
        except OSError as e:
            failed.append({"original": str(path), "error": f"当前 key VCFG 无法读取: {e}"})
    if failed:
        return {
            "ok": False,
            "code": _POISONED_KEYBIND_CODE,
            "migrated": [],
            "preserved": [],
            "failed": failed,
        }

    still_poisoned = find_poisoned_keybind_backup_paths(current_data)
    poisoned_set = set(still_poisoned)
    preserved = [str(path) for path in unique_targets if path not in poisoned_set]
    if not still_poisoned:
        return {
            "ok": True,
            "migrated": [],
            "preserved": preserved,
            "failed": [],
        }

    default_path, default_data, default_error = _official_default_key_vcfg(cs2_path)
    if default_data is None:
        return {
            "ok": False,
            "code": _DEFAULT_KEY_VCFG_UNAVAILABLE_CODE,
            "migrated": [],
            "preserved": preserved,
            "failed": [
                {
                    "original": str(path),
                    "error": default_error,
                }
                for path in still_poisoned
            ],
            "default_path": str(default_path) if default_path else "",
        }

    migrated: list[str] = []
    migration_failed: list[dict[str, str]] = []
    quarantined: list[str] = []
    for path in still_poisoned:
        try:
            # Re-read immediately before mutation so a concurrently repaired
            # healthy file is never overwritten by the default.
            live_data = path.read_bytes()
            if not find_poisoned_keybind_backup_paths({path: live_data}):
                if str(path) not in preserved:
                    preserved.append(str(path))
                continue
            quarantine_path = _quarantine_poisoned_key_vcfg(path, live_data)
            quarantined.append(str(quarantine_path))
            # Steam Cloud may rewrite the file while the forensic copy is
            # being created.  Re-check immediately before replacement so a
            # concurrently repaired healthy table is never overwritten.
            latest_data = path.read_bytes()
            if not find_poisoned_keybind_backup_paths({path: latest_data}):
                if str(path) not in preserved:
                    preserved.append(str(path))
                continue
            if latest_data != live_data:
                quarantine_path = _quarantine_poisoned_key_vcfg(path, latest_data)
                quarantined.append(str(quarantine_path))
            _atomic_write_bytes(path, default_data)
            migrated.append(str(path))
        except OSError as e:
            migration_failed.append({"original": str(path), "error": f"默认键位迁移失败: {e}"})

    return {
        "ok": not migration_failed,
        "code": "CONFIG_POISONED_KEY_VCFG_MIGRATED" if not migration_failed else _POISONED_KEYBIND_CODE,
        "migrated": migrated,
        "preserved": preserved,
        "quarantined": quarantined,
        "failed": migration_failed,
        "default_path": str(default_path),
    }


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".cs2insight.tmp")
    tmp.write_bytes(data)
    # Windows 上目标文件可能被 AV / Steam Cloud / CS2 exit autosave 短暂锁定，
    # 重试最多 4 次（等待 0.3 / 0.6 / 1.2 / 2.4 s），覆盖绝大多数瞬时锁场景。
    last_err: Optional[OSError] = None
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            return
        except OSError as e:
            last_err = e
            if attempt < 4:
                time.sleep(0.3 * (2 ** attempt))
    raise last_err  # type: ignore[misc]


def restore_latest_user_config_backup(
    *,
    skip_cs2_running_check: bool = False,
    cs2_path: Optional[str] = None,
) -> dict[str, Any]:
    """按 manifest 将备份写回原始路径；全部成功后将 ``recording_state`` 置为 ``recorded``。"""
    backup_root = get_backup_root()
    manifest = read_manifest()
    entries: list[dict[str, Any]] = list(manifest.get("entries") or [])
    state = read_recording_state()
    status_was = state.get("status")

    if not entries:
        if status_was == "recording":
            return {
                "ok": False,
                "restored": 0,
                "failed": [{"original": "", "error": "manifest 缺失或 entries 为空，无法恢复"}],
            }
        return {"ok": True, "restored": 0, "failed": [], "code": "CONFIG_NO_MANIFEST"}

    if not skip_cs2_running_check:
        running = probe_cs2_running()
        if running is not False:
            return {
                "ok": False,
                "code": "CS2_RUNNING" if running is True else "CS2_PROCESS_STATE_UNKNOWN",
                "restored": 0,
                "failed": [],
            }

    # Load and validate every source before touching any player file.  Older
    # releases could persist the post-``unbindall`` minimal key table as the
    # "backup"; restoring it would recreate the no-mouse-input failure.
    restore_data: dict[Path, Optional[bytes]] = {}
    source_errors: list[dict[str, str]] = []
    for ent in entries:
        orig_s = ent.get("original")
        if not orig_s:
            source_errors.append({"original": "", "error": "manifest 缺少 original，拒绝静默跳过恢复目标"})
            continue
        if not bool(ent.get("existed", True)):
            continue
        original = Path(orig_s)
        rel = ent.get("backup_relpath")
        if not rel:
            source_errors.append({"original": str(original), "error": "manifest 缺少 backup_relpath"})
            continue
        rel_path = Path(str(rel))
        try:
            backup_root_resolved = backup_root.resolve()
            src = (backup_root / rel_path).resolve()
            if rel_path.is_absolute() or not src.is_relative_to(backup_root_resolved):
                source_errors.append(
                    {"original": str(original), "error": f"backup_relpath 越出备份目录: {rel}"}
                )
                continue
        except OSError as e:
            source_errors.append({"original": str(original), "error": f"备份路径无法解析: {rel}: {e}"})
            continue
        try:
            restore_data[original] = src.read_bytes()
        except OSError as e:
            source_errors.append({"original": str(original), "error": f"备份文件无法读取: {rel}: {e}"})

    if source_errors:
        return {"ok": False, "restored": 0, "failed": source_errors}

    poisoned = find_poisoned_keybind_backup_paths(restore_data)
    migration: dict[str, Any] = {}
    if poisoned:
        migration = migrate_legacy_poisoned_key_vcfg_targets(poisoned, cs2_path=cs2_path)
        if not migration.get("ok"):
            return {
                "ok": False,
                "code": migration.get("code") or _POISONED_KEYBIND_CODE,
                "restored": len(migration.get("migrated") or []),
                "preserved": migration.get("preserved") or [],
                "failed": migration.get("failed") or [],
            }

    failed: list[dict[str, str]] = []
    poisoned_set = set(poisoned)
    restored = len(migration.get("migrated") or [])
    preserved = list(migration.get("preserved") or [])

    for ent in entries:
        orig_s = ent.get("original")
        if not orig_s:
            continue
        original = Path(orig_s)
        if original in poisoned_set:
            # A healthy current key table was preserved, or a poisoned current
            # table was already atomically replaced by Valve's local default.
            continue
        existed = bool(ent.get("existed", True))
        try:
            if existed:
                data = restore_data[original]
                if data is None:  # defensive: existed entries were preloaded above
                    failed.append({"original": str(original), "error": "备份内容为空"})
                    continue
                _atomic_write_bytes(original, data)
                restored += 1
            else:
                if original.is_file():
                    original.unlink()
                    restored += 1
        except OSError as e:
            failed.append({"original": str(original), "error": str(e)})

    # Only a fully successful restore may clear the recovery marker.  Keeping
    # ``recording`` on partial failure is what makes a later retry possible.
    if not failed and status_was == "recording":
        try:
            write_recording_state("recorded")
        except OSError as e:
            logger.warning("write_recording_state(recorded) failed: %s", e)
            failed.append({"original": str(get_recording_state_path()), "error": str(e)})

    if failed:
        return {"ok": False, "restored": restored, "preserved": preserved, "failed": failed}

    return {"ok": True, "restored": restored, "preserved": preserved, "failed": []}


def _clear_snapshot_contents_preserving_quarantine(backup_dir: Path) -> None:
    if not backup_dir.exists():
        return
    for child in backup_dir.iterdir():
        if child.name == _POISONED_QUARANTINE_DIR_NAME:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def write_persistent_backup_from_snap(snap: dict[Path, Optional[bytes]]) -> Optional[Path]:
    """清空备份目录、按 Steam 账号分层落盘、写 manifest / 说明 / ``recording_state=recording``。"""
    if not snap:
        return None
    if is_restore_required():
        logger.warning("Refusing persistent backup: restore_required (recording state)")
        return None

    poisoned = find_poisoned_keybind_backup_paths(snap)
    if poisoned:
        logger.error(
            "Refusing to overwrite the last backup with legacy poisoned keybind data: %s",
            [str(path) for path in poisoned],
        )
        return None

    backup_dir = get_backup_root()
    try:
        if backup_dir.exists():
            # Keep forensic copies made before a legacy poisoned key table was
            # replaced.  Everything else remains the single latest snapshot.
            _clear_snapshot_contents_preserving_quarantine(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Persistent backup mkdir %s failed: %s", backup_dir, e)
        return None

    used_rel: set[str] = set()
    entries: list[dict[str, Any]] = []
    now = time.time()
    created_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

    for orig_path in sorted(snap.keys(), key=lambda p: str(p).lower()):
        original = snap[orig_path]
        account_id = extract_steam_account_id(orig_path)
        entry: dict[str, Any] = {
            "account_id": account_id or "",
            "original": str(orig_path),
            "filename": orig_path.name,
            "existed": original is not None,
        }
        if original is not None:
            rel = make_backup_relpath(orig_path, used_rel)
            target = backup_dir / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)
            except OSError as e:
                logger.warning("Persistent backup write %s failed: %s", target, e)
                try:
                    _clear_snapshot_contents_preserving_quarantine(backup_dir)
                except OSError:
                    pass
                return None
            entry["backup_relpath"] = rel
        entries.append(entry)

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "created_at": now,
        "created_at_iso": created_iso,
        "entries": entries,
    }
    try:
        write_manifest(manifest)
        (backup_dir / README_FILENAME).write_text(RECOVERY_README_TEXT, encoding="utf-8")
        write_recording_state("recording")
    except OSError as e:
        logger.warning("Persistent backup finalize failed: %s", e)
        try:
            _clear_snapshot_contents_preserving_quarantine(backup_dir)
        except OSError:
            pass
        return None

    logger.info(
        "Persistent backup written: %s (%d files)",
        backup_dir,
        len([e for e in entries if e.get("existed")]),
    )
    return backup_dir


def build_config_backup_status_payload() -> dict[str, Any]:
    state = read_recording_state()
    status = state.get("status") or "recorded"
    rr = status == "recording"
    manifest = read_manifest()
    accounts: set[str] = set()
    for ent in manifest.get("entries") or []:
        aid = ent.get("account_id")
        if aid:
            accounts.add(str(aid))
        else:
            op = ent.get("original")
            if op:
                aid2 = extract_steam_account_id(Path(str(op)))
                if aid2:
                    accounts.add(aid2)
    backup_dir = str(get_backup_root().resolve())
    return {
        "status": status if status in ("recording", "recorded") else "recorded",
        "restore_required": rr,
        "backup_dir": backup_dir,
        "created_at_iso": state.get("started_at_iso") or manifest.get("created_at_iso") or "",
        "accounts": sorted(accounts),
        "cs2_running": is_cs2_running(),
    }


def open_backup_directory() -> dict[str, Any]:
    root = get_backup_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    path_str = str(root.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(path_str)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False, timeout=30)
        else:
            subprocess.run(["xdg-open", path_str], check=False, timeout=30)
        return {"ok": True, "backup_dir": path_str}
    except Exception as e:  # noqa: BLE001
        logger.warning("open backup dir failed: %s", e)
        return {
            "ok": False,
            "backup_dir": path_str,
            "code": "CONFIG_OPEN_DIR_FAIL",
        }
