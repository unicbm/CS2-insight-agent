from types import SimpleNamespace

from app import obs_director
from app.env_utils import OBSConfig
from app.obs_director import OBSDirector, _V3_DEMO_CONTROL_BIND_LINES


def _launch_cfg_lines(monkeypatch, tmp_path) -> list[str]:
    game_root = tmp_path / "game"
    cs2 = game_root / "bin" / "win64" / "cs2.exe"
    csgo_dir = game_root / "csgo"
    cs2.parent.mkdir(parents=True)
    csgo_dir.mkdir(parents=True)
    cs2.touch()

    demo = tmp_path / "recording.dem"
    demo.write_bytes(b"demo")
    director = OBSDirector(OBSConfig(), str(cs2))

    monkeypatch.setattr(obs_director, "is_cs2_running", lambda: False)
    monkeypatch.setattr(director, "_snapshot_user_configs", lambda: None)
    monkeypatch.setattr(director, "_clear_voice_ban_files", lambda: None)
    monkeypatch.setattr(
        obs_director.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    director._launch_cs2(demo)

    assert director._copied_cfg is not None
    return director._copied_cfg.read_text(encoding="ascii").splitlines()


def test_launch_cfg_and_warmup_reserve_only_three_control_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("CS2_INSIGHT_CONSOLE_TOGGLE_KEY", raising=False)
    cfg_lines = _launch_cfg_lines(monkeypatch, tmp_path)
    binding_lines = [line for line in cfg_lines if line.startswith(("bind ", "unbind"))]

    assert [*binding_lines, *_V3_DEMO_CONTROL_BIND_LINES] == [
        'bind "F10" "toggleconsole"',
        "bind KP_5 demo_pause",
        "bind KP_6 demo_resume",
    ]


def test_custom_console_key_does_not_also_take_over_f10(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_INSIGHT_CONSOLE_TOGGLE_KEY", "F9")
    cfg_lines = _launch_cfg_lines(monkeypatch, tmp_path)

    assert [line for line in cfg_lines if line.startswith(("bind ", "unbind"))] == [
        'bind "F9" "toggleconsole"',
    ]
