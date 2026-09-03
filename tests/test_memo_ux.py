"""Regression coverage for configurable highlighting, manual todo ordering, and row hit zones."""
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QWidget

from state_store import AppState, CalendarEvent, TodoItem


@pytest.mark.parametrize(("saved", "expected"), [(0, 1), (7, 7), (99, 30)])
def test_near_highlight_days_roundtrips_and_clamps(saved, expected):
    payload = asdict(AppState())
    payload["settings"]["nearHighlightDays"] = saved

    restored = AppState.from_dict(payload)

    assert restored.settings.nearHighlightDays == expected


def test_todo_sort_key_pins_urgent_then_uses_manual_order(qapp):
    from app import MemoWindow

    todos = [
        TodoItem(id="late-ddl", ddl="2099-01-01", order=1),
        TodoItem(id="early-ddl", ddl="2020-01-01", order=3),
        TodoItem(id="urgent", urgent=True, order=9),
        TodoItem(id="first", order=0),
    ]

    assert [todo.id for todo in sorted(todos, key=MemoWindow._todo_sort_key)] == [
        "urgent", "first", "late-ddl", "early-ddl",
    ]


def test_configured_near_window_applies_to_todos_and_calendar(qapp):
    from app import CalendarRow, TodoRow

    state = AppState()
    state.settings.nearHighlightDays = 2
    host = QWidget()
    parent = SimpleNamespace(
        content=host,
        app=SimpleNamespace(state=state),
        text_color_for=lambda _todo: QColor("#111820"),
        text_needs_halo=lambda: False,
        _normal_text_color=lambda: QColor("#111820"),
        edit_todo=lambda _todo_id: None,
        toggle_urgent=lambda _todo_id: None,
        complete_todo=lambda *_args: None,
        toggle_calendar_event=lambda *_args: None,
    )
    start = (datetime.now() + timedelta(days=2, hours=12)).strftime("%Y-%m-%d %H:%M")
    todo_row = TodoRow(TodoItem(text="test", ddl=start), state.settings, parent)
    event_row = CalendarRow(CalendarEvent(summary="test", start=start), False, parent)

    assert todo_row._ddl_status() == "normal"
    assert event_row._event_status() == "normal"

    state.settings.nearHighlightDays = 3
    assert todo_row._ddl_status() == "near"
    assert event_row._event_status() == "near"

    todo_row.deleteLater()
    event_row.deleteLater()
    host.deleteLater()


def _todo_row_host(state):
    return SimpleNamespace(
        content=QWidget(),
        app=SimpleNamespace(state=state),
        text_color_for=lambda _todo: QColor("#111820"),
        text_needs_halo=lambda: False,
        _normal_text_color=lambda: QColor("#111820"),
        edit_todo=lambda _todo_id: None,
        toggle_urgent=lambda _todo_id: None,
        complete_todo=lambda *_args: None,
        toggle_calendar_event=lambda *_args: None,
    )


def _mouse_event(kind, pos):
    return QMouseEvent(kind, QPointF(pos), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_todo_row_checkbox_zone_clicks_instead_of_dragging(qapp):
    from app import TodoRow

    state = AppState()
    parent = _todo_row_host(state)
    drag_calls = []
    parent.begin_todo_reorder = lambda _row: drag_calls.append("begin")
    row = TodoRow(TodoItem(text="test"), state.settings, parent)
    row.setGeometry(QRect(0, 0, 400, 44))
    row.checkbox.setGeometry(QRect(6, 10, 24, 24))
    box = row.checkbox.geometry()

    # Zone helper: the box and a few px of margin around it; the text area stays a drag surface.
    assert row._in_checkbox_zone(box.center())
    assert row._in_checkbox_zone(QPoint(box.left() - 4, box.top() - 4))
    assert not row._in_checkbox_zone(QPoint(box.left() - 10, box.center().y()))
    assert not row._in_checkbox_zone(QPoint(300, 22))

    # Hover feedback: pointer over the zone, drag hand elsewhere.
    hover = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(box.center()), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    row.mouseMoveEvent(hover)
    assert row.cursor().shape() == Qt.PointingHandCursor
    hover_away = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(300, 22), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    row.mouseMoveEvent(hover_away)
    assert row.cursor().shape() == Qt.OpenHandCursor

    # Press + release inside the zone toggles the box and never arms the reorder drag.
    assert not row.checkbox.isChecked()
    row.mousePressEvent(_mouse_event(QMouseEvent.Type.MouseButtonPress, box.center()))
    assert row._drag_start is None and row._zone_press
    row.mouseReleaseEvent(_mouse_event(QMouseEvent.Type.MouseButtonRelease, box.center()))
    assert row.checkbox.isChecked()
    assert drag_calls == [] and not row._dragging

    # A press outside the zone still arms the drag as before.
    row.mousePressEvent(_mouse_event(QMouseEvent.Type.MouseButtonPress, QPoint(300, 22)))
    assert row._drag_start == QPoint(300, 22) and not row._zone_press

    row.deleteLater()
    parent.content.deleteLater()
