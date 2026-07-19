"""录制预热控制台注入：固定 cvar 已迁出硬编码，仅由 record_inject_console_lines 提供。"""

from app.env_utils import OBSConfig
from app.obs_director import OBSDirector, RecordingWarmupExtras

FIXED_CVARS = (
    "cl_hud_telemetry_frametime_show 0",
    "engine_no_focus_sleep 0",
    "cl_demo_predict 0",
    "fps_max 0",
    "cl_trueview_show_status 0",
)


def _director(inject_lines: str) -> OBSDirector:
    return OBSDirector(OBSConfig(), "", record_inject_console_lines=inject_lines)


def test_no_fixed_cvars_when_inject_lines_empty():
    director = _director("")
    lines = director._recording_warmup_console_lines(RecordingWarmupExtras())
    for cvar in FIXED_CVARS:
        assert cvar not in lines


def test_fixed_cvars_injected_from_config():
    director = _director("\n".join(FIXED_CVARS))
    lines = director._recording_warmup_console_lines(RecordingWarmupExtras())
    for cvar in FIXED_CVARS:
        assert cvar in lines


def test_keybind_reset_still_forced():
    # 只覆盖自动化占用键；绝不能再清空整张玩家键位表。
    director = _director("")
    lines = director._recording_warmup_console_lines(RecordingWarmupExtras())
    assert "unbindall" not in lines
    assert any("toggleconsole" in l for l in lines)
    assert 'bind "MOUSE_X" "yaw"' in lines
    assert 'bind "MOUSE_Y" "pitch"' in lines


def test_third_person_camera_injects_the_configured_camera_commands():
    director = _director("")
    lines = director._recording_warmup_console_lines(
        RecordingWarmupExtras(third_person_camera=True)
    )

    third_person_commands = [
        "cam_command 1",
        "cam_idealdist 30",
        "cam_idealyaw 0",
        "cam_idealpitch 0",
        "c_thirdpersonshoulder 1",
        "c_thirdpersonshoulderaimdist 300",
        "c_thirdpersonshoulderdist 40",
        "c_thirdpersonshoulderheight 2",
        "c_thirdpersonshoulderoffset 20",
    ]
    start = lines.index("cam_command 1")
    assert lines[start : start + len(third_person_commands)] == third_person_commands
