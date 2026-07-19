"""OBS 配置中心：诊断、推荐预设、.cs2obs 与原生文件导入、备份恢复（主要面向 Windows 本机 OBS 配置目录）。"""

from __future__ import annotations

import configparser
import filecmp
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from obswebsocket import obsws, requests as obs_requests

from .env_utils import get_data_dir

logger = logging.getLogger(__name__)

APP_VERSION = "V2.1.0"
DEFAULT_PROJECT_PROFILE = "未命名"  # 解析失败时的兜底目录名；正常由 resolve_default_project_profile_for_obs() 解析
BACKUP_SUBDIR = ".obs_config_backups"


def _dedicated_scene_name() -> str:
    s = (os.environ.get("CS2_INSIGHT_OBS_SCENE_NAME") or "CS2 Insight Recording").strip()
    return s or "CS2 Insight Recording"


def _dedicated_capture_name() -> str:
    s = (os.environ.get("CS2_INSIGHT_OBS_GAME_CAPTURE_NAME") or "CS2 Insight Game Capture").strip()
    return s or "CS2 Insight Game Capture"


def _obs_studio_root() -> Optional[Path]:
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "obs-studio"


def _as_optional_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _read_obs_websocket_settings(obs_root: Optional[Path] = None) -> dict[str, Any]:
    """读取 OBS WebSocket 插件配置，只返回连接元数据，绝不返回密码内容。"""
    root = obs_root if obs_root is not None else _obs_studio_root()
    result: dict[str, Any] = {
        "settings_found": False,
        "server_enabled": None,
        "server_port": None,
        "auth_required": None,
        "obs_password_present": False,
    }
    if root is None:
        return result
    path = root / "plugin_config" / "obs-websocket" / "config.json"
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Cannot read OBS WebSocket settings: %s", path, exc_info=True)
        return result
    if not isinstance(payload, dict):
        return result
    values = {str(key).casefold(): value for key, value in payload.items()}
    result["settings_found"] = True
    result["server_enabled"] = _as_optional_bool(values.get("server_enabled"))
    result["auth_required"] = _as_optional_bool(values.get("auth_required"))
    try:
        port = int(values.get("server_port") or 0)
    except (TypeError, ValueError):
        port = 0
    result["server_port"] = port if 1 <= port <= 65535 else None
    result["obs_password_present"] = bool(str(values.get("server_password") or "").strip())
    return result


def _tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def get_connection_readiness(
    obs_cfg,
    *,
    obs_root: Optional[Path] = None,
    probe_port: bool = True,
) -> dict[str, Any]:
    """无需 WebSocket 握手即可给出连接准备状态和明确阻断原因。"""
    settings = _read_obs_websocket_settings(obs_root)
    host = str(getattr(obs_cfg, "host", "") or "localhost").strip() or "localhost"
    try:
        port = int(getattr(obs_cfg, "port", 4455) or 4455)
    except (TypeError, ValueError):
        port = 4455
    app_password_present = bool(str(getattr(obs_cfg, "password", "") or "").strip())
    server_port = settings.get("server_port")
    port_matches = server_port is None or int(server_port) == port
    auth_required = settings.get("auth_required")
    credentials_ready = auth_required is not True or app_password_present
    port_open = _tcp_port_open(host, port) if probe_port and 1 <= port <= 65535 else None

    blockers: list[str] = []
    if not settings["settings_found"]:
        blockers.append("OBS_WEBSOCKET_CONFIG_NOT_FOUND")
    if settings["server_enabled"] is False:
        blockers.append("OBS_WEBSOCKET_DISABLED")
    if not port_matches:
        blockers.append("OBS_PORT_MISMATCH")
    if not credentials_ready:
        blockers.append("OBS_PASSWORD_REQUIRED")
    if port_open is False:
        blockers.append("OBS_PORT_NOT_LISTENING")

    return {
        "host": host,
        "configured_port": port,
        **settings,
        "app_password_present": app_password_present,
        "port_matches": port_matches,
        "credentials_ready": credentials_ready,
        "port_open": port_open,
        "connected": False,
        "blockers": blockers,
    }


def _backup_root() -> Path:
    return get_data_dir() / BACKUP_SUBDIR


def _read_global_profile_names(obs_root: Path) -> tuple[Optional[str], Optional[str]]:
    g = obs_root / "global.ini"
    if not g.is_file():
        return None, None
    try:
        raw = g.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    values: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";", "[")) or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip().casefold()
        value = value.strip()
        if value:
            values[key] = value
    # OBS 30+ stores filesystem-safe directory names in ProfileDir and
    # SceneCollectionFile.  Keep the legacy keys as fallbacks for older installs.
    prof = (
        values.get("profiledir")
        or values.get("currentprofile")
        or values.get("profile")
    )
    sc = (
        values.get("scenecollectionfile")
        or values.get("currentscenecollection")
        or values.get("scenecollection")
    )
    return prof, sc


def _set_global_ini_current_profile(obs_root: Path, profile_name: str) -> bool:
    g = obs_root / "global.ini"
    if not g.is_file():
        return False
    try:
        lines = g.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    key = "CurrentProfile="
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip().lower().startswith("currentprofile="):
            out.append(f"CurrentProfile={profile_name}")
            found = True
        else:
            out.append(line)
    if not found:
        # 追加到 [General] 后或文件头
        inserted = False
        final: list[str] = []
        for i, line in enumerate(out):
            final.append(line)
            if line.strip() == "[General]" and i + 1 < len(out) and not any(
                x.strip().lower().startswith("currentprofile=") for x in out[i + 1 : i + 5]
            ):
                final.append(f"CurrentProfile={profile_name}")
                inserted = True
        if not inserted:
            final = [f"CurrentProfile={profile_name}", ""] + out
        out = final
    try:
        g.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


# OBS 安装后常见的默认配置目录名（中文界面为「未命名」，英文为 Untitled）。不使用环境变量覆盖。
_OBS_DEFAULT_PROFILE_FOLDER_NAMES: tuple[str, ...] = ("未命名", "Untitled", "Default")


def _profile_template_dir(profiles_root: Path) -> Optional[Path]:
    """从已有 Profile 中选一个作为新建目录的拷贝模板。"""
    for name in _OBS_DEFAULT_PROFILE_FOLDER_NAMES:
        p = profiles_root / name
        if p.is_dir():
            return p
    try:
        subs = sorted(
            [x for x in profiles_root.iterdir() if x.is_dir()],
            key=lambda x: x.name.lower(),
        )
        if subs:
            return subs[0]
    except OSError:
        pass
    return None


def _resolve_obs_profile_folder_name(obs_root: Path) -> str:
    """解析本机应写入的 OBS Profile 目录名：优先默认文件夹（未命名 / Untitled），否则 CurrentProfile，再否则首个子目录。不使用环境变量。"""
    profiles_root = obs_root / "basic" / "profiles"
    for name in _OBS_DEFAULT_PROFILE_FOLDER_NAMES:
        if (profiles_root / name).is_dir():
            return name
    cur, _ = _read_global_profile_names(obs_root)
    if cur:
        c = cur.strip()
        if c and (profiles_root / c).is_dir():
            return c
    try:
        subs = [p for p in profiles_root.iterdir() if p.is_dir()]
        if subs:
            return sorted(subs, key=lambda p: p.name.lower())[0].name
    except OSError:
        pass
    return DEFAULT_PROJECT_PROFILE


def resolve_default_project_profile_for_obs() -> str:
    """解析默认 OBS Profile 目录名（Windows）；非 Windows 返回 ``Untitled``（后续文件 API 仍会拒绝）。"""
    if sys.platform != "win32":
        return "Untitled"
    root = _obs_studio_root()
    if root is None:
        return DEFAULT_PROJECT_PROFILE
    return _resolve_obs_profile_folder_name(root)


def _effective_project_profile(obs_root: Path, explicit: Optional[str]) -> str:
    """explicit 非空则用之；否则按本机 OBS 目录解析默认 Profile（未命名 / Untitled 等）。"""
    e = (explicit or "").strip()
    if e:
        return e
    return _resolve_obs_profile_folder_name(obs_root)


def _read_profile_ini(ini_path: Path) -> Optional[configparser.ConfigParser]:
    if not ini_path.is_file():
        return None
    # OBS profile templates can contain legacy lower-case keys alongside the
    # canonical mixed-case variants. Preserve case while parsing, then fold it
    # ourselves so the later/canonical entry wins deterministically.
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(ini_path, encoding="utf-8-sig")
    except (OSError, configparser.Error):
        return None
    return cp


