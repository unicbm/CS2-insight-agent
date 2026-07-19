"""OBS 配置中心：诊断、推荐预设、.cs2obs 与原生文件导入、备份恢复（主要面向 Windows 本机 OBS 配置目录）。"""

from __future__ import annotations

import configparser
import filecmp
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from obswebsocket import obsws, requests as obs_requests

from .env_utils import get_data_dir

logger = logging.getLogger(__name__)

APP_VERSION = "V2.3.0"
DEFAULT_PROJECT_PROFILE = "未命名"  # 解析失败时的兜底目录名；正常由 resolve_default_project_profile_for_obs() 解析
BACKUP_SUBDIR = ".obs_config_backups"

_OUTPUT_MODE_SIMPLE = "simple"
_OUTPUT_MODE_ADVANCED = "advanced"
_NVENC_HEVC_ENCODER = "obs_nvenc_hevc_tex"
_VIDEO_PRESET_DISPLAY = "display"
_VIDEO_PRESET_PRO_4X3_480 = "pro_4x3_480"
_PRO_VIDEO_WIDTH = 1280
_PRO_VIDEO_HEIGHT = 960
_PRO_VIDEO_FPS = 480
_AMF_ENCODERS = frozenset(
    {
        "h264_texture_amf",
        "h265_texture_amf",
        "amd_amf_h264",
        "amd_amf_hevc",
    }
)
_ADVANCED_STREAM_ENCODERS = frozenset({"none", "stream", "use_stream_encoder"})


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
        values[key.strip().lower()] = value.strip()
    # OBS 30+ writes ProfileDir/SceneCollectionFile under [Basic].  Older
    # installations and imported profiles may still use the Current* keys.
    prof = (
        values.get("currentprofile")
        or values.get("profiledir")
        or values.get("profile")
        or None
    )
    sc = (
        values.get("currentscenecollection")
        or values.get("scenecollectionfile")
        or values.get("scenecollection")
        or None
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


def _normalise_output_mode(raw: object) -> str:
    return (
        _OUTPUT_MODE_ADVANCED
        if str(raw or "").strip().lower() == _OUTPUT_MODE_ADVANCED
        else _OUTPUT_MODE_SIMPLE
    )


def _parse_output_profile(ini_path: Path) -> tuple[str, dict[str, str], dict[str, str]]:
    if not ini_path.is_file():
        return _OUTPUT_MODE_SIMPLE, {}, {}
    cp = configparser.ConfigParser(interpolation=None)
    # OBS option names are case-sensitive in the rest of this module
    # (RecEncoder, RecFormat2, ...). ConfigParser lower-cases them by default.
    cp.optionxform = str
    try:
        cp.read(ini_path, encoding="utf-8-sig")
    except (OSError, configparser.Error):
        return _OUTPUT_MODE_SIMPLE, {}, {}
    output = cp["Output"] if "Output" in cp else {}
    simple = cp["SimpleOutput"] if "SimpleOutput" in cp else {}
    advanced = cp["AdvOut"] if "AdvOut" in cp else {}
    mode = _normalise_output_mode(output.get("Mode") if output else "")
    return (
        mode,
        {k: str(v) for k, v in simple.items()},
        {k: str(v) for k, v in advanced.items()},
    )


def _parse_simple_output(ini_path: Path) -> dict[str, str]:
    return _parse_output_profile(ini_path)[1]


def _parse_adv_output_rec_path(ini_path: Path) -> str:
    """从 basic.ini [AdvOut] 读取高级输出模式的录像目录（RecFilePath）。"""
    section = _parse_output_profile(ini_path)[2]
    return str(section.get("RecFilePath") or "").strip()


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


def _recording_video_target(
    obs_cfg: object,
    monitor_width: int,
    monitor_height: int,
    current_video: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Resolve the single video target used by status, diagnosis and calibration."""
    raw_preset = str(
        getattr(obs_cfg, "recording_video_preset", _VIDEO_PRESET_DISPLAY) or ""
    ).strip()
    preset = (
        _VIDEO_PRESET_PRO_4X3_480
        if raw_preset == _VIDEO_PRESET_PRO_4X3_480
        else _VIDEO_PRESET_DISPLAY
    )
    if preset == _VIDEO_PRESET_PRO_4X3_480:
        return {
            "preset": preset,
            "width": _PRO_VIDEO_WIDTH,
            "height": _PRO_VIDEO_HEIGHT,
            "fps_num": _PRO_VIDEO_FPS,
            "fps_den": 1,
            "fps": _PRO_VIDEO_FPS,
        }

    current = current_video or {}
    fps_num = int(current.get("fps_num") or 60)
    fps_den = max(1, int(current.get("fps_den") or 1))
    if fps_num / fps_den < 60:
        fps_num, fps_den = 60, 1
    return {
        "preset": preset,
        "width": int(monitor_width),
        "height": int(monitor_height),
        "fps_num": fps_num,
        "fps_den": fps_den,
        "fps": int(round(fps_num / fps_den)),
    }


def _video_fps_matches_target(video: dict[str, int], target: dict[str, Any]) -> bool:
    actual_num = int(video.get("fps_num") or 0)
    actual_den = max(1, int(video.get("fps_den") or 1))
    target_num = int(target["fps_num"])
    target_den = max(1, int(target["fps_den"]))
    return actual_num * target_den == target_num * actual_den


def _get_profile_parameter(ws: obsws, category: str, name: str) -> str:
    resp = ws.call(
        obs_requests.GetProfileParameter(
            parameterCategory=category,
            parameterName=name,
        )
    )
    raw = (getattr(resp, "datain", None) or {}).get("parameterValue")
    return str(raw or "").strip()


def _profile_parameter_or_default(
    ws: obsws,
    category: str,
    name: str,
    default: str = "",
) -> str:
    try:
        value = _get_profile_parameter(ws, category, name)
    except Exception:  # noqa: BLE001
        return str(default or "").strip()
    return value or str(default or "").strip()


def _effective_output_mode(ws: obsws, disk_mode: str) -> str:
    return _normalise_output_mode(
        _profile_parameter_or_default(ws, "Output", "Mode", disk_mode)
    )


def _is_nvenc_encoder_id(raw: object) -> bool:
    value = str(raw or "").strip().lower()
    return value == "nvenc" or "nvenc" in value


def _obs_runtime_log_confirms_nvenc(obs_root: Optional[Path]) -> bool:
    """Use the current OBS startup log as capability evidence, not a GPU-name guess."""
    if obs_root is None:
        return False
    logs_dir = obs_root / "logs"
    try:
        latest = max(logs_dir.glob("*.txt"), key=lambda path: path.stat().st_mtime)
        raw = latest.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return "[obs-nvenc] NVENC version:" in raw


def _is_amf_encoder_id(raw: object) -> bool:
    return str(raw or "").strip().lower() in _AMF_ENCODERS


def _advanced_uses_stream_encoder(advanced: dict[str, str]) -> bool:
    return str(advanced.get("RecEncoder") or "").strip().lower() in _ADVANCED_STREAM_ENCODERS


def _recording_output_path_from_advanced(advanced: dict[str, str]) -> str:
    return str(advanced.get("RecFilePath") or advanced.get("FFFilePath") or "").strip()


def _set_profile_parameter_verified(
    ws: obsws,
    *,
    category: str,
    name: str,
    value: str,
    previous: str,
) -> None:
    """Set a profile value and roll it back when OBS does not echo it."""
    ws.call(
        obs_requests.SetProfileParameter(
            parameterCategory=category,
            parameterName=name,
            parameterValue=value,
        )
    )
    try:
        observed = _get_profile_parameter(ws, category, name)
    except Exception as exc:  # noqa: BLE001
        try:
            ws.call(
                obs_requests.SetProfileParameter(
                    parameterCategory=category,
                    parameterName=name,
                    parameterValue=previous,
                )
            )
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"OBS 未能回读 {category}/{name}，已尝试恢复原设置") from exc
    if observed != value:
        try:
            ws.call(
                obs_requests.SetProfileParameter(
                    parameterCategory=category,
                    parameterName=name,
                    parameterValue=previous,
                )
            )
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(
            f"OBS 未接受 {category}/{name}={value}（实际为 {observed or '空'}），已尝试恢复原设置"
        )


def _detect_use_stream_encoder(simple: dict[str, str], obs_ws: Optional[obsws]) -> bool:
    # ini 启发式
    for key in ("RecUseStreamEncoder", "UseStreamEncoder", "rec_use_stream_encoder"):
        val = simple.get(key)
        if val is not None:
            return str(val).strip().lower() in ("1", "true", "yes")
    same = (simple.get("RecEncoder") or "").strip() and (simple.get("Encoder") or "").strip()
    if same and simple.get("RecEncoder") == simple.get("Encoder"):
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
    return (simple.get("FilePath") or simple.get("FilePath2") or "").strip()


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
    prof_name, sc_name = (None, None)
    if obs_root:
        prof_name, sc_name = _read_global_profile_names(obs_root)
        if not prof_name:
            prof_name = _resolve_obs_profile_folder_name(obs_root)

    latest = _latest_backup_summary()
    from .env_utils import get_primary_monitor_resolution
    _mon_w, _mon_h = get_primary_monitor_resolution()
    initial_target = _recording_video_target(obs_cfg, _mon_w, _mon_h)
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
            "output_mode": _OUTPUT_MODE_SIMPLE,
            "use_stream_encoder": False,
            "encoder": "",
            "format": "",
            "output_path": "",
        },
        "scene": {
            "dedicated_scene_exists": False,
            "capture_source_exists": False,
            "source_fit_to_canvas": False,
        },
        "monitor": {"width": _mon_w, "height": _mon_h},
        "recording_video_preset": initial_target["preset"],
        "video_target": initial_target,
        "latest_backup": latest,
        "obs_version": None,
    }

    if sys.platform != "win32":
        base["ok"] = True
        base["message"] = "OBS 配置文件操作建议在 Windows 上进行；仍可尝试连接 OBS WebSocket 查看画面状态。"
    ws: Optional[obsws] = None
    try:
        ws = _ws_connect(obs_cfg)
    except Exception as e:  # noqa: BLE001
        base["obs_connected"] = False
        base["ws_error"] = str(e)
        return base

    base["obs_connected"] = True
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
        base["video_target"] = _recording_video_target(obs_cfg, _mon_w, _mon_h, vd)
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
        mode = _OUTPUT_MODE_SIMPLE
        simple: dict[str, str] = {}
        advanced: dict[str, str] = {}
        if obs_root and prof_name:
            mode, simple, advanced = _parse_output_profile(
                obs_root / "basic" / "profiles" / prof_name / "basic.ini"
            )
        mode = _effective_output_mode(ws, mode)
        base["recording"]["output_mode"] = mode

        if mode == _OUTPUT_MODE_ADVANCED:
            encoder = _profile_parameter_or_default(
                ws,
                "AdvOut",
                "RecEncoder",
                advanced.get("RecEncoder") or "",
            )
            advanced["RecEncoder"] = encoder
            base["recording"]["use_stream_encoder"] = _advanced_uses_stream_encoder(advanced)
            base["recording"]["encoder"] = encoder
            base["recording"]["format"] = _profile_parameter_or_default(
                ws,
                "AdvOut",
                "RecFormat2",
                advanced.get("RecFormat2") or advanced.get("RecFormat") or "",
            )
            base["recording"]["output_path"] = (
                _recording_output_path_from_advanced(advanced)
                or _get_record_directory_via_ws(ws)
            )
            # Advanced mode has no SimpleOutput-style quality preset.  A
            # truthy marker keeps the existing UI from calling it "unknown".
            base["recording"]["rec_quality"] = "Advanced"
        else:
            base["recording"]["use_stream_encoder"] = _detect_use_stream_encoder(simple, ws)
            base["recording"]["encoder"] = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "RecEncoder",
                simple.get("RecEncoder") or simple.get("Encoder") or "",
            )
            base["recording"]["format"] = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "RecFormat2",
                simple.get("RecFormat2") or simple.get("RecFormat") or "",
            )
            base["recording"]["output_path"] = (
                _recording_output_path_from_simple(simple)
                or _get_record_directory_via_ws(ws)
            )
            base["recording"]["rec_quality"] = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "RecQuality",
                simple.get("RecQuality") or "",
            )
    except Exception as e:  # noqa: BLE001
        base["ws_error"] = str(e)
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
    video_target: Optional[dict[str, Any]] = None
    prof_name: Optional[str] = None
    disk_profile_checked = False
    recording_state: dict[str, Any] = {
        "output_mode": _OUTPUT_MODE_SIMPLE,
        "encoder": "",
        "format": "",
        "output_path": "",
        "use_stream_encoder": False,
    }

    try:
        ws = _ws_connect(obs_cfg)
        obs_connected = True

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
        video_target = _recording_video_target(
            obs_cfg,
            monitor_w,
            monitor_h,
            video_dims,
        )
        target_w = int(video_target["width"])
        target_h = int(video_target["height"])
        high_fps_preset = video_target["preset"] == _VIDEO_PRESET_PRO_4X3_480
        target_name = "专业 4:3 高帧率预设" if high_fps_preset else "主显示器"
        fps = _fps_from_video_dict(video_dims)
        bw, bh = video_dims["base_width"], video_dims["base_height"]
        ow, oh = video_dims["output_width"], video_dims["output_height"]
        if bw and bh and (bw != target_w or bh != target_h):
            issues.append(
                {
                    "code": "CANVAS_RESOLUTION_MISMATCH",
                    "level": "warning",
                    "title": f"画布分辨率与{target_name}不一致",
                    "message": f"当前 {bw}×{bh}，应为 {target_w}×{target_h}。",
                    "fixable": True,
                }
            )
        if ow and oh and (ow != target_w or oh != target_h):
            issues.append(
                {
                    "code": "OUTPUT_RESOLUTION_MISMATCH",
                    "level": "warning",
                    "title": f"输出分辨率与{target_name}不一致",
                    "message": f"当前 {ow}×{oh}，应为 {target_w}×{target_h}。",
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
        if high_fps_preset and not _video_fps_matches_target(video_dims, video_target):
            issues.append(
                {
                    "code": "FPS_PRESET_MISMATCH",
                    "level": "warning",
                    "title": "录制帧率不是 480 FPS",
                    "message": f"当前 {fps} FPS；专业高帧率预设要求 480 FPS。",
                    "fixable": True,
                }
            )
        elif not high_fps_preset and fps < 60:
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

        disk_mode = _OUTPUT_MODE_SIMPLE
        simple: dict[str, str] = {}
        advanced: dict[str, str] = {}
        if obs_root is not None and obs_root.is_dir():
            prof_name, _ = _read_global_profile_names(obs_root)
            if not prof_name:
                prof_name = _resolve_obs_profile_folder_name(obs_root)
            if prof_name:
                pin = obs_root / "basic" / "profiles" / prof_name / "basic.ini"
                if pin.is_file():
                    disk_mode, simple, advanced = _parse_output_profile(pin)
                    disk_profile_checked = True

        output_mode = _effective_output_mode(ws, disk_mode)
        recording_state["output_mode"] = output_mode
        if output_mode == _OUTPUT_MODE_ADVANCED:
            encoder = _profile_parameter_or_default(
                ws,
                "AdvOut",
                "RecEncoder",
                advanced.get("RecEncoder") or "",
            )
            advanced["RecEncoder"] = encoder
            rec_format = _profile_parameter_or_default(
                ws,
                "AdvOut",
                "RecFormat2",
                advanced.get("RecFormat2") or advanced.get("RecFormat") or "",
            )
            out_path = (
                _recording_output_path_from_advanced(advanced)
                or _get_record_directory_via_ws(ws)
            )
            uses_stream = _advanced_uses_stream_encoder(advanced)
            stream_encoder = _profile_parameter_or_default(
                ws,
                "AdvOut",
                "Encoder",
                advanced.get("Encoder") or "",
            )
        else:
            encoder = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "RecEncoder",
                simple.get("RecEncoder") or simple.get("Encoder") or "",
            )
            rec_format = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "RecFormat2",
                simple.get("RecFormat2") or simple.get("RecFormat") or "",
            )
            out_path = (
                _recording_output_path_from_simple(simple)
                or _get_record_directory_via_ws(ws)
            )
            uses_stream = _detect_use_stream_encoder(simple, ws)
            stream_encoder = _profile_parameter_or_default(
                ws,
                "SimpleOutput",
                "StreamEncoder",
                simple.get("StreamEncoder") or "",
            )

        recording_state.update(
            {
                "encoder": encoder,
                "format": rec_format,
                "output_path": out_path,
                "use_stream_encoder": uses_stream,
            }
        )

        if disk_profile_checked:
            if high_fps_preset and not _is_nvenc_encoder_id(encoder):
                issues.append(
                    {
                        "code": "HIGH_FPS_NVENC_REQUIRED",
                        "level": "error",
                        "title": "480 FPS 预设未使用 NVIDIA NVENC",
                        "message": (
                            f"当前录像编码器为 {encoder or '未配置'}。"
                            "专业高帧率预设必须先确认 NVENC 可用，之后才会修改视频规格。"
                        ),
                        "fixable": (
                            _is_nvenc_encoder_id(stream_encoder)
                            or _obs_runtime_log_confirms_nvenc(obs_root)
                        ),
                    }
                )
            if uses_stream:
                issues.append(
                    {
                        "code": "USE_STREAM_ENCODER",
                        "level": "error",
                        "title": "录制正在使用与串流一致",
                        "message": "当前录制配置依赖串流编码器，可能导致录制失败。建议应用推荐预设。",
                        "fixable": True,
                    }
                )
            if not out_path:
                issues.append(
                    {
                        "code": "NO_OUTPUT_PATH",
                        "level": "warning",
                        "title": "未设置录制输出目录",
                        "message": "请在 OBS 设置中为录制指定输出路径。",
                        "fixable": False,
                    }
                )
            if not encoder:
                issues.append(
                    {
                        "code": "ENCODER_UNAVAILABLE",
                        "level": "warning",
                        "title": "录制编码器未配置或为空",
                        "message": "建议应用推荐预设或手动选择可用编码器。",
                        "fixable": True,
                    }
                )
            if rec_format and rec_format != "hybrid_mp4":
                issues.append(
                    {
                        "code": "RECORD_FORMAT_MISMATCH",
                        "level": "warning",
                        "title": "录像格式不是混合 MP4",
                        "message": f"当前格式为 {rec_format}，建议应用推荐预设。",
                        "fixable": True,
                    }
                )
            if (
                output_mode == _OUTPUT_MODE_ADVANCED
                and _is_amf_encoder_id(encoder)
                and _is_nvenc_encoder_id(stream_encoder)
            ):
                issues.append(
                    {
                        "code": "ADVANCED_AMF_ON_NVENC",
                        "level": "error",
                        "title": "高级输出使用了错误的 AMD 录像编码器",
                        "message": (
                            f"当前录像编码器为 {encoder}，但 OBS 的串流编码器为 {stream_encoder}。"
                            "这会在 NVIDIA 渲染适配器上触发 AMF 适配器错误；可应用推荐预设切换到 NVENC HEVC。"
                        ),
                        "fixable": True,
                    }
                )
    except Exception as e:  # noqa: BLE001
        obs_connected = False
        obs_version = None
        issues.append(
            {
                "code": "OBS_NOT_CONNECTED",
                "level": "error",
                "title": "无法连接 OBS WebSocket",
                "message": str(e) or "请确认 OBS 已启动且 WebSocket 已启用。",
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
        "recording": recording_state,
        "recording_video_preset": (
            video_target["preset"]
            if video_target is not None
            else _recording_video_target(obs_cfg, 0, 0)["preset"]
        ),
        "video_target": video_target,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _calibrate_simple_output(
    ws: obsws,
    *,
    require_nvenc: bool = False,
    nvenc_available: bool = False,
) -> tuple[list[str], list[str], bool]:
    changed: list[str] = []
    already_ok: list[str] = []
    restart_required = False

    rec_quality = _get_profile_parameter(ws, "SimpleOutput", "RecQuality")
    rec_encoder = _get_profile_parameter(ws, "SimpleOutput", "RecEncoder")
    stream_encoder = _get_profile_parameter(ws, "SimpleOutput", "StreamEncoder")
    if (
        require_nvenc
        and not nvenc_available
        and not _is_nvenc_encoder_id(rec_encoder)
        and not _is_nvenc_encoder_id(stream_encoder)
    ):
        raise ValueError(
            "专业 480 FPS 预设需要 NVIDIA NVENC，但当前 OBS 简单输出中未检测到可用的 NVENC 编码器。"
        )

    if rec_quality == "Stream":
        _set_profile_parameter_verified(
            ws,
            category="SimpleOutput",
            name="RecQuality",
            value="Small",
            previous=rec_quality,
        )
        # RecQuality="Stream" 时 OBS 忽略 RecEncoder；改为 "Small" 后必须有有效编码器。
        if not rec_encoder or rec_encoder.lower() in ("none", "null", "stream"):
            hw_priority = [
                "jim_nvenc",
                "ffmpeg_nvenc",
                "h264_texture_amf",
                "amd_amf_h264",
                "obs_qsv11_v2",
                "obs_qsv11",
            ]
            invalid = {"", "none", "null", "stream"}
            target_enc = stream_encoder if stream_encoder.lower() not in invalid else hw_priority[0]
            _set_profile_parameter_verified(
                ws,
                category="SimpleOutput",
                name="RecEncoder",
                value=target_enc,
                previous=rec_encoder,
            )
            changed.append(f"录像编码器已设为「{target_enc}」（原质量为串流一致时未配置）")
            rec_encoder = target_enc
        restart_required = True
        changed.append("录像质量已从「与串流一致」改为「高质量，中等文件大小」")
    else:
        already_ok.append("录像质量设置正常")

    if require_nvenc and not _is_nvenc_encoder_id(rec_encoder):
        _set_profile_parameter_verified(
            ws,
            category="SimpleOutput",
            name="RecEncoder",
            value="nvenc",
            previous=rec_encoder,
        )
        changed.append(f"简单输出录像编码器已从「{rec_encoder or '未配置'}」切换为「nvenc」")
        restart_required = True
    elif require_nvenc:
        already_ok.append(f"简单输出录像编码器正常（{rec_encoder}）")

    rec_format = _get_profile_parameter(ws, "SimpleOutput", "RecFormat2")
    if rec_format != "hybrid_mp4":
        _set_profile_parameter_verified(
            ws,
            category="SimpleOutput",
            name="RecFormat2",
            value="hybrid_mp4",
            previous=rec_format,
        )
        changed.append("录像格式已改为「混合 MP4」")
    else:
        already_ok.append("录像格式正确（混合 MP4）")

    return changed, already_ok, restart_required


def _calibrate_advanced_output(
    ws: obsws,
    advanced_disk: dict[str, str],
    *,
    require_nvenc: bool = False,
    nvenc_available: bool = False,
) -> tuple[list[str], list[str], bool]:
    changed: list[str] = []
    already_ok: list[str] = []
    restart_required = False

    # Require live WebSocket reads before touching an encoder. A disk-only
    # guess can be stale while OBS is running and would be unsafe to apply.
    rec_encoder = _get_profile_parameter(ws, "AdvOut", "RecEncoder")
    stream_encoder = _get_profile_parameter(ws, "AdvOut", "Encoder")
    if not rec_encoder:
        rec_encoder = str(advanced_disk.get("RecEncoder") or "").strip()
    if not stream_encoder:
        stream_encoder = str(advanced_disk.get("Encoder") or "").strip()

    uses_stream_encoder = _advanced_uses_stream_encoder({"RecEncoder": rec_encoder})
    if uses_stream_encoder:
        if not stream_encoder or _advanced_uses_stream_encoder({"RecEncoder": stream_encoder}):
            raise ValueError(
                "高级输出录像正在使用“与串流一致”，但串流编码器也未配置；无法创建独立录像编码器。"
            )
        if require_nvenc and not nvenc_available and not _is_nvenc_encoder_id(stream_encoder):
            raise ValueError(
                "专业 480 FPS 预设需要 NVIDIA NVENC，但当前串流编码器不是 NVENC；未修改视频规格。"
            )
        target_encoder = _NVENC_HEVC_ENCODER if require_nvenc else stream_encoder
        _set_profile_parameter_verified(
            ws,
            category="AdvOut",
            name="RecEncoder",
            value=target_encoder,
            previous=rec_encoder,
        )
        changed.append(
            f"高级输出录像已从“与串流一致”改为独立编码器「{target_encoder}」"
        )
        restart_required = True
    elif require_nvenc and not _is_nvenc_encoder_id(rec_encoder):
        if not nvenc_available and not _is_nvenc_encoder_id(stream_encoder):
            raise ValueError(
                "专业 480 FPS 预设需要 NVIDIA NVENC，但当前 OBS 高级输出中无法确认 NVENC 可用；未修改视频规格。"
            )
        _set_profile_parameter_verified(
            ws,
            category="AdvOut",
            name="RecEncoder",
            value=_NVENC_HEVC_ENCODER,
            previous=rec_encoder,
        )
        changed.append(
            f"高级输出录像编码器已从「{rec_encoder}」切换为「{_NVENC_HEVC_ENCODER}」"
        )
        restart_required = True
    elif _is_amf_encoder_id(rec_encoder):
        if not nvenc_available and not _is_nvenc_encoder_id(stream_encoder):
            raise ValueError(
                "检测到高级输出正在使用 AMD AMF，但无法确认 NVENC 可用；为避免写入不可用编码器，未自动修改。"
            )
        _set_profile_parameter_verified(
            ws,
            category="AdvOut",
            name="RecEncoder",
            value=_NVENC_HEVC_ENCODER,
            previous=rec_encoder,
        )
        changed.append(
            f"高级输出录像编码器已从「{rec_encoder}」切换为「{_NVENC_HEVC_ENCODER}」"
        )
        restart_required = True
    else:
        already_ok.append(f"高级输出录像编码器正常（{rec_encoder or '未配置'}）")

    rec_format = _get_profile_parameter(ws, "AdvOut", "RecFormat2")
    if not rec_format:
        rec_format = str(
            advanced_disk.get("RecFormat2") or advanced_disk.get("RecFormat") or ""
        ).strip()
    if rec_format != "hybrid_mp4":
        _set_profile_parameter_verified(
            ws,
            category="AdvOut",
            name="RecFormat2",
            value="hybrid_mp4",
            previous=rec_format,
        )
        changed.append("高级输出录像格式已改为「混合 MP4」")
    else:
        already_ok.append("高级输出录像格式正确（混合 MP4）")

    return changed, already_ok, restart_required


def calibrate(obs_cfg) -> dict[str, Any]:
    """Apply the selected video preset and repair the dedicated recording scene.

    Video and output settings belong to the active OBS profile. Scene changes are
    limited to the dedicated CS2 Insight scene; other scene contents are untouched.
    """
    from .env_utils import get_primary_monitor_resolution

    ws = None
    try:
        ws = _ws_connect(obs_cfg)
    except Exception as exc:
        raise ValueError(f"OBS WebSocket 未连接，请先在设置中测试连接：{exc}") from exc

    video_snapshot: Optional[dict[str, int]] = None
    video_rollback_armed = False
    output_snapshot: dict[tuple[str, str], str] = {}
    output_rollback_armed = False
    try:
        if _obs_is_recording(ws):
            raise ValueError("录制进行中，无法修改视频设置，请录制结束后再校准")

        changed: list[str] = []
        already_ok: list[str] = []

        # Step 1+2: 读显示器分辨率 & OBS 画布
        monitor_w, monitor_h = get_primary_monitor_resolution()

        vr = ws.call(obs_requests.GetVideoSettings())
        vd = _parse_ws_video(vr)
        video_snapshot = dict(vd)
        canvas_w = vd.get("base_width", 0)
        canvas_h = vd.get("base_height", 0)
        output_w = vd.get("output_width", 0)
        output_h = vd.get("output_height", 0)
        existing_fps_n = vd.get("fps_num", 60)
        existing_fps_d = vd.get("fps_den", 1)
        video_target = _recording_video_target(obs_cfg, monitor_w, monitor_h, vd)
        target_w = int(video_target["width"])
        target_h = int(video_target["height"])
        target_fps_n = int(video_target["fps_num"])
        target_fps_d = int(video_target["fps_den"])

        # Validate and repair the active recording encoder before raising the
        # profile to 480 FPS. A failed NVENC preflight must leave video settings
        # untouched instead of producing a half-applied, CPU-bound preset.
        disk_mode = _OUTPUT_MODE_SIMPLE
        advanced_disk: dict[str, str] = {}
        obs_root = _obs_studio_root()
        if obs_root is not None:
            profile_name, _ = _read_global_profile_names(obs_root)
            if not profile_name:
                profile_name = _resolve_obs_profile_folder_name(obs_root)
            if profile_name:
                profile_ini = obs_root / "basic" / "profiles" / profile_name / "basic.ini"
                disk_mode, _, advanced_disk = _parse_output_profile(profile_ini)
        output_mode = _effective_output_mode(ws, disk_mode)
        require_nvenc = video_target["preset"] == _VIDEO_PRESET_PRO_4X3_480
        runtime_nvenc_available = _obs_runtime_log_confirms_nvenc(obs_root)
        if output_mode == _OUTPUT_MODE_ADVANCED:
            output_snapshot = {
                ("AdvOut", name): _get_profile_parameter(ws, "AdvOut", name)
                for name in ("RecEncoder", "RecFormat2")
            }
            output_rollback_armed = True
            output_changed, output_ok, restart_required = _calibrate_advanced_output(
                ws,
                advanced_disk,
                require_nvenc=require_nvenc,
                nvenc_available=runtime_nvenc_available,
            )
        else:
            output_snapshot = {
                ("SimpleOutput", name): _get_profile_parameter(ws, "SimpleOutput", name)
                for name in ("RecQuality", "RecEncoder", "RecFormat2")
            }
            output_rollback_armed = True
            output_changed, output_ok, restart_required = _calibrate_simple_output(
                ws,
                require_nvenc=require_nvenc,
                nvenc_available=runtime_nvenc_available,
            )
        changed.extend(output_changed)
        already_ok.extend(output_ok)

        # Step 3: 修正画布与输出分辨率
        # OBS WS v5 SetVideoSettings 字段是顶层 kwargs，不能嵌套在 videoSettings={}
        canvas_needs_fix = canvas_w != target_w or canvas_h != target_h
        output_needs_fix = output_w != target_w or output_h != target_h
        fps_needs_fix = not _video_fps_matches_target(vd, video_target)
        if canvas_needs_fix or output_needs_fix or fps_needs_fix:
            video_rollback_armed = True
            ws.call(obs_requests.SetVideoSettings(
                fpsNumerator=target_fps_n,
                fpsDenominator=target_fps_d,
                baseWidth=target_w,
                baseHeight=target_h,
                outputWidth=target_w,
                outputHeight=target_h,
            ))
            # 回读验证：避免静默失败时错误报告成功
            verify_vr = ws.call(obs_requests.GetVideoSettings())
            verify_vd = _parse_ws_video(verify_vr)
            canvas_ok = verify_vd["base_width"] == target_w and verify_vd["base_height"] == target_h
            output_ok = verify_vd["output_width"] == target_w and verify_vd["output_height"] == target_h
            fps_ok = _video_fps_matches_target(verify_vd, video_target)
            if canvas_ok and output_ok and fps_ok:
                if canvas_needs_fix:
                    changed.append(f"已将画布分辨率从 {canvas_w}×{canvas_h} 修正为 {target_w}×{target_h}")
                if output_needs_fix:
                    changed.append(f"已将输出分辨率从 {output_w}×{output_h} 修正为 {target_w}×{target_h}")
                if fps_needs_fix:
                    changed.append(
                        f"已将录制帧率从 {round(existing_fps_n / max(1, existing_fps_d))} FPS "
                        f"修正为 {round(target_fps_n / max(1, target_fps_d))} FPS"
                    )
            else:
                parts: list[str] = []
                if not canvas_ok:
                    parts.append(
                        f"画布仍为 {verify_vd['base_width']}×{verify_vd['base_height']}（应为 {target_w}×{target_h}）"
                    )
                if not output_ok:
                    parts.append(
                        f"输出仍为 {verify_vd['output_width']}×{verify_vd['output_height']}（应为 {target_w}×{target_h}）"
                    )
                if not fps_ok:
                    parts.append(
                        f"帧率仍为 {_fps_from_video_dict(verify_vd)} FPS（应为 {video_target['fps']} FPS）"
                    )
                raise ValueError(
                    "OBS 未接受视频设置修改（" + "；".join(parts) + "），"
                    f"请在 OBS 设置→视频中手动改为 {target_w}×{target_h} / {video_target['fps']} FPS"
                )
        else:
            already_ok.append(f"画布分辨率正确（{canvas_w}×{canvas_h}）")
            already_ok.append(f"输出分辨率正确（{output_w}×{output_h}）")
            already_ok.append(f"录制帧率正确（{_fps_from_video_dict(vd)} FPS）")

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
                inputSettings={"capture_mode": "window", "window": "cs2.exe"},
            ))
            changed.append(f"已创建 Game Capture 源「{capture_name}」")
        else:
            already_ok.append(f"Game Capture 源「{capture_name}」已存在")

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
                    "boundsWidth": target_w,
                    "boundsHeight": target_h,
                },
            ))
            changed.append("已设置 Game Capture 拉伸填满画布")
        else:
            already_ok.append("Game Capture 拉伸变换跳过（无法获取 sceneItemId）")

        return {
            "success": True,
            "changed": changed,
            "already_ok": already_ok,
            "restart_obs_required": restart_required,
            "recording_video_preset": video_target["preset"],
            "video_target": video_target,
        }

    except Exception as exc:
        rollback_errors: list[str] = []
        if video_rollback_armed and video_snapshot is not None:
            try:
                ws.call(obs_requests.SetVideoSettings(
                    fpsNumerator=int(video_snapshot["fps_num"]),
                    fpsDenominator=int(video_snapshot["fps_den"]),
                    baseWidth=int(video_snapshot["base_width"]),
                    baseHeight=int(video_snapshot["base_height"]),
                    outputWidth=int(video_snapshot["output_width"]),
                    outputHeight=int(video_snapshot["output_height"]),
                ))
                restored_video = _parse_ws_video(ws.call(obs_requests.GetVideoSettings()))
                if restored_video != video_snapshot:
                    rollback_errors.append("视频设置回读不一致")
            except Exception:  # noqa: BLE001
                rollback_errors.append("视频设置恢复失败")
        if output_rollback_armed:
            for (category, name), previous in output_snapshot.items():
                try:
                    ws.call(obs_requests.SetProfileParameter(
                        parameterCategory=category,
                        parameterName=name,
                        parameterValue=previous,
                    ))
                    if _get_profile_parameter(ws, category, name) != previous:
                        rollback_errors.append(f"{category}/{name} 回读不一致")
                except Exception:  # noqa: BLE001
                    rollback_errors.append(f"{category}/{name} 恢复失败")
        original = str(exc) or "OBS 校准失败"
        if rollback_errors:
            raise ValueError(
                f"{original}；自动回滚未完全成功：{'；'.join(rollback_errors)}。请刷新状态后再操作。"
            ) from exc
        if video_rollback_armed or output_rollback_armed:
            raise ValueError(f"{original}；已恢复本次校准修改的视频与输出设置。") from exc
        raise
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
