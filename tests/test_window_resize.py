"""Native drag-resize: hot-zone hit testing and the manual-size persistence model."""
from dataclasses import asdict
from types import SimpleNamespace

from PySide6.QtCore import QPoint

from window_layer import HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTLEFT, HTRIGHT
from state_store import AppState
from app import MemoWindow


def _stub_window(width=400, height=300):
    return SimpleNamespace(
        width=lambda: width,
        height=lambda: height,
        _dock_hidden=False,
        _dock_animating=False,
        _expanded=False,
    )


def test_resize_zone_bottom_corners_and_edges():
    memo = _stub_window()
    assert MemoWindow._resize_zone(memo, QPoint(3, 298)) == HTBOTTOMLEFT
    assert MemoWindow._resize_zone(memo, QPoint(19, 285)) == HTBOTTOMLEFT
    assert MemoWindow._resize_zone(memo, QPoint(397, 298)) == HTBOTTOMRIGHT
    assert MemoWindow._resize_zone(memo, QPoint(200, 298)) == HTBOTTOM
    assert MemoWindow._resize_zone(memo, QPoint(3, 275)) == HTLEFT
    assert MemoWindow._resize_zone(memo, QPoint(397, 275)) == HTRIGHT
    # The interior (click-through surface) and the upper edges are not resize zones.
    assert MemoWindow._resize_zone(memo, QPoint(200, 150)) is None
    assert MemoWindow._resize_zone(memo, QPoint(3, 100)) is None
    assert MemoWindow._resize_zone(memo, QPoint(397, 100)) is None


def test_resize_zone_disabled_when_dock_hidden_or_expanded():
    memo = _stub_window()
    memo._dock_hidden = True
    assert MemoWindow._resize_zone(memo, QPoint(3, 298)) is None
    memo._dock_hidden = False
    memo._dock_animating = True
    assert MemoWindow._resize_zone(memo, QPoint(3, 298)) is None
    memo._dock_animating = False
    memo._expanded = True
    assert MemoWindow._resize_zone(memo, QPoint(3, 298)) is None


def test_usersized_state_roundtrips_and_defaults_off():
    payload = asdict(AppState())
    payload["window"]["userSized"] = True
    assert AppState.from_dict(payload).window.userSized is True
    # Legacy states (no key) default to the auto-fit model.
    assert AppState.from_dict({}).window.userSized is False
