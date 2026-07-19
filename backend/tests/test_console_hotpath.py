import asyncio

from app import win_cs2_console
from app.env_utils import AppConfig
from app.recording.executor import recording_executor as executor_module
from app.recording.executor import spec_controller
from app.recording.executor.recording_executor import (
    RecordingExecutor,
    _ConsoleSessionState,
    _spec_by_slot_with_retry,
)


def test_console_batch_joins_only_simple_trusted_commands():
    assert win_cs2_console._build_console_batch(
        ["spec_mode 5", "spec_player 3", "cl_demo_predict 0"]
    ) == "spec_mode 5; spec_player 3; cl_demo_predict 0"


def test_console_batch_preserves_existing_grouping_and_quotes():
    assert win_cs2_console._build_console_batch(["demo_pause; demo_resume", "spec_mode 5"]) is None
    assert win_cs2_console._build_console_batch(['bind KP_5 "demo_pause"']) is None
    assert win_cs2_console._build_console_batch(["echo 'quoted value'"]) is None


def test_inject_console_batch_uses_one_submission_or_falls_back(monkeypatch):
    calls = []

    def fake_sequence(lines, **kwargs):
        calls.append((list(lines), kwargs))
        return True

    monkeypatch.setattr(win_cs2_console, "inject_console_sequence", fake_sequence)

    assert win_cs2_console.inject_console_batch(
        ["spec_mode 5", "spec_player 7"], close_console=False
    )
    assert calls[-1] == (["spec_mode 5; spec_player 7"], {"skip_console_toggle": False, "close_console": False})

    assert win_cs2_console.inject_console_batch(
        ["demo_pause; demo_resume", 'bind KP_5 "demo_pause"']
    )
    assert calls[-1][0] == ["demo_pause; demo_resume", 'bind KP_5 "demo_pause"']


def test_numeric_spec_mode_and_slot_use_one_batch(monkeypatch):
    calls = []

    def fake_batch(lines, **_kwargs):
        calls.append(list(lines))
        return True

    monkeypatch.setattr(spec_controller, "inject_console_batch", fake_batch)
    assert asyncio.run(spec_controller.spec_by_slot(7, settle=0)) is True
    assert calls == [["spec_mode 5", "spec_player 7"]]


def test_kb_overlay_is_opt_in_by_default():
    assert AppConfig().kb_overlay_enabled is False


def test_console_session_state_emits_only_changed_assignments():
    state = _ConsoleSessionState()
    requested = ["tv_listen_voice_indices 218", "cl_demo_predict 0"]

    assert state.pending_commands(requested) == requested
    state.mark_applied(requested)
    assert state.pending_commands(requested) == []
    assert state.pending_commands(
        ["tv_listen_voice_indices 436", "cl_demo_predict 0"]
    ) == ["tv_listen_voice_indices 436"]

    # Compound user-authored lines are never guessed/cached.
    compound = "cl_demo_predict 0; echo preserved"
    state.mark_applied([compound])
    assert state.pending_commands([compound]) == [compound]


def test_prepare_voice_and_post_spec_are_batched_once_per_value(monkeypatch):
    calls = []

    def fake_batch(lines, **_kwargs):
        calls.append(list(lines))
        return True

    monkeypatch.setattr(executor_module, "inject_console_batch", fake_batch)
    executor = RecordingExecutor(None, post_spec_console_lines=["cl_demo_predict 0"])

    assert asyncio.run(executor._apply_prepare_console_state(218)) is True
    assert calls == [["tv_listen_voice_indices 218", "cl_demo_predict 0"]]

    assert asyncio.run(executor._apply_prepare_console_state(218)) is True
    assert len(calls) == 1

    assert asyncio.run(executor._apply_prepare_console_state(436)) is True
    assert calls[-1] == ["tv_listen_voice_indices 436"]


def test_failed_prepare_batch_is_not_cached(monkeypatch):
    calls = []

    def fake_batch(lines, **_kwargs):
        calls.append(list(lines))
        return False

    monkeypatch.setattr(executor_module, "inject_console_batch", fake_batch)
    executor = RecordingExecutor(None, post_spec_console_lines=["cl_demo_predict 0"])

    assert asyncio.run(executor._apply_prepare_console_state(218)) is False
    assert asyncio.run(executor._apply_prepare_console_state(218)) is False
    assert len(calls) == 2


def test_spec_mode_and_verified_target_are_reused(monkeypatch):
    spec_calls = []

    async def fake_spec_by_slot(slot, mode=5, settle=0.8, *, include_mode=True):
        spec_calls.append((slot, mode, include_mode))
        return True

    async def fake_verify(_steamid64, **_kwargs):
        return True

    monkeypatch.setattr(executor_module, "spec_by_slot", fake_spec_by_slot)
    monkeypatch.setattr(executor_module, "verify_spec_target", fake_verify)
    state = _ConsoleSessionState()

    async def scenario():
        first = await _spec_by_slot_with_retry(3, "player", "steam-a", [], 1, state)
        same_target = await _spec_by_slot_with_retry(3, "player", "steam-a", [], 2, state)
        changed_target = await _spec_by_slot_with_retry(4, "other", "steam-b", [], 3, state)
        return first, same_target, changed_target

    assert asyncio.run(scenario()) == (True, True, True)
    assert spec_calls == [(3, 5, True), (4, 5, False)]


def test_post_spec_cache_is_reused_for_same_target_but_invalidated_on_switch(monkeypatch):
    spec_calls = []
    batch_calls = []

    async def fake_spec_by_slot(slot, mode=5, settle=0.8, *, include_mode=True):
        spec_calls.append((slot, include_mode))
        return True

    async def fake_verify(_steamid64, **_kwargs):
        return True

    def fake_batch(lines, **_kwargs):
        batch_calls.append(list(lines))
        return True

    monkeypatch.setattr(executor_module, "spec_by_slot", fake_spec_by_slot)
    monkeypatch.setattr(executor_module, "verify_spec_target", fake_verify)
    monkeypatch.setattr(executor_module, "inject_console_batch", fake_batch)
    executor = RecordingExecutor(None, post_spec_console_lines=["cl_demo_predict 0"])
    state = executor._console_state

    async def scenario():
        await _spec_by_slot_with_retry(3, "player", "steam-a", [], 1, state)
        await executor._apply_prepare_console_state(218)

        # GSI confirms the same POV, so spec_player and target-bound cvars both stay cached.
        await _spec_by_slot_with_retry(3, "player", "steam-a", [], 2, state)
        await executor._apply_prepare_console_state(218)

        # A real POV switch invalidates only post-spec cvars; the unchanged voice mask stays cached.
        await _spec_by_slot_with_retry(4, "other", "steam-b", [], 3, state)
        await executor._apply_prepare_console_state(218)

    asyncio.run(scenario())

    assert spec_calls == [(3, True), (4, False)]
    assert batch_calls == [
        ["tv_listen_voice_indices 218", "cl_demo_predict 0"],
        ["cl_demo_predict 0"],
    ]
