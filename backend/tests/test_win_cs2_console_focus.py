import sys
from types import SimpleNamespace

import pytest

from app import win_cs2_console


@pytest.mark.skipif(sys.platform != "win32", reason="Windows foreground handling only")
def test_focus_does_not_tap_alt_when_cs2_is_already_foreground(monkeypatch):
    hwnd = 1234
    alt_taps: list[str] = []

    monkeypatch.setattr(
        win_cs2_console,
        "user32",
        SimpleNamespace(GetForegroundWindow=lambda: hwnd),
    )
    monkeypatch.setattr(
        win_cs2_console,
        "_unlock_foreground",
        lambda: alt_taps.append("alt"),
    )

    assert win_cs2_console._focus_hwnd(hwnd) == hwnd
    assert alt_taps == []
