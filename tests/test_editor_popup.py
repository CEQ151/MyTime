"""v3.3.0 editor/details/subtask/recurrence coverage: dynamic TodoEditorPopup, DetailsPopup,
subtask row rendering, and recurrence-on-complete wiring — all offscreen."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit, QWidget

from state_store import AppState, SubTask, TodoItem


def _editor_host(state):
    """Duck-typed MemoWindow host for TodoEditorPopup: records add/update calls."""
    calls = {"add": [], "update": []}

    def add_todo(text, location="", ddl="", details="", subtasks=None, recur="none"):
        calls["add"].append((text, location, ddl, details, subtasks, recur))

    def update_todo(todo_id, text, location, ddl, details=None, subtasks=None, recur=None):
        calls["update"].append((todo_id, text, location, ddl, details, subtasks, recur))

    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        add_todo=add_todo,
        update_todo=update_todo,
    ), calls


def _row_host(state):
    from types import SimpleNamespace as NS

    return SimpleNamespace(
        content=QWidget(),
        app=SimpleNamespace(state=state),
        text_color_for=lambda _todo: QColor("#111820"),
        text_needs_halo=lambda: False,
        _normal_text_color=lambda: QColor("#111820"),
        edit_todo=lambda _todo_id: None,
        toggle_urgent=lambda _todo_id: None,
        complete_todo=lambda *_args: None,
        complete_subtask=lambda *_args: None,
        toggle_subtasks_collapsed=lambda _todo_id: None,
        toggle_calendar_event=lambda *_args: None,
        show_details_preview=lambda _row: None,
        hide_details_preview=lambda: None,
    )


def test_editor_popup_expands_and_roundtrips_editor_fields(qapp):
    from app import TodoEditorPopup

    state = AppState()
    host, calls = _editor_host(state)
    editor = TodoEditorPopup(host)
    base_height = editor.height()

    todo = TodoItem(
        id="t1", text="写报告", location="办公室", ddl="2099-01-01",
        details="# 细节\n- 第一条",
        subtasks=[SubTask(id="s1", text="子任务一", done=True, order=0), SubTask(id="s2", text="子任务二", order=1)],
        recur="daily",
    )
    editor.open_edit(todo, QPoint(10, 10), 460)

    assert editor.height() > base_height  # sections expanded -> grew downward
    assert editor.details_edit.isVisible() and editor.details_edit.toPlainText().startswith("# 细节")
    assert editor.subtask_box.isVisible() and len(editor._subtask_rows) == 2
    assert editor.recur_box.isVisible() and editor.recur_combo.currentData() == "daily"
    assert editor.input.text() == "写报告"

    editor.input.setText("写报告")
    editor.accept()

    assert len(calls["update"]) == 1
    call = calls["update"][0]
    assert call[:4] == ("t1", "写报告", "办公室", "2099-01-01")
    assert call[4] == "# 细节\n- 第一条"
    assert [sub.text for sub in call[5]] == ["子任务一", "子任务二"]
    assert call[6] == "daily"
    editor.deleteLater()

    # add mode round-trips too, and open_add resets expanded sections
    editor2 = TodoEditorPopup(host)
    editor2.open_edit(todo, QPoint(10, 10), 460)
    editor2.open_add(QPoint(10, 10), 460)
    assert editor2.height() == base_height
    assert not editor2.details_edit.isVisible() and editor2._subtask_rows == []
    editor2.input.setText("新事项")
    editor2._toggle_details()
    editor2.details_edit.setPlainText("**重点**")
    editor2._toggle_subtasks()
    editor2._add_subtask_row("先做一步")
    editor2._toggle_recur()
    idx = editor2.recur_combo.findData("every:")
    editor2.recur_combo.setCurrentIndex(idx)
    editor2.recur_count.setValue(3)
    editor2.accept()

    added = calls["add"][0]
    assert added[0] == "新事项" and added[3] == "**重点**"
    assert [s.text for s in added[4]] == ["先做一步"]
    assert added[5] == "every:3d"
    editor2.deleteLater()


def test_editor_details_toggle_changes_height(qapp):
    from app import TodoEditorPopup

    host, _ = _editor_host(AppState())
    editor = TodoEditorPopup(host)
    editor.open_add(QPoint(10, 10), 460)
    closed = editor.height()
    editor._toggle_details()
    assert editor.details_edit.isVisible()
    assert editor.height() > closed
    editor._toggle_details()
    assert not editor.details_edit.isVisible()
    assert editor.height() == closed
    editor.deleteLater()


def test_subtask_row_rendering_badge_and_collapse_toggle(qapp):
    from app import TodoRow

    state = AppState()
    todo = TodoItem(
        text="主页", subtasksCollapsed=True,
        subtasks=[SubTask(text="a"), SubTask(text="b", done=True), SubTask(text="c")],
    )
    host = _row_host(state)
    row = TodoRow(todo, state.settings, host)
    row.apply_text_width(300)

    assert row.sub_toggle.isVisibleTo(row) and not row.subtask_area.isVisibleTo(row)
    assert row.sub_badge.isVisibleTo(row) and row.sub_badge.text() == "1/3"

    # collapse toggle flips the flag through the host
    flipped = []
    host.toggle_subtasks_collapsed = lambda todo_id: flipped.append(todo_id)
    row._toggle_collapsed()
    assert flipped == [todo.id]

    # expanded rows show one checkbox per subtask and no badge (default = expanded)
    todo.subtasksCollapsed = False
    row._rebuild_subtask_rows()
    assert row.subtask_area.isVisibleTo(row)
    assert not row.sub_badge.isVisibleTo(row)
    assert len(row._sub_labels) == 3  # one fresh row per subtask (old ones are deleteLater'd)

    # all done -> ✓ badge when collapsed
    todo.subtasksCollapsed = True
    for sub in todo.subtasks:
        sub.done = True
    row.refresh_subtask_state()
    assert row.sub_badge.text() == "✓"

    # presses over the subtask block never arm the drag
    row.setGeometry(0, 0, 400, 200)
    row.subtask_area.setGeometry(34, 30, 300, 60)  # not shown -> simulate a laid-out geometry
    top_left = row.subtask_area.mapTo(row, QPoint(0, 0))
    assert row._in_subtask_zone(top_left + QPoint(10, 2))
    assert not row._in_subtask_zone(QPoint(300, 5))

    row.deleteLater()
    host.content.deleteLater()


def test_complete_todo_checks_all_subtasks_but_not_reverse(qapp):
    from app import MemoWindow, TodoRow

    state = AppState()
    todo = TodoItem(
        id="p1", text="父任务",
        subtasks=[SubTask(id="s1", text="a"), SubTask(id="s2", text="b")],
    )
    state.todos.append(todo)
    state.settings.completeBehavior = "dim"
    flashes = []

    class FakeRow:
        def refresh_subtask_state(self):
            pass

        def flash_completion(self):
            flashes.append(1)

    window = SimpleNamespace(
        app=SimpleNamespace(
            state=state,
            save=lambda: None,
            save_later=lambda: None,
            history_window=SimpleNamespace(refresh=lambda: None),
        ),
        close_details_preview=lambda: None,
        refresh=lambda: None,
        _rows={"p1": FakeRow()},
        details_popup=SimpleNamespace(sync_subtask=lambda *_: None),
    )

    row = TodoRow(todo, state.settings, _row_host(state))
    host_widget = row.parentWidget()

    # parent completion marks every subtask done; dim keeps the item in place
    MemoWindow.complete_todo(window, "p1", True, row)
    assert todo.done and all(sub.done for sub in todo.subtasks)
    assert len(state.todos) == 1

    # unchecking the parent leaves subtask state untouched
    MemoWindow.complete_todo(window, "p1", False, row)
    assert not todo.done and all(sub.done for sub in todo.subtasks)

    # complete_subtask on the LAST open subtask must NOT check the parent
    todo.subtasks[0].done = True
    todo.subtasks[1].done = False
    MemoWindow.complete_subtask(window, "p1", "s2", True)
    assert todo.subtasks[1].done and not todo.done  # parent stays unchecked
    assert len(flashes) == 1  # last-subtask flash fired

    # deleteLater on the row alone (with the parent host widget outliving it) corrupts the
    # deferred-delete queue in offscreen mode and crashes a later QTest.qWait — always delete
    # the host content widget together with the row (same pattern as test_memo_ux.py).
    row.deleteLater()
    host_widget.deleteLater()


def test_recurrence_completion_keeps_todo_and_snapshots_history(qapp):
    from app import MemoWindow

    state = AppState()
    future = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    todo = TodoItem(id="r1", text="打卡", ddl=future, recur="daily")
    state.todos.append(todo)
    settings = state.settings
    settings.completeBehavior = "archive"

    window = SimpleNamespace(
        app=SimpleNamespace(
            state=state,
            save=lambda: None,
            save_later=lambda: None,
            history_window=SimpleNamespace(refresh=lambda: None),
        ),
        close_details_preview=lambda: None,
        refresh=lambda: None,
    )

    class FakeRow:
        pass

    MemoWindow.complete_todo(window, "r1", True, FakeRow())

    # still in todos, rolled forward, unchecked; history carries the completed snapshot with
    # the OLD ddl and done=True
    assert [t.id for t in state.todos] == ["r1"]
    assert todo.done is False
    assert todo.lastDoneAt is not None and todo.recurAnchor is not None
    assert len(state.history) == 1
    snap = state.history[0]
    assert snap.done
    assert snap.text == "打卡"
    assert snap.ddl == future  # old ddl on the snapshot
    assert parse_next(todo.ddl) > datetime.now()


def parse_next(ddl):
    from state_store import parse_ddl

    parsed = parse_ddl(ddl)
    assert parsed is not None
    return parsed


def test_details_popup_shows_details_and_subtasks(qapp):
    from app import DetailsPopup

    state = AppState()
    todo = TodoItem(
        id="d1", text="计划", details="## 标题",
        subtasks=[SubTask(id="x", text="甲", done=True), SubTask(id="y", text="乙")],
    )
    synced = []
    host = SimpleNamespace(
        app=SimpleNamespace(state=state),
        complete_subtask=lambda todo_id, sub_id, checked: synced.append((todo_id, sub_id, checked)),
    )
    popup = DetailsPopup(host)
    popup.show_for(todo, QPoint(100, 100))

    assert popup.isVisible()
    assert popup.title.isVisible()  # both details and subtasks -> 事件细节 header
    assert popup.body.isVisible() and "标题" in popup.body.text()
    assert len(popup._sub_checks) == 2
    assert popup._sub_checks["x"].isChecked() and not popup._sub_checks["y"].isChecked()

    # a todo with neither details nor subtasks never shows
    empty = TodoItem(text="空")
    popup.hide()
    popup.show_for(empty, QPoint(100, 100))
    assert not popup.isVisible()

    # checkbox toggle routes back through complete_subtask
    popup._sub_checks["y"].setChecked(True)
    assert synced == [("d1", "y", True)]

    # sync_subtask keeps checkboxes aligned without re-emitting
    popup.sync_subtask("d1", "x", False)
    assert not popup._sub_checks["x"].isChecked()
    popup.deleteLater()


def test_row_hover_arms_details_timer_without_attribute_error(qapp):
    """Regression: enterEvent used to touch self.details_popup (the popup lives on the window
    host), so every 2s hover died with a silent AttributeError and the preview never showed."""
    from PySide6.QtCore import QTimer

    from app import TodoRow

    state = AppState()
    todo = TodoItem(text="细节行", details="正文")
    host = _row_host(state)
    shown = []
    host.show_details_preview = lambda row: shown.append(row)
    host.details_popup = SimpleNamespace(isVisible=lambda: False, _cancel_close=lambda: None)
    row = TodoRow(todo, state.settings, host)
    row.apply_text_width(300)

    from PySide6.QtGui import QEnterEvent
    from PySide6.QtCore import QPointF
    row.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert row._preview_timer is not None and row._preview_timer.isActive()

    row._preview_timer.timeout.emit()  # fires show(host) -> our stub
    assert shown == [row]

    # done todos and ones without details/subtasks never arm the timer
    row._cancel_preview()
    todo.done = True
    row.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert row._preview_timer is None
    row.deleteLater()
    host.content.deleteLater()