def _parse_ini_section(ini_path: Path, section_name: str) -> dict[str, str]:
    cp = _read_profile_ini(ini_path)
    if cp is None:
        return {}
    actual = next(
        (name for name in cp.sections() if name.casefold() == section_name.casefold()),
        None,
    )
    if actual is None:
        return {}
    return {str(key).casefold(): str(value) for key, value in cp[actual].items()}


def _parse_simple_output(ini_path: Path) -> dict[str, str]:
    return _parse_ini_section(ini_path, "SimpleOutput")


def _parse_adv_output(ini_path: Path) -> dict[str, str]:
    return _parse_ini_section(ini_path, "AdvOut")


def _parse_output_mode(ini_path: Path) -> str:
    raw = (_parse_ini_section(ini_path, "Output").get("mode") or "Simple").strip()
    return "Advanced" if raw.casefold() == "advanced" else "Simple"


def _parse_adv_output_rec_path(ini_path: Path) -> str:
    """从 basic.ini [AdvOut] 读取高级输出模式的录像目录（RecFilePath）。"""
    return (_parse_adv_output(ini_path).get("recfilepath") or "").strip()


def _parse_track_mask(raw: object, default: int = 1) -> int:
    try:
        mask = int(str(raw).strip())
    except (TypeError, ValueError):
        mask = default
    return max(0, mask) & 0x3F


def _profile_recording_settings(ini_path: Optional[Path]) -> dict[str, Any]:
    """Read the active OBS output mode and the matching recording section."""
    base: dict[str, Any] = {
        "output_mode": "Simple",
        "use_stream_encoder": False,
        "encoder": "",
        "format": "",
        "rec_quality": "",
        "output_path": "",
        "audio_track_mask": 1,
        "audio_tracks": [1],
        "output_track1_enabled": True,
    }
    if ini_path is None or not ini_path.is_file():
        return base

    mode = _parse_output_mode(ini_path)
    simple = _parse_simple_output(ini_path)
    advanced = _parse_adv_output(ini_path)
    base["output_mode"] = mode

    if mode == "Advanced":
        rec_encoder = (advanced.get("recencoder") or "").strip()
        stream_encoder = (advanced.get("encoder") or "").strip()
        use_stream = rec_encoder.casefold() in {"", "none", "null", "stream"}
        mask = _parse_track_mask(advanced.get("rectracks"), 1)
        base.update(
            {
                "use_stream_encoder": use_stream,
                "encoder": stream_encoder if use_stream else rec_encoder,
                "format": (advanced.get("recformat2") or advanced.get("recformat") or "").strip(),
                "rec_quality": "Advanced",
                "output_path": (advanced.get("recfilepath") or "").strip(),
                "audio_track_mask": mask,
            }
        )
    else:
        mask = _parse_track_mask(simple.get("rectracks"), 1)
        base.update(
            {
                "use_stream_encoder": _detect_use_stream_encoder(simple, None),
                "encoder": (simple.get("recencoder") or simple.get("encoder") or "").strip(),
                "format": (simple.get("recformat2") or simple.get("recformat") or "").strip(),
                "rec_quality": (simple.get("recquality") or "").strip(),
                "output_path": _recording_output_path_from_simple(simple),
                "audio_track_mask": mask,
            }
        )

    mask = int(base["audio_track_mask"])
    base["audio_tracks"] = [track for track in range(1, 7) if mask & (1 << (track - 1))]
    base["output_track1_enabled"] = bool(mask & 1)
    return base


def _get_record_directory_via_ws(ws: obsws) -> str:
    """通过 OBS WebSocket GetRecordDirectory 获取录像目录，失败返回空串。"""
    try:
        req = getattr(obs_requests, "GetRecordDirectory", None)
        if req is None:
            return ""
        resp = ws.call(req())
        datain = getattr(resp, "datain", None) or {}
        raw = (
            datain.get("recordDirectory")
            or datain.get("record_directory")
            or datain.get("record-directory")
        )
        return str(raw).strip() if raw else ""
    except Exception:  # noqa: BLE001
        return ""


