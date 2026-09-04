"""Coverage for v3.3.0 features: subtasks/details fields, recurrence rules and
application-on-complete, and the allowCover setting."""
from dataclasses import asdict
from datetime import datetime, timedelta

from recurrence import (
    apply_recurrence_on_complete,
    next_occurrence,
    parse_recur,
    recur_label,
    utc_now,
)
from state_store import AppState, Settings, SubTask, TodoItem


# ---------------------------------------------------------------- helpers

def _todo_with_subtasks() -> TodoItem:
    return TodoItem(
        id="t1",
        text="recurring",
        ddl="2099-01-10 09:00",
        recur="daily",
        recurAnchor=utc_now(),
        lastDoneAt=utc_now(),
        details="notes",
        subtasksCollapsed=True,
        subtasks=[SubTask(id="s1", text="step", done=True, order=2)],
    )


# ---------------------------------------------------------------- TodoItem / SubTask

def test_todoitem_roundtrips_new_fields():
    original = _todo_with_subtasks()
    restored = TodoItem.from_dict(asdict(original))

    assert restored.details == "notes"
    assert restored.subtasksCollapsed is True
    assert restored.recur == "daily"
    assert restored.recurAnchor == original.recurAnchor
    assert restored.lastDoneAt == original.lastDoneAt
    assert len(restored.subtasks) == 1
    assert restored.subtasks[0].id == "s1"
    assert restored.subtasks[0].text == "step"
    assert restored.subtasks[0].done is True
    assert restored.subtasks[0].order == 2
    # original core fields untouched
    assert restored.text == "recurring"
    assert restored.ddl == "2099-01-10 09:00"


def test_subtask_defensive_parsing_defaults_and_skips_junk():
    todo = TodoItem.from_dict(
        {
            "id": "t2",
            "subtasks": [
                {"text": "ok", "order": "3"},  # missing id/done, order coerced
                "not-a-dict",  # non-dict element skipped
                {"text": "nested", "subtasks": [{"text": "inner"}]},  # nesting dropped
                {"id": "s2", "text": "kept", "done": True},
            ],
        }
    )

    assert [s.text for s in todo.subtasks] == ["ok", "kept"]
    assert todo.subtasks[0].order == 3
    assert todo.subtasks[0].done is False
    assert todo.subtasks[0].id  # default factory id assigned
    assert todo.subtasks[1].done is True


def test_subtasks_non_list_becomes_empty():
    assert TodoItem.from_dict({"subtasks": "junk"}).subtasks == []
    assert TodoItem.from_dict({"subtasks": None}).subtasks == []


# ---------------------------------------------------------------- recur whitelist

def test_parse_recur_accepts_valid_tokens():
    assert parse_recur("none") == "none"
    assert parse_recur("weekly") == "weekly"
    assert parse_recur("every:3d") == "every:3d"
    assert parse_recur("every:2w") == "every:2w"
    assert parse_recur("every:365d") == "every:365d"


def test_parse_recur_rejects_invalid():
    assert parse_recur("every:0d") is None
    assert parse_recur("every:366d") is None
    assert parse_recur("weekly2") is None
    assert parse_recur("") is None
    assert parse_recur(None) is None


def test_todoitem_recur_whitelist_normalizes_to_none():
    assert TodoItem.from_dict({"recur": "every:3d"}).recur == "every:3d"
    assert TodoItem.from_dict({"recur": "every:0d"}).recur == "none"
    assert TodoItem.from_dict({"recur": "weekly2"}).recur == "none"
    assert TodoItem.from_dict({}).recur == "none"


def test_recur_label_custom_intervals():
    assert recur_label("daily") == "每天"
    assert recur_label("weekly") == "每周"
    assert recur_label("every:3d") == "每3天"
    assert recur_label("every:2w") == "每2周"
    assert recur_label("weekly2") == "不重复"


# ---------------------------------------------------------------- next_occurrence

def test_next_occurrence_none_is_none():
    assert next_occurrence("none", datetime(2099, 1, 1, 9), None, datetime(2026, 6, 1)) is None


def test_next_occurrence_daily_advances_one_day():
    now = datetime(2026, 6, 1, 10, 0)
    base = datetime(2026, 6, 1, 9, 0)
    assert next_occurrence("daily", base, None, now) == datetime(2026, 6, 2, 9, 0)