def _copy_profile_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def _safe_copy_file(src: Path, dst: Path, *, skip_if_identical: bool = False) -> bool:
    """复制文件，可选在目标与源字节完全一致时跳过（避免仅刷新修改时间）。返回是否执行了复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if skip_if_identical and dst.is_file() and src.is_file():
        try:
            if filecmp.cmp(src, dst, shallow=False):
                return False
        except OSError:
            pass
    shutil.copy2(src, dst)
    return True


def _ensure_project_profile_folder(obs_root: Path, project_profile: str) -> Path:
    profiles_root = obs_root / "basic" / "profiles"
    tgt = profiles_root / project_profile
    if tgt.is_dir():
        return tgt
    profiles_root.mkdir(parents=True, exist_ok=True)
    template = _profile_template_dir(profiles_root)
    if template is not None and template.is_dir():
        shutil.copytree(template, tgt)
        logger.info("Created OBS profile %s from template %s", project_profile, template.name)
    else:
        tgt.mkdir(parents=True, exist_ok=True)
        basic = tgt / "basic.ini"
        if not basic.is_file():
            basic.write_text("[General]\nName=" + project_profile + "\n", encoding="utf-8")
        logger.info("Created empty OBS profile directory: %s", tgt)
    return tgt


def _ws_connect(obs_cfg) -> obsws:
    ws = obsws(obs_cfg.host, obs_cfg.port, obs_cfg.password)
    ws.connect()
    return ws


def _ws_disconnect(ws: Optional[obsws]) -> None:
    if not ws:
        return
    try:
        ws.disconnect()
    except Exception:  # noqa: BLE001
        pass


def _obs_is_recording(ws: obsws) -> bool:
    try:
        req = getattr(obs_requests, "GetRecordStatus", None)
        if req is None:
            return False
        resp = ws.call(req())
        d = getattr(resp, "datain", {}) or {}
        active = d.get("outputActive")
        if active is None:
            active = getattr(resp, "outputActive", None)
        return bool(active)
    except Exception:  # noqa: BLE001
        return False


def _parse_ws_video(resp: object) -> dict[str, int]:
    d = getattr(resp, "datain", {}) or {}
    vs = d.get("videoSettings") or d.get("video_settings") or d
    if isinstance(vs, dict):
        bw = int(vs.get("baseWidth") or vs.get("base_width") or 0)
        bh = int(vs.get("baseHeight") or vs.get("base_height") or 0)
        ow = int(vs.get("outputWidth") or vs.get("output_width") or bw)
        oh = int(vs.get("outputHeight") or vs.get("output_height") or bh)
        fn = int(vs.get("fpsNumerator") or vs.get("fps_numerator") or 60)
        fd = int(vs.get("fpsDenominator") or vs.get("fps_denominator") or 1)
        return {"base_width": bw, "base_height": bh, "output_width": ow, "output_height": oh, "fps_num": fn, "fps_den": max(1, fd)}
    return {"base_width": 0, "base_height": 0, "output_width": 0, "output_height": 0, "fps_num": 60, "fps_den": 1}


def _fps_from_video_dict(v: dict[str, int]) -> int:
    fd = max(1, int(v.get("fps_den") or 1))
    fn = int(v.get("fps_num") or 60)
    return int(round(fn / fd)) if fn else 60


def _detect_use_stream_encoder(simple: dict[str, str], obs_ws: Optional[obsws]) -> bool:
    # ini 启发式
    for key in ("recusestreamencoder", "usestreamencoder", "rec_use_stream_encoder"):
        val = simple.get(key)
        if val is not None:
            return str(val).strip().lower() in ("1", "true", "yes")
    same = (simple.get("recencoder") or "").strip() and (simple.get("encoder") or "").strip()
    if same and simple.get("recencoder") == simple.get("encoder"):
        # 可能共用；保守视为警告来源之一，最终以 WS 为准
        pass
    if obs_ws is not None:
        for pname in ("RecUseStreamEncoder", "UseStreamEncoder"):
            try:
                req = getattr(obs_requests, "GetProfileParameter", None)
                if req is None:
                    break
                r = obs_ws.call(
                    req(parameterCategory="SimpleOutput", parameterName=pname),
                )
                d = getattr(r, "datain", {}) or {}
                raw = d.get("parameterValue")
                if raw is not None:
                    return str(raw).strip().lower() in ("1", "true", "yes")
            except Exception:  # noqa: BLE001
                continue
    return False


def _recording_output_path_from_simple(simple: dict[str, str]) -> str:
    return (simple.get("filepath") or simple.get("filepath2") or "").strip()


def _scene_item_transform(ws: obsws, scene_name: str, source_name: str) -> Optional[dict]:
    try:
        gtr = getattr(obs_requests, "GetSceneItemTransform", None)
        if gtr is None:
            return None
        il = ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
        items = getattr(il, "datain", {}).get("sceneItems") or []
        sid = None
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = it.get("sourceName") or it.get("sceneItemSourceName")
            if str(nm or "") == source_name:
                sid = it.get("sceneItemId")
                break
        if sid is None:
            return None
        tr = ws.call(gtr(sceneName=scene_name, sceneItemId=int(sid)))
        d = getattr(tr, "datain", {}) or {}
        return d.get("sceneItemTransform") or d
    except Exception as e:  # noqa: BLE001
        logger.debug("GetSceneItemTransform failed: %s", e)
        return None


def _source_fits_canvas(ws: obsws, scene_name: str, source_name: str, base_w: int, base_h: int) -> bool:
    t = _scene_item_transform(ws, scene_name, source_name)
    if not isinstance(t, dict):
        return False
    bt = str(t.get("boundsType") or "")
    # Bounds-based stretch/scale (set via calibrate or OBS bounds UI)
    if bt and bt != "OBS_BOUNDS_NONE":
        bw = int(float(t.get("boundsWidth") or 0))
        bh = int(float(t.get("boundsHeight") or 0))
        ok_dims = bw >= base_w - 4 and bh >= base_h - 4 and bw > 0 and bh > 0
        ok_type = "STRETCH" in bt.upper() or "SCALE_INNER" in bt.upper() or "SCALE_OUTER" in bt.upper()
        if ok_dims and ok_type:
            return True
    # Fallback: OBS "拉伸至全屏" (boundsType=NONE, fills via scale)
    px = float(t.get("positionX") or 0)
    py = float(t.get("positionY") or 0)
    if abs(px) <= 4 and abs(py) <= 4:
        w = int(float(t.get("width") or 0))
        h = int(float(t.get("height") or 0))
        if w >= base_w - 4 and h >= base_h - 4 and w > 0 and h > 0:
            return True
        # scaleX * sourceWidth in case width is pre-scale
        sx = float(t.get("scaleX") or 0)
        sy = float(t.get("scaleY") or 0)
        sw = int(float(t.get("sourceWidth") or 0))
        sh = int(float(t.get("sourceHeight") or 0))
        if sx > 0 and sy > 0 and sw > 0 and sh > 0:
            if int(sx * sw) >= base_w - 8 and int(sy * sh) >= base_h - 8:
                return True
    return False


def _get_input_settings(ws: obsws, input_name: str) -> Optional[dict[str, Any]]:
    try:
        req = getattr(obs_requests, "GetInputSettings", None)
        if req is None:
            return None
        response = ws.call(req(inputName=input_name))
        settings = (getattr(response, "datain", None) or {}).get("inputSettings")
        return dict(settings) if isinstance(settings, dict) else None
    except Exception:  # noqa: BLE001
        logger.debug("GetInputSettings failed for %r", input_name, exc_info=True)
        return None


def _get_input_mute(ws: obsws, input_name: str) -> Optional[bool]:
    try:
        req = getattr(obs_requests, "GetInputMute", None)
        if req is None:
            return None
        response = ws.call(req(inputName=input_name))
        raw = (getattr(response, "datain", None) or {}).get("inputMuted")
        return _as_optional_bool(raw)
    except Exception:  # noqa: BLE001
        logger.debug("GetInputMute failed for %r", input_name, exc_info=True)
        return None


def _get_input_audio_tracks(ws: obsws, input_name: str) -> Optional[dict[str, bool]]:
    try:
        req = getattr(obs_requests, "GetInputAudioTracks", None)
        if req is None:
            return None
        response = ws.call(req(inputName=input_name))
        raw = (getattr(response, "datain", None) or {}).get("inputAudioTracks")
        if not isinstance(raw, dict):
            return None
        return {
            str(key): _as_optional_bool(value) is True
            for key, value in raw.items()
        }
    except Exception:  # noqa: BLE001
        logger.debug("GetInputAudioTracks failed for %r", input_name, exc_info=True)
        return None


def _get_special_input_names(ws: obsws) -> Optional[set[str]]:
    """Return global Desktop Audio / Mic-Aux input names configured by OBS."""
    try:
        req = getattr(obs_requests, "GetSpecialInputs", None)
        if req is None:
            return None
        response = ws.call(req())
        raw = getattr(response, "datain", None)
        if not isinstance(raw, dict):
            return None
        names: set[str] = set()
        for key, value in raw.items():
            normalized_key = str(key).casefold()
            if not (
                normalized_key.startswith("desktop")
                or normalized_key.startswith("mic")
            ):
                continue
            name = str(value or "").strip()
            if name:
                names.add(name)
        return names
    except Exception:  # noqa: BLE001
        logger.debug("GetSpecialInputs failed", exc_info=True)
        return None


def _get_scene_source_names(ws: obsws, scene_name: str) -> Optional[set[str]]:
    try:
        response = ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
        items = (getattr(response, "datain", None) or {}).get("sceneItems")
        if not isinstance(items, list):
            return None
        return {
            str(item.get("sourceName") or item.get("sceneItemSourceName") or "").strip()
            for item in items
            if isinstance(item, dict)
            and str(item.get("sourceName") or item.get("sceneItemSourceName") or "").strip()
        }
    except Exception:  # noqa: BLE001
        logger.debug("GetSceneItemList failed for %r", scene_name, exc_info=True)
        return None


def _track1_conflict_health(
    ws: obsws,
    managed_input_name: str,
    *,
    scene_name: Optional[str] = None,
) -> dict[str, Any]:
    """Verify no other source can mix into Track 1 during managed recording.

    Scene inputs are considered active when the dedicated scene is used. A
    configured, unmuted OBS special input (Desktop Audio / Mic-Aux) is global and
    therefore conservatively counts as active. ``GetInputActiveState`` exposes a
    video-oriented flag whose false value is not reliable proof that an audio-only
    source cannot mix later. The check is intentionally read-only: user-owned
    global inputs are reported, never muted or rerouted behind the user's back.
    """
    special_names = _get_special_input_names(ws)
    scene_names = _get_scene_source_names(ws, scene_name) if scene_name else set()
    scan_available = special_names is not None and scene_names is not None
    if not scan_available:
        return {
            "track1_conflict_scan_available": False,
            "track1_conflicts": [],
            "track1_conflict_names": [],
            "track1_unverified_inputs": [],
            "track1_isolated": False,
        }

    managed_key = managed_input_name.casefold()
    special_names = special_names or set()
    scene_names = scene_names or set()
    candidates = sorted(
        special_names | scene_names,
        key=lambda name: name.casefold(),
    )
    conflicts: list[dict[str, Any]] = []
    unverified: list[str] = []
    for candidate in candidates:
        if candidate.casefold() == managed_key:
            continue
        settings = _get_input_settings(ws, candidate)
        # OBS exposes track toggles even for browser sources whose audio is not
        # routed through the mixer.  Our keyboard overlay explicitly uses
        # reroute_audio=false, so treating its default Track 1 flag as audible
        # creates a permanent false-positive recording block.
        if (
            settings is not None
            and "reroute_audio" in settings
            and _as_optional_bool(settings.get("reroute_audio")) is False
        ):
            continue
        tracks = _get_input_audio_tracks(ws, candidate)
        if tracks is None:
            unverified.append(candidate)
            continue
        if tracks.get("1") is not True:
            continue
        muted = _get_input_mute(ws, candidate)
        if muted is True:
            continue
        if muted is None:
            unverified.append(candidate)
            continue

        scopes: list[str] = []
        active = False
        if candidate in scene_names:
            scopes.append("dedicated_scene")
            active = True
        if candidate in special_names:
            scopes.append("global_special_input")
            active = True
        if not active:
            continue
        conflicts.append(
            {
                "input_name": candidate,
                "scopes": scopes,
                "muted": False,
                "track1_enabled": True,
            }
        )

    unverified = sorted(set(unverified), key=str.casefold)
    return {
        "track1_conflict_scan_available": not unverified,
        "track1_conflicts": conflicts,
        "track1_conflict_names": [row["input_name"] for row in conflicts],
        "track1_unverified_inputs": unverified,
        "track1_isolated": not conflicts and not unverified,
    }


def _dedicated_audio_health(
    ws: obsws,
    input_name: str,
    *,
    scene_name: Optional[str] = None,
) -> dict[str, Any]:
    settings = _get_input_settings(ws, input_name)
    muted = _get_input_mute(ws, input_name)
    tracks = _get_input_audio_tracks(ws, input_name)
    capture_audio = (
        _as_optional_bool(settings.get("capture_audio", False))
        if settings is not None
        else None
    )
    track1 = tracks.get("1") if tracks is not None else None
    enabled_tracks = sorted(
        int(key)
        for key, enabled in (tracks or {}).items()
        if enabled and str(key).isdigit() and 1 <= int(key) <= 6
    )
    extra_tracks = [track for track in enabled_tracks if track != 1]
    exclusive_track1 = tracks is not None and enabled_tracks == [1]
    track1_health = _track1_conflict_health(
        ws,
        input_name,
        scene_name=scene_name,
    )
    return {
        "capture_audio_enabled": capture_audio,
        "capture_muted": muted,
        "track1_enabled": track1,
        "enabled_tracks": enabled_tracks,
        "extra_tracks": extra_tracks,
        "exclusive_track1": exclusive_track1,
        "duplicate_track_risk": bool(
            extra_tracks
            or track1_health["track1_conflicts"]
            or track1_health["track1_unverified_inputs"]
            or not track1_health["track1_conflict_scan_available"]
        ),
        **track1_health,
        "ready": (
            capture_audio is True
            and muted is False
            and track1 is True
            and exclusive_track1
            and track1_health["track1_isolated"] is True
        ),
    }


def _ensure_exclusive_input_audio_track(
    ws: obsws,
    input_name: str,
    track_number: int = 1,
) -> bool:
    """Route one managed input to exactly one OBS track, with read-back.

    This is intentionally only used for the CS2 Insight-owned capture source.
    It never changes Desktop Audio, microphones, or any user-owned input.
    """
    track = int(track_number)
    if track < 1 or track > 6:
        raise ValueError("OBS 音轨编号必须在 1 到 6 之间")
    current = _get_input_audio_tracks(ws, input_name)
    if current is None:
        raise ValueError(f"无法读取「{input_name}」的 OBS 音轨路由")
    desired = {str(number): number == track for number in range(1, 7)}
    current_normalized = {
        str(number): current.get(str(number)) is True
        for number in range(1, 7)
    }
    if current_normalized == desired:
        return False
    req = getattr(obs_requests, "SetInputAudioTracks", None)
    if req is None:
        raise ValueError("当前 OBS WebSocket 不支持设置输入音轨")
    ws.call(req(inputName=input_name, inputAudioTracks=desired))
    verified = _get_input_audio_tracks(ws, input_name)
    verified_normalized = {
        str(number): bool(verified and verified.get(str(number)) is True)
        for number in range(1, 7)
    }
    if verified_normalized != desired:
        raise ValueError(
            f"OBS 未接受「{input_name}」仅路由到音轨 {track} 的修改"
        )
    return True


def _create_backup(
    obs_root: Path,
    *,
    reason: str,
    project_profile: str,
) -> tuple[str, Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"{ts}_{reason}"
    dest = _backup_root() / backup_id
    dest.mkdir(parents=True, exist_ok=True)
    prof_name, sc_name = _read_global_profile_names(obs_root)
    manifest = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
        "obs_config_dir": str(obs_root),
        "active_profile": prof_name,
        "active_scene_collection": sc_name,
        "project_profile": project_profile,
        "app_version": APP_VERSION,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    g = obs_root / "global.ini"
    if g.is_file():
        _safe_copy_file(g, dest / "global.ini")
    pp = obs_root / "basic" / "profiles" / project_profile
    if pp.is_dir():
        _copy_profile_tree(pp, dest / "profiles" / project_profile)
    return backup_id, dest


def _restore_backup_pack(backup_dir: Path, obs_root: Path, project_profile: str) -> bool:
    g_bak = backup_dir / "global.ini"
    if g_bak.is_file():
        _safe_copy_file(g_bak, obs_root / "global.ini")
    prof_bak = backup_dir / "profiles" / project_profile
    if prof_bak.is_dir():
        tgt = obs_root / "basic" / "profiles" / project_profile
        _copy_profile_tree(prof_bak, tgt)
    return True


def list_backups() -> list[dict[str, Any]]:
    root = _backup_root()
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        mf = child / "manifest.json"
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items.append(
            {
                "id": child.name,
                "created_at": str(data.get("created_at") or ""),
                "reason": str(data.get("reason") or ""),
                "active_profile": data.get("active_profile"),
            }
        )
    return items


def _latest_backup_summary() -> Optional[dict[str, str]]:
    rows = list_backups()
    if not rows:
        return None
    top = rows[0]
    return {"id": top["id"], "created_at": top.get("created_at") or ""}


def get_status_payload(obs_cfg) -> dict[str, Any]:
    obs_root = _obs_studio_root()
    connection = get_connection_readiness(obs_cfg, obs_root=obs_root)
    prof_name, sc_name = (None, None)
    if obs_root:
        prof_name, sc_name = _read_global_profile_names(obs_root)

    latest = _latest_backup_summary()
    from .env_utils import get_primary_monitor_resolution
    _mon_w, _mon_h = get_primary_monitor_resolution()
    base: dict[str, Any] = {
        "ok": True,
        "obs_connected": False,
        "obs_config_dir": str(obs_root) if obs_root else None,
        "active_profile": prof_name,
        "active_scene_collection": sc_name,
        "video": {
            "base_width": 0,
            "base_height": 0,
            "output_width": 0,
            "output_height": 0,
            "fps": 0,
        },
        "recording": {
            "output_mode": "Simple",
            "use_stream_encoder": False,
            "encoder": "",
            "format": "",
            "rec_quality": "",
            "output_path": "",
            "audio_track_mask": 1,
            "audio_tracks": [1],
            "output_track1_enabled": True,
        },
        "scene": {
            "dedicated_scene_exists": False,
            "capture_source_exists": False,
            "source_fit_to_canvas": False,
        },
        "audio": {
            "capture_audio_enabled": None,
            "capture_muted": None,
            "track1_enabled": None,
            "enabled_tracks": [],
            "extra_tracks": [],
            "exclusive_track1": False,
            "duplicate_track_risk": False,
            "track1_conflict_scan_available": False,
            "track1_conflicts": [],
            "track1_conflict_names": [],
            "track1_unverified_inputs": [],
            "track1_isolated": False,
            "output_track1_enabled": True,
            "ready": False,
        },
        "monitor": {"width": _mon_w, "height": _mon_h},
        "latest_backup": latest,
        "obs_version": None,
        "connection": connection,
    }

    if sys.platform != "win32":
        base["ok"] = True
        base["message"] = "OBS 配置文件操作建议在 Windows 上进行；仍可尝试连接 OBS WebSocket 查看画面状态。"

    # 文件级检查不依赖 WebSocket；连接失败时仍能展示输出目录、格式与编码器线索。
    profile_ini: Optional[Path] = None
    if obs_root and prof_name:
        profile_ini = obs_root / "basic" / "profiles" / prof_name / "basic.ini"
    profile_recording = _profile_recording_settings(profile_ini)
    base["recording"].update(profile_recording)
    base["audio"]["output_track1_enabled"] = bool(
        profile_recording.get("output_track1_enabled", True)
    )
    static_blockers = {
        "OBS_WEBSOCKET_DISABLED",
        "OBS_PORT_MISMATCH",
        "OBS_PASSWORD_REQUIRED",
    }.intersection(connection.get("blockers", []))
    if static_blockers:
        return base
    ws: Optional[obsws] = None
    try:
        ws = _ws_connect(obs_cfg)
    except Exception as e:  # noqa: BLE001
        logger.info("OBS WebSocket status handshake failed: %s", e)
        return base

    base["obs_connected"] = True
    base["connection"]["connected"] = True
    base["connection"]["port_open"] = True
    base["connection"]["blockers"] = [
        code
        for code in base["connection"].get("blockers", [])
        if code != "OBS_PORT_NOT_LISTENING"
    ]
    try:
        try:
            ver = ws.call(obs_requests.GetVersion())
            ov = None
            if ver is not None:
                go = getattr(ver, "getObsVersion", None)
                ov = go() if callable(go) else None
            if ov is None and ver is not None:
                din = getattr(ver, "datain", {}) or {}
                ov = din.get("obsVersion") or din.get("obs_version") or din.get("obs-version")
            base["obs_version"] = str(ov).strip() if ov else None
        except Exception:  # noqa: BLE001
            base["obs_version"] = None

        vr = ws.call(obs_requests.GetVideoSettings())
        vd = _parse_ws_video(vr)
        base["video"] = {
            "base_width": vd["base_width"],
            "base_height": vd["base_height"],
            "output_width": vd["output_width"],
            "output_height": vd["output_height"],
            "fps": _fps_from_video_dict(vd),
        }
        scene_name = _dedicated_scene_name()
        cap_name = _dedicated_capture_name()
        try:
            sl = ws.call(obs_requests.GetSceneList())
            scenes = getattr(sl, "datain", {}).get("scenes") or []
            base["scene"]["dedicated_scene_exists"] = any(
                isinstance(s, dict) and str(s.get("sceneName") or "") == scene_name for s in scenes
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            il = ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
            items = getattr(il, "datain", {}).get("sceneItems") or []
            base["scene"]["capture_source_exists"] = any(
                isinstance(it, dict) and str(it.get("sourceName") or it.get("sceneItemSourceName") or "") == cap_name
                for it in items
            )
        except Exception:  # noqa: BLE001
            pass
        if base["video"]["base_width"] and base["scene"]["capture_source_exists"]:
            base["scene"]["source_fit_to_canvas"] = _source_fits_canvas(
                ws,
                scene_name,
                cap_name,
                int(base["video"]["base_width"]),
                int(base["video"]["base_height"]),
            )
        if base["scene"]["capture_source_exists"]:
            audio_health = _dedicated_audio_health(
                ws,
                cap_name,
                scene_name=scene_name,
            )
            audio_health["output_track1_enabled"] = bool(
                base["recording"].get("output_track1_enabled", True)
            )
            audio_health["ready"] = bool(
                audio_health.get("ready")
                and audio_health["output_track1_enabled"]
            )
            base["audio"] = audio_health

        if base["recording"]["output_mode"] == "Simple":
            base["recording"]["use_stream_encoder"] = _detect_use_stream_encoder(
                _parse_simple_output(profile_ini) if profile_ini else {},
                ws,
            )
        base["recording"]["output_path"] = (
            base["recording"].get("output_path") or _get_record_directory_via_ws(ws)
        )

        # Query the section OBS is actually using. Advanced Output has no
        # SimpleOutput RecQuality, so do not overwrite it with an unrelated value.
        profile_category = (
            "AdvOut" if base["recording"]["output_mode"] == "Advanced" else "SimpleOutput"
        )
        try:
            fmt_resp = ws.call(obs_requests.GetProfileParameter(
                parameterCategory=profile_category, parameterName="RecFormat2"
            ))
            fmt_val = (getattr(fmt_resp, "datain", None) or {}).get("parameterValue", "")
            if fmt_val:
                base["recording"]["format"] = fmt_val
        except Exception:  # noqa: BLE001
            pass
        if profile_category == "SimpleOutput":
            try:
                q_resp = ws.call(obs_requests.GetProfileParameter(
                    parameterCategory="SimpleOutput", parameterName="RecQuality"
                ))
                q_val = (getattr(q_resp, "datain", None) or {}).get("parameterValue", "")
                if q_val:
                    base["recording"]["rec_quality"] = q_val
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        logger.warning("OBS WebSocket status query failed after connect: %s", e)
    finally:
        _ws_disconnect(ws)

    return base


def diagnose(obs_cfg) -> dict[str, Any]:
    """基于 OBS WebSocket +（Windows）Profile 目录的真实诊断；返回结果含连接状态与时间戳供前端展示。"""
    issues: list[dict[str, Any]] = []
    obs_root = _obs_studio_root()

    ws: Optional[obsws] = None
    obs_connected = False
    obs_version: Optional[str] = None
    video_dims: dict[str, int] = {"base_width": 0, "base_height": 0, "output_width": 0, "output_height": 0, "fps_num": 60, "fps_den": 1}
    prof_name: Optional[str] = None
    disk_profile_checked = False
    profile_recording = _profile_recording_settings(None)
    audio_health: dict[str, Any] = {
        "capture_audio_enabled": None,
        "capture_muted": None,
        "track1_enabled": None,
        "enabled_tracks": [],
        "extra_tracks": [],
        "exclusive_track1": False,
        "duplicate_track_risk": False,
        "track1_conflict_scan_available": False,
        "track1_conflicts": [],
        "track1_conflict_names": [],
        "track1_unverified_inputs": [],
        "track1_isolated": False,
        "output_track1_enabled": True,
        "ready": False,
    }
    connection = get_connection_readiness(obs_cfg, obs_root=obs_root)

    connection_issue_specs = {
        "OBS_WEBSOCKET_CONFIG_NOT_FOUND": (
            "warning",
            "未找到 OBS WebSocket 配置",
            "请先启动一次 OBS，并在「工具 → WebSocket 服务器设置」中确认配置。",
        ),
        "OBS_WEBSOCKET_DISABLED": (
            "error",
            "OBS WebSocket 服务器未启用",
            "在 OBS 中打开「工具 → WebSocket 服务器设置」，勾选“启用 WebSocket 服务器”。",
        ),
        "OBS_PORT_MISMATCH": (
            "error",
            "OBS 与应用端口不一致",
            f"OBS 使用 {connection.get('server_port')}，应用配置为 {connection.get('configured_port')}；请改为相同端口。",
        ),
        "OBS_PASSWORD_REQUIRED": (
            "error",
            "OBS 需要密码，但应用尚未填写",
            "OBS 已启用身份验证。请把 OBS WebSocket 设置中的密码填入本页连接配置。",
        ),
        "OBS_PORT_NOT_LISTENING": (
            "error",
            "OBS WebSocket 端口未监听",
            f"当前无法访问 {connection.get('host')}:{connection.get('configured_port')}。请确认 OBS 已启动且 WebSocket 服务器已启用。",
        ),
    }
    for code in connection.get("blockers", []):
        spec = connection_issue_specs.get(code)
        if spec is None:
            continue
        level_name, title, message = spec
        issues.append(
            {
                "code": code,
                "level": level_name,
                "title": title,
                "message": message,
                "fixable": False,
            }
        )

    # Profile 文件可以离线读取；即使 WebSocket 完全连不上，也继续给出录制配置诊断。
    if obs_root is not None and obs_root.is_dir():
        prof_name, _ = _read_global_profile_names(obs_root)
        if prof_name:
            profile_ini = obs_root / "basic" / "profiles" / prof_name / "basic.ini"
            if profile_ini.is_file():
                profile_recording = _profile_recording_settings(profile_ini)
                audio_health["output_track1_enabled"] = bool(
                    profile_recording.get("output_track1_enabled", True)
                )
                disk_profile_checked = True
                if profile_recording["use_stream_encoder"]:
                    issues.append(
                        {
                            "code": "USE_STREAM_ENCODER",
                            "level": "error",
                            "title": "录制正在使用与串流一致",
                            "message": "当前录制配置依赖串流编码器，可能导致录制失败。建议应用推荐预设。",
                            "fixable": True,
                        }
                    )
                if not profile_recording["output_path"]:
                    issues.append(
                        {
                            "code": "NO_OUTPUT_PATH",
                            "level": "warning",
                            "title": "未设置录制输出目录",
                            "message": "请在 OBS 设置中为录制指定输出路径。",
                            "fixable": False,
                        }
                    )
                if not profile_recording["encoder"] and not profile_recording["use_stream_encoder"]:
                    issues.append(
                        {
                            "code": "ENCODER_UNAVAILABLE",
                            "level": "warning",
                            "title": "录制编码器未配置或为空",
                            "message": "建议应用推荐预设或手动选择可用编码器。",
                            "fixable": True,
                        }
                    )
                if not profile_recording["output_track1_enabled"]:
                    issues.append(
                        {
                            "code": "OUTPUT_AUDIO_TRACK1_DISABLED",
                            "level": "error",
                            "title": "录像输出未包含音轨 1",
                            "message": (
                                f"当前 {profile_recording['output_mode']} 输出未录制音轨 1；"
                                "即使 CS2 音频源正常，也不会写入成片。"
                            ),
                            "fixable": True,
                        }
                    )

    try:
        static_blockers = {
            "OBS_WEBSOCKET_DISABLED",
            "OBS_PORT_MISMATCH",
            "OBS_PASSWORD_REQUIRED",
        }.intersection(connection.get("blockers", []))
        if static_blockers:
            raise ConnectionError("OBS connection readiness check failed")
        ws = _ws_connect(obs_cfg)
        obs_connected = True
        connection["connected"] = True
        connection["port_open"] = True
        connection["blockers"] = []
        issues[:] = [issue for issue in issues if issue.get("code") not in connection_issue_specs]

        try:
            ver = ws.call(obs_requests.GetVersion())
            ov = None
            if ver is not None:
                go = getattr(ver, "getObsVersion", None)
                ov = go() if callable(go) else None
            if ov is None and ver is not None:
                din = getattr(ver, "datain", {}) or {}
                ov = din.get("obsVersion") or din.get("obs_version") or din.get("obs-version")
            obs_version = str(ov).strip() if ov else None
        except Exception:  # noqa: BLE001
            obs_version = None

        if _obs_is_recording(ws):
            issues.append(
                {
                    "code": "OBS_RECORDING_ACTIVE",
                    "level": "error",
                    "title": "OBS 正在录制",
                    "message": "录制进行中时无法安全修改配置，请先停止录制。",
                    "fixable": False,
                }
            )
        from .env_utils import get_primary_monitor_resolution

        monitor_w, monitor_h = get_primary_monitor_resolution()
        vr = ws.call(obs_requests.GetVideoSettings())
        video_dims = _parse_ws_video(vr)
        fps = _fps_from_video_dict(video_dims)
        bw, bh = video_dims["base_width"], video_dims["base_height"]
        ow, oh = video_dims["output_width"], video_dims["output_height"]
        if bw and bh and (bw != monitor_w or bh != monitor_h):
            issues.append(
                {
                    "code": "CANVAS_RESOLUTION_MISMATCH",
                    "level": "warning",
                    "title": "画布分辨率与主显示器不一致",
                    "message": f"当前 {bw}×{bh}，应为 {monitor_w}×{monitor_h}。",
                    "fixable": True,
                }
            )
        if ow and oh and (ow != monitor_w or oh != monitor_h):
            issues.append(
                {
                    "code": "OUTPUT_RESOLUTION_MISMATCH",
                    "level": "warning",
                    "title": "输出分辨率与主显示器不一致",
                    "message": f"当前 {ow}×{oh}，应为 {monitor_w}×{monitor_h}。",
                    "fixable": True,
                }
            )
        if bw and bh and ow and oh and (bw != ow or bh != oh):
            issues.append(
                {
                    "code": "VIDEO_SCALE_MISMATCH",
                    "level": "warning",
                    "title": "基础画布与输出分辨率不一致",
                    "message": "可能导致画面缩放异常或黑边。",
                    "fixable": True,
                }
            )
        if fps < 60:
            issues.append(
                {
                    "code": "FPS_TOO_LOW",
                    "level": "warning",
                    "title": "FPS 低于 60",
                    "message": "录制流畅度可能不足，推荐 60 FPS。",
                    "fixable": True,
                }
            )

        scene_name = _dedicated_scene_name()
        cap_name = _dedicated_capture_name()
        scene_ok = False
        cap_ok = False
        try:
            sl = ws.call(obs_requests.GetSceneList())
            scenes = getattr(sl, "datain", {}).get("scenes") or []
            scene_ok = any(isinstance(s, dict) and str(s.get("sceneName") or "") == scene_name for s in scenes)
        except Exception:  # noqa: BLE001
            pass
        if not scene_ok:
            issues.append(
                {
                    "code": "DEDICATED_SCENE_MISSING",
                    "level": "warning",
                    "title": "专属录制场景不存在",
                    "message": f"未找到场景「{scene_name}」，录制流程可能无法切换画面。",
                    "fixable": True,
                }
            )
        try:
            il = ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
            items = getattr(il, "datain", {}).get("sceneItems") or []
            cap_ok = any(
                isinstance(it, dict) and str(it.get("sourceName") or it.get("sceneItemSourceName") or "") == cap_name for it in items
            )
        except Exception:  # noqa: BLE001
            pass
        if not cap_ok:
            issues.append(
                {
                    "code": "CAPTURE_SOURCE_MISSING",
                    "level": "error",
                    "title": "CS2 捕获源缺失",
                    "message": f"场景「{scene_name}」中未找到「{cap_name}」。",
                    "fixable": True,
                }
            )
        elif bw and bh and not _source_fits_canvas(ws, scene_name, cap_name, bw, bh):
            issues.append(
                {
                    "code": "SOURCE_NOT_FIT_CANVAS",
                    "level": "warning",
                    "title": "CS2 捕获源未铺满画布",
                    "message": "可能出现黑边或未拉伸。应用推荐预设可修复变换。",
                    "fixable": True,
                }
            )
        if cap_ok:
            audio_health.update(
                _dedicated_audio_health(
                    ws,
                    cap_name,
                    scene_name=scene_name,
                )
            )
            audio_health["output_track1_enabled"] = bool(
                profile_recording.get("output_track1_enabled", True)
            )
            audio_health["ready"] = bool(
                audio_health.get("ready")
                and audio_health["output_track1_enabled"]
            )
            if audio_health["capture_audio_enabled"] is False:
                issues.append(
                    {
                        "code": "CAPTURE_AUDIO_DISABLED",
                        "level": "error",
                        "title": "CS2 游戏音频捕获未启用",
                        "message": f"「{cap_name}」只捕获画面，当前不会采集 CS2 声音。",
                        "fixable": True,
                    }
                )
            if audio_health["capture_muted"] is True:
                issues.append(
                    {
                        "code": "CAPTURE_AUDIO_MUTED",
                        "level": "error",
                        "title": "CS2 游戏音频源已静音",
                        "message": f"「{cap_name}」在 OBS 混音器中处于静音状态。",
                        "fixable": True,
                    }
                )
            if audio_health["track1_enabled"] is False:
                issues.append(
                    {
                        "code": "CAPTURE_AUDIO_TRACK1_DISABLED",
                        "level": "error",
                        "title": "CS2 游戏音频未路由到音轨 1",
                        "message": f"「{cap_name}」未发送到应用默认使用的 OBS 音轨 1。",
                        "fixable": True,
                    }
                )
            if audio_health["duplicate_track_risk"]:
                extra_tracks = ", ".join(
                    str(track) for track in audio_health.get("extra_tracks", [])
                )
                if extra_tracks:
                    issues.append(
                        {
                            "code": "CAPTURE_AUDIO_EXTRA_TRACK_ROUTES",
                            "level": "warning",
                            "title": "CS2 游戏音频被发送到额外音轨",
                            "message": (
                                f"「{cap_name}」除音轨 1 外还发送到音轨 {extra_tracks}。"
                                "这些音轨若再混入桌面音频，导出时可能出现重复叠加、削波或刺耳失真。"
                            ),
                            "fixable": True,
                        }
                    )
            if audio_health["track1_conflict_names"]:
                conflict_names = "、".join(audio_health["track1_conflict_names"])
                issues.append(
                    {
                        "code": "AUDIO_TRACK1_CONFLICTING_INPUTS",
                        "level": "error",
                        "title": "音轨 1 同时混入其他音频源",
                        "message": (
                            f"以下未静音音频源也在专属录制时发送到音轨 1：{conflict_names}。"
                            "这会与 CS2 专用捕获重复混音。请在 OBS 高级音频属性中将这些源移出音轨 1，"
                            "本应用不会自动修改用户的全局音频输入。"
                        ),
                        "fixable": False,
                    }
                )
            if not audio_health["track1_conflict_scan_available"]:
                unverified_names = "、".join(
                    audio_health.get("track1_unverified_inputs", [])
                )
                issues.append(
                    {
                        "code": "AUDIO_TRACK1_ISOLATION_UNVERIFIED",
                        "level": "error",
                        "title": "无法确认音轨 1 是否只有 CS2 声音",
                        "message": (
                            f"无法验证这些音频源：{unverified_names}。"
                            if unverified_names
                            else "OBS 未返回完整的全局/场景音频路由状态。"
                        ),
                        "fixable": False,
                    }
                )
            if any(
                audio_health[key] is None
                for key in ("capture_audio_enabled", "capture_muted", "track1_enabled")
            ):
                issues.append(
                    {
                        "code": "CAPTURE_AUDIO_STATUS_UNAVAILABLE",
                        "level": "warning",
                        "title": "无法确认 CS2 游戏音频状态",
                        "message": "OBS 未返回完整的音频开关、静音或音轨路由信息。",
                        "fixable": False,
                    }
                )

    except Exception:  # noqa: BLE001
        obs_connected = False
        obs_version = None
        issues.append(
            {
                "code": "OBS_NOT_CONNECTED",
                "level": "error",
                "title": "无法连接 OBS WebSocket",
                "message": (
                    "请先按上方连接诊断逐项处理，然后重新测试连接。"
                    if connection.get("blockers")
                    else f"无法完成 WebSocket 握手，请检查地址、端口和密码（{connection.get('host')}:{connection.get('configured_port')}）。"
                ),
                "fixable": False,
            }
        )
    finally:
        _ws_disconnect(ws)

    if not obs_root and sys.platform != "win32":
        issues.append(
            {
                "code": "UNKNOWN_PROFILE",
                "level": "warning",
                "title": "未检测到本机 OBS 配置目录",
                "message": "当前平台非 Windows，文件级诊断与导入功能不可用。",
                "fixable": False,
            }
        )

    level = "normal"
    if any(i["level"] == "error" for i in issues):
        level = "error"
    elif any(i["level"] == "warning" for i in issues):
        level = "warning"

    return {
        "ok": True,
        "level": level,
        "issues": issues,
        "obs_connected": obs_connected,
        "obs_version": obs_version,
        "active_profile_name": prof_name,
        "disk_profile_checked": disk_profile_checked,
        "recording": profile_recording,
        "audio": audio_health,
        "connection": connection,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def calibrate(obs_cfg) -> dict[str, Any]:
    """运行时校准：读显示器分辨率 → 修正 OBS 画布 → 建场景 → 建 Game Capture → 设拉伸 → 修输出格式。
    仅操作 CS2 Insight 专用场景，不动用户其他场景。
    """
    from .env_utils import get_primary_monitor_resolution

    ws = None
    try:
        ws = _ws_connect(obs_cfg)
    except Exception as exc:
        raise ValueError(f"OBS WebSocket 未连接，请先在设置中测试连接：{exc}") from exc

    try:
        if _obs_is_recording(ws):
            raise ValueError("录制进行中，无法修改视频设置，请录制结束后再校准")

        changed: list[str] = []
        already_ok: list[str] = []
        obs_root = _obs_studio_root()
        active_profile, _ = _read_global_profile_names(obs_root) if obs_root else (None, None)
        profile_ini = (
            obs_root / "basic" / "profiles" / active_profile / "basic.ini"
            if obs_root and active_profile
            else None
        )
        profile_recording = _profile_recording_settings(profile_ini)

        # Step 1+2: 读显示器分辨率 & OBS 画布
        monitor_w, monitor_h = get_primary_monitor_resolution()

        vr = ws.call(obs_requests.GetVideoSettings())
        vd = _parse_ws_video(vr)
        canvas_w = vd.get("base_width", 0)
        canvas_h = vd.get("base_height", 0)
        output_w = vd.get("output_width", 0)
        output_h = vd.get("output_height", 0)
        existing_fps_n = vd.get("fps_num", 60)
        existing_fps_d = vd.get("fps_den", 1)

        # Step 3: 修正画布与输出分辨率
        # OBS WS v5 SetVideoSettings 字段是顶层 kwargs，不能嵌套在 videoSettings={}
        canvas_needs_fix = canvas_w != monitor_w or canvas_h != monitor_h
        output_needs_fix = output_w != monitor_w or output_h != monitor_h
        if canvas_needs_fix or output_needs_fix:
            ws.call(obs_requests.SetVideoSettings(
                fpsNumerator=existing_fps_n,
                fpsDenominator=existing_fps_d,
                baseWidth=monitor_w,
                baseHeight=monitor_h,
                outputWidth=monitor_w,
                outputHeight=monitor_h,
            ))
            # 回读验证：避免静默失败时错误报告成功
            verify_vr = ws.call(obs_requests.GetVideoSettings())
            verify_vd = _parse_ws_video(verify_vr)
            canvas_ok = verify_vd["base_width"] == monitor_w and verify_vd["base_height"] == monitor_h
            output_ok = verify_vd["output_width"] == monitor_w and verify_vd["output_height"] == monitor_h
            if canvas_ok and output_ok:
                if canvas_needs_fix:
                    changed.append(f"已将画布分辨率从 {canvas_w}×{canvas_h} 修正为 {monitor_w}×{monitor_h}")
                if output_needs_fix:
                    changed.append(f"已将输出分辨率从 {output_w}×{output_h} 修正为 {monitor_w}×{monitor_h}")
            else:
                parts: list[str] = []
                if not canvas_ok:
                    parts.append(
                        f"画布仍为 {verify_vd['base_width']}×{verify_vd['base_height']}（应为 {monitor_w}×{monitor_h}）"
                    )
                if not output_ok:
                    parts.append(
                        f"输出仍为 {verify_vd['output_width']}×{verify_vd['output_height']}（应为 {monitor_w}×{monitor_h}）"
                    )
                raise ValueError(
                    "OBS 未接受分辨率修改（" + "；".join(parts) + "），"
                    f"请在 OBS 设置→视频中手动将基础（画布）与输出（缩放）分辨率改为 {monitor_w}×{monitor_h}"
                )
        else:
            already_ok.append(f"画布分辨率正确（{canvas_w}×{canvas_h}）")
            already_ok.append(f"输出分辨率正确（{output_w}×{output_h}）")

        # Step 4: 确保 CS2 Insight 场景存在
        scene_name = _dedicated_scene_name()
        scenes_resp = ws.call(obs_requests.GetSceneList())
        scenes_data = getattr(scenes_resp, "datain", None) or {}
        scene_names = [s.get("sceneName", "") for s in scenes_data.get("scenes", [])]

        if scene_name not in scene_names:
            ws.call(obs_requests.CreateScene(sceneName=scene_name))
            changed.append(f"已创建场景「{scene_name}」")
        else:
            already_ok.append(f"场景「{scene_name}」已存在")

        # Step 5: 确保 Game Capture 源存在
        capture_name = _dedicated_capture_name()
        items_resp = ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
        items_data = getattr(items_resp, "datain", None) or {}
        source_names = [item.get("sourceName", "") for item in items_data.get("sceneItems", [])]

        if capture_name not in source_names:
            ws.call(obs_requests.CreateInput(
                sceneName=scene_name,
                inputName=capture_name,
                inputKind="game_capture",
                inputSettings={
                    "capture_mode": "window",
                    "window": "cs2.exe",
                    "capture_audio": True,
                    "capture_cursor": False,
                },
            ))
            changed.append(f"已创建 Game Capture 源「{capture_name}」")
        else:
            already_ok.append(f"Game Capture 源「{capture_name}」已存在")

        # Always repair the existing managed source as well. overlay=True keeps
        # unrelated Game Capture properties intact and never touches user sources.
        before_audio = _dedicated_audio_health(
            ws,
            capture_name,
            scene_name=scene_name,
        )
        ws.call(obs_requests.SetInputSettings(
            inputName=capture_name,
            inputSettings={"capture_audio": True, "capture_cursor": False},
            overlay=True,
        ))
        if before_audio["capture_audio_enabled"] is True:
            already_ok.append("CS2 游戏音频捕获已启用")
        else:
            changed.append("已启用 CS2 Game Capture 游戏音频")

        if before_audio["capture_muted"] is True:
            ws.call(obs_requests.SetInputMute(inputName=capture_name, inputMuted=False))
            changed.append("已取消 CS2 游戏音频源静音")
        elif before_audio["capture_muted"] is False:
            already_ok.append("CS2 游戏音频源未静音")

        if _ensure_exclusive_input_audio_track(ws, capture_name, 1):
            changed.append("已将 CS2 游戏音频仅路由到 OBS 音轨 1")
        else:
            already_ok.append("CS2 游戏音频仅路由到 OBS 音轨 1")

        verified_audio = _dedicated_audio_health(
            ws,
            capture_name,
            scene_name=scene_name,
        )
        manual_actions: list[str] = []
        if verified_audio["track1_conflict_names"]:
            conflict_names = "、".join(verified_audio["track1_conflict_names"])
            manual_actions.append(
                "专属录制的 OBS 音轨 1 仍混入其他未静音音频源："
                f"{conflict_names}。请在 OBS 高级音频属性中将这些源移出音轨 1；"
                "应用不会静默修改 Desktop Audio、麦克风或其他用户输入。"
            )
        if not verified_audio["track1_conflict_scan_available"]:
            unverified_names = "、".join(
                verified_audio.get("track1_unverified_inputs", [])
            )
            suffix = f"（无法验证：{unverified_names}）" if unverified_names else ""
            manual_actions.append(
                "OBS 未返回完整的音轨 1 输入状态，无法确认录制音频不会重复混音"
                + suffix
            )
        source_audio_ready = bool(
            verified_audio["capture_audio_enabled"] is True
            and verified_audio["capture_muted"] is False
            and verified_audio["track1_enabled"] is True
            and verified_audio["exclusive_track1"] is True
        )
        if not source_audio_ready:
            raise ValueError(
                "OBS 未接受专属游戏音频修复："
                f"捕获开关={verified_audio['capture_audio_enabled']}，"
                f"静音={verified_audio['capture_muted']}，"
                f"启用音轨={verified_audio['enabled_tracks']}"
            )

        # Step 6: 设置拉伸填满画布
        item_id_resp = ws.call(obs_requests.GetSceneItemId(
            sceneName=scene_name, sourceName=capture_name
        ))
        item_id_data = getattr(item_id_resp, "datain", None) or {}
        item_id = item_id_data.get("sceneItemId")
        if item_id is not None:
            ws.call(obs_requests.SetSceneItemTransform(
                sceneName=scene_name,
                sceneItemId=int(item_id),
                sceneItemTransform={
                    "positionX": 0,
                    "positionY": 0,
                    "boundsType": "OBS_BOUNDS_STRETCH",
                    "boundsWidth": monitor_w,
                    "boundsHeight": monitor_h,
                },
            ))
            changed.append("已设置 Game Capture 拉伸填满画布")
        else:
            already_ok.append("Game Capture 拉伸变换跳过（无法获取 sceneItemId）")

        # Step 7: 修正当前实际输出模式对应的设置。
        restart_required = False
        output_mode = profile_recording["output_mode"]
        profile_category = "AdvOut" if output_mode == "Advanced" else "SimpleOutput"

        if output_mode == "Simple":
            rec_q_resp = ws.call(obs_requests.GetProfileParameter(
                parameterCategory="SimpleOutput", parameterName="RecQuality"
            ))
            rec_q_data = getattr(rec_q_resp, "datain", None) or {}
            rec_quality = rec_q_data.get("parameterValue", "")

            if rec_quality == "Stream":
                ws.call(obs_requests.SetProfileParameter(
                    parameterCategory="SimpleOutput",
                    parameterName="RecQuality",
                    parameterValue="Small",
                ))
                # RecQuality="Stream" 时 OBS 忽略 RecEncoder；改为 "Small" 后必须有有效编码器
                rec_enc_resp = ws.call(obs_requests.GetProfileParameter(
                    parameterCategory="SimpleOutput", parameterName="RecEncoder"
                ))
                rec_enc_val = ((getattr(rec_enc_resp, "datain", None) or {}).get("parameterValue") or "").strip()
                if not rec_enc_val or rec_enc_val.lower() in ("none", "null", "stream"):
                    try:
                        stream_enc_resp = ws.call(obs_requests.GetProfileParameter(
                            parameterCategory="SimpleOutput", parameterName="StreamEncoder"
                        ))
                        stream_enc = ((getattr(stream_enc_resp, "datain", None) or {}).get("parameterValue") or "").strip()
                    except Exception:  # noqa: BLE001
                        stream_enc = ""
                    _HW_PRIORITY = [
                        "jim_nvenc", "ffmpeg_nvenc", "h264_texture_amf",
                        "amd_amf_h264", "obs_qsv11_v2", "obs_qsv11",
                    ]
                    _INVALID = {"", "none", "null", "stream"}
                    target_enc = stream_enc if stream_enc.lower() not in _INVALID else _HW_PRIORITY[0]
                    ws.call(obs_requests.SetProfileParameter(
                        parameterCategory="SimpleOutput",
                        parameterName="RecEncoder",
                        parameterValue=target_enc,
                    ))
                    changed.append(f"录像编码器已设为「{target_enc}」（原质量为串流一致时未配置）")
                restart_required = True
                changed.append("录像质量已从「与串流一致」改为「高质量，中等文件大小」")
            else:
                already_ok.append("录像质量设置正常")
        else:
            already_ok.append("已识别高级输出模式，不套用简单输出质量选项")

        rec_f_resp = ws.call(obs_requests.GetProfileParameter(
            parameterCategory=profile_category, parameterName="RecFormat2"
        ))
        rec_f_data = getattr(rec_f_resp, "datain", None) or {}
        rec_format = rec_f_data.get("parameterValue", "")

        if rec_format != "hybrid_mp4":
            ws.call(obs_requests.SetProfileParameter(
                parameterCategory=profile_category,
                parameterName="RecFormat2",
                parameterValue="hybrid_mp4",
            ))
            changed.append("录像格式已改为「混合 MP4」")
        else:
            already_ok.append("录像格式正确（混合 MP4）")

        # Source routing and output routing are separate in OBS.  Preserve all
        # user-selected output tracks while ensuring Track 1 is actually written.
        fallback_mask = int(profile_recording.get("audio_track_mask", 1) or 0)
        try:
            rec_tracks_resp = ws.call(obs_requests.GetProfileParameter(
                parameterCategory=profile_category,
                parameterName="RecTracks",
            ))
            rec_tracks_raw = (getattr(rec_tracks_resp, "datain", None) or {}).get(
                "parameterValue",
                fallback_mask,
            )
        except Exception:  # noqa: BLE001
            rec_tracks_raw = fallback_mask
        current_track_mask = _parse_track_mask(rec_tracks_raw, fallback_mask)
        target_track_mask = current_track_mask | 1
        if current_track_mask != target_track_mask:
            ws.call(obs_requests.SetProfileParameter(
                parameterCategory=profile_category,
                parameterName="RecTracks",
                parameterValue=str(target_track_mask),
            ))
            verify_tracks_resp = ws.call(obs_requests.GetProfileParameter(
                parameterCategory=profile_category,
                parameterName="RecTracks",
            ))
            verify_tracks_raw = (getattr(verify_tracks_resp, "datain", None) or {}).get(
                "parameterValue",
                0,
            )
            verified_track_mask = _parse_track_mask(verify_tracks_raw, 0)
            if not (verified_track_mask & 1):
                raise ValueError("OBS 未接受录像输出音轨 1 设置，请在输出设置中手动勾选音轨 1")
            changed.append("已在录像输出中启用音轨 1")
        else:
            already_ok.append("录像输出已包含音轨 1")

        return {
            "success": not manual_actions,
            "changed": changed,
            "already_ok": already_ok,
            "manual_actions": manual_actions,
            "restart_obs_required": restart_required,
        }

    finally:
        _ws_disconnect(ws)


def restore_backup(backup_id: str, obs_cfg, *, project_profile: Optional[str] = None) -> dict[str, Any]:
    if sys.platform != "win32":
        raise ValueError("恢复备份仅支持 Windows")
    obs_root = _obs_studio_root()
    if obs_root is None:
        raise ValueError("无法定位 OBS 配置目录")
    pp = _effective_project_profile(obs_root, project_profile)
    bdir = _backup_root() / backup_id
    if not bdir.is_dir():
        raise ValueError("备份不存在")
    ws: Optional[obsws] = None
    try:
        ws = _ws_connect(obs_cfg)
        if _obs_is_recording(ws):
            raise ValueError("OBS 正在录制中，请停止录制后再恢复备份。")
    finally:
        _ws_disconnect(ws)
    _restore_backup_pack(bdir, obs_root, pp)
    return {
        "ok": True,
        "restored": True,
        "restart_obs_required": True,
        "message": "OBS 配置已恢复，请重启 OBS 后生效。",
    }


def delete_backup(backup_id: str) -> dict[str, Any]:
    bdir = _backup_root() / backup_id
    if not bdir.is_dir():
        raise ValueError("备份不存在")
    shutil.rmtree(bdir, ignore_errors=True)
    return {"ok": True}


def open_backup_folder() -> dict[str, Any]:
    root = _backup_root()
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
        return {"ok": True, "path": path_str}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": path_str, "message": str(e)}