def test_next_occurrence_weekly_keeps_weekday():
    now = datetime(2026, 6, 1, 10, 0)  # Monday
    base = datetime(2026, 5, 25, 9, 0)  # previous Monday
    nxt = next_occurrence("weekly", base, None, now)
    assert nxt == datetime(2026, 6, 8, 9, 0)
    assert nxt.weekday() == base.weekday()


def test_next_occurrence_weekdays_skips_weekend():
    # Friday Jun 5 2026, completed after that → next is Monday Jun 8.
    now = datetime(2026, 6, 5, 12, 0)
    base = datetime(2026, 6, 5, 9, 0)
    assert next_occurrence("weekdays", base, None, now) == datetime(2026, 6, 8, 9, 0)


def test_next_occurrence_monthly_clamps_day():
    base = datetime(2026, 1, 31, 9, 0)
    now = datetime(2026, 2, 1, 10, 0)
    assert next_occurrence("monthly", base, None, now) == datetime(2026, 2, 28, 9, 0)


def test_next_occurrence_yearly_clamps_feb29():
    base = datetime(2028, 2, 29, 9, 0)
    now = datetime(2029, 3, 1, 10, 0)
    assert next_occurrence("yearly", base, None, now) == datetime(2030, 2, 28, 9, 0)


def test_next_occurrence_every_n_days_rolls_from_anchor_until_future():
    anchor = utc_now()
    now = datetime.now()
    result = next_occurrence("every:3d", None, anchor, now)
    assert result is not None
    assert result > now
    # Steps from the anchor in whole 3-day increments.
    anchor_dt = datetime.fromisoformat(anchor).replace(tzinfo=None)
    delta = (result - anchor_dt).total_seconds()
    assert delta % (3 * 86400) == 0
    assert delta >= 3 * 86400


def test_next_occurrence_skips_missed_daily():
    now = datetime(2026, 6, 10, 10, 0)
    base = now - timedelta(days=10)
    nxt = next_occurrence("daily", base, None, now)
    assert nxt > now
    assert nxt == datetime(2026, 6, 11, 10, 0)  # daily keeps the base's time-of-day


def test_next_occurrence_base_none_falls_back_to_now():
    now = datetime(2026, 6, 1, 10, 0)
    assert next_occurrence("daily", None, None, now) == datetime(2026, 6, 2, 10, 0)


# ---------------------------------------------------------------- apply on complete

def test_apply_recurrence_on_complete_advances_and_resets():
    todo = _todo_with_subtasks()
    now = datetime(2099, 1, 10, 12, 0)
    original_anchor = todo.recurAnchor

    assert apply_recurrence_on_complete(todo, now) is True
    assert todo.ddl == "2099-01-11 09:00"  # parseable back by state_store.parse_ddl
    assert todo.done is False
    assert todo.completedAt is None
    assert todo.lastDoneAt and todo.lastDoneAt != original_anchor
    assert todo.recurAnchor == todo.lastDoneAt
    assert all(sub.done is False for sub in todo.subtasks)


def test_apply_recurrence_on_complete_returns_false_for_non_recurring():
    todo = TodoItem(text="one-off", ddl="2099-01-01", recur="none", done=True)
    assert apply_recurrence_on_complete(todo, datetime(2099, 1, 1)) is False


# ---------------------------------------------------------------- Settings / old state

def test_settings_allow_cover_defaults_and_roundtrips():
    assert Settings().allowCover is False

    payload = asdict(AppState())
    payload["settings"]["allowCover"] = True
    assert AppState.from_dict(payload).settings.allowCover is True


def test_old_state_without_new_keys_loads_with_defaults():
    legacy = TodoItem.from_dict({"id": "old", "text": "legacy"})

    assert legacy.details == ""
    assert legacy.subtasks == []
    assert legacy.subtasksCollapsed is False
    assert legacy.recur == "none"
    assert legacy.recurAnchor is None
    assert legacy.lastDoneAt is None

    legacy_state = AppState.from_dict({"settings": {"skin": "acrylic"}, "todos": [{"id": "old"}]})
    assert legacy_state.settings.allowCover is False
    assert legacy_state.todos[0].recur == "none"