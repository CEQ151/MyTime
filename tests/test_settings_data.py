"""Workstream C tests: allowCover switch wiring + export/import (备份与迁移)."""
import json
import types

import pytest

from state_store import AppState, CalendarFeed, Settings, TodoItem
import settings_ui
from settings_ui import (
    EXPORT_APP_TAG,
    export_payload,
    import_backup_path,
    validate_import,
)


def _stub_app():
    """Duck-typed app that tolerates SettingsWindow construction and _apply."""
    window = types.SimpleNamespace(apply_settings=lambda *a, **k: None)
    notifier = types.SimpleNamespace(check_now=lambda: None)
    calendar = types.SimpleNamespace(on_settings_changed=lambda: None, sync_now=lambda: None)
    return types.SimpleNamespace(
        state=AppState(),
        window=window,
        notifier=notifier,
        calendar=calendar,
        ink=None,
        floating=None,
        save=lambda: None,
        save_later=lambda: None,
        store=types.SimpleNamespace(path=None),
    )


def _state_with_data() -> AppState:
    state = AppState()
    state.settings.allowCover = True
    state.settings.calendarFeeds = [CalendarFeed(url="https://example.com/cal.ics", name="工作")]
    state.settings.customSkins = []  # image skins are excluded from exports by design
    todo = TodoItem(text="买牛奶")
    state.todos = [todo]
    done = TodoItem(text="已完成的事")
    state.history = [done]
    return state


# ── export payload builder ─────────────────────────────────────────────────────────────
def test_export_payload_keys_and_skin_strip():
    payload = export_payload(_state_with_data())
    assert payload["app"] == EXPORT_APP_TAG
    assert payload["version"] == 1
    assert isinstance(payload["exportedAt"], str) and payload["exportedAt"]
    assert set(payload) == {"app", "version", "exportedAt", "settings", "calendarFeeds", "todos", "history"}
    assert payload["settings"]["customSkins"] == []
    assert payload["settings"]["customSkinsNote"]
    assert len(payload["todos"]) == 1 and payload["todos"][0]["text"] == "买牛奶"
    assert len(payload["history"]) == 1
    # feeds travel both inside settings (what from_dict reads) and as a top-level list
    assert payload["calendarFeeds"][0]["url"] == "https://example.com/cal.ics"
    assert payload["settings"]["calendarFeeds"][0]["url"] == "https://example.com/cal.ics"


def test_export_payload_is_json_safe():
    json.dumps(export_payload(_state_with_data()), ensure_ascii=False)


# ── import validation ──────────────────────────────────────────────────────────────────
def test_validate_import_accepts_export_payload():
    validate_import(export_payload(_state_with_data()))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("app"),
        lambda d: d.update(app="something-else"),
        lambda d: d.pop("settings"),
        lambda d: d.update(settings="not-a-dict"),
        lambda d: d.pop("todos"),
        lambda d: d.update(todos={"oops": 1}),
        lambda d: d.update(history="not-a-list"),
        lambda d: "not even a dict gets here" and d.update(app=42),
    ],
)
def test_validate_import_rejects_bad_payloads(mutate):
    data = export_payload(_state_with_data())
    mutate(data)
    with pytest.raises(ValueError):
        validate_import(data)


def test_validate_import_rejects_non_dict():
    with pytest.raises(ValueError):
        validate_import(["app", "settings", "todos"])


# ── round trip: export → from_dict reproduces todos/settings/feeds/history ─────────────
def test_round_trip_export_then_from_dict():
    state = _state_with_data()
    payload = export_payload(state)
    # from_dict expects a full state dict; the payload is one (settings/todos/history).
    rebuilt = AppState.from_dict(payload)
    assert rebuilt.settings.allowCover is True
    assert rebuilt.settings.skin == "acrylic"  # customSkins stripped → skin falls back safely
    assert rebuilt.settings.customSkins == []
    assert [f.url for f in rebuilt.settings.calendarFeeds] == ["https://example.com/cal.ics"]
    assert rebuilt.settings.calendarFeeds[0].name == "工作"
    assert [t.text for t in rebuilt.todos] == ["买牛奶"]
    assert [t.text for t in rebuilt.history] == ["已完成的事"]


def test_round_trip_falls_back_when_skin_missing():
    payload = export_payload(_state_with_data())
    payload["settings"]["skin"] = "image:gone"
    rebuilt = AppState.from_dict(payload)
    assert rebuilt.settings.skin == "acrylic"


# ── backup naming helper ───────────────────────────────────────────────────────────────
def test_import_backup_path_naming(tmp_path):
    state_path = tmp_path / "liquid-state.json"
    backup = import_backup_path(state_path, "20260903-120000")
    assert backup.parent == state_path.parent
    assert backup.name == "liquid-state.json.pre-import-20260903-120000.json"


def test_import_default_file_name_format():
    import re

    assert re.fullmatch(r"MyTime-backup-\d{8}\.json", settings_ui.import_default_file_name())


# ── allowCover switch wiring ───────────────────────────────────────────────────────────
def test_allow_cover_switch_exists_and_apply_writes_state(qapp):
    app = _stub_app()
    sw = settings_ui.SettingsWindow(app)
    assert sw.allow_cover.isChecked() is False  # Settings().allowCover default
    sw.allow_cover.setChecked(True)
    sw._apply(save_now=False)
    assert app.state.settings.allowCover is True
    sw.allow_cover.setChecked(False)
    sw._apply(save_now=False)
    assert app.state.settings.allowCover is False


def test_sync_from_state_reflects_allow_cover(qapp):
    app = _stub_app()
    app.state.settings.allowCover = True
    sw = settings_ui.SettingsWindow(app)
    sw.allow_cover.blockSignals(True)
    sw.allow_cover.setChecked(False)
    sw.allow_cover.blockSignals(False)
    sw.sync_from_state()
    # signal-blocked resync must push the state value back onto the switch
    assert sw.allow_cover.isChecked() is True


def test_backup_card_present_in_about_page(qapp):
    sw = settings_ui.SettingsWindow(_stub_app())
    about_index = sw.nav.count() - 1  # 关于 is the last section
    sw.nav.setCurrentRow(about_index)
    assert sw.stack.currentIndex() == about_index
    from qfluentwidgets import BodyLabel

    label_texts = [label.text() for label in sw.findChildren(BodyLabel)]
    assert "备份与迁移" in label_texts

def test_import_flow_shows_error_on_bad_file(qapp, tmp_path, monkeypatch):
    """点击导入数据必须有反应：坏文件要走 warning 弹窗，数据保持不变（原生对话框曾静默失败）。"""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    sw = settings_ui.SettingsWindow(_stub_app())
    before = (sw.app.state.todos, sw.app.state.history, sw.app.state.settings)

    calls = {}
    monkeypatch.setattr(
        settings_ui.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(bad), "JSON (*.json)"),
    )
    monkeypatch.setattr(
        settings_ui.QMessageBox, "warning",
        lambda *a, **k: calls.setdefault("warning", a),
    )
    sw._import_data()
    assert "warning" in calls, "坏文件必须弹出导入失败提示（否则表现为点了没反应）"
    assert sw.app.state.todos == before[0]


def test_import_flow_swaps_state_on_confirm(qapp, tmp_path, monkeypatch):
    state = AppState()
    state.todos = [TodoItem(text="来自备份")]
    payload = settings_ui.export_payload(state)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    sw = settings_ui.SettingsWindow(_stub_app())
    sw.app.save = lambda: None
    sw.app.window = types.SimpleNamespace(apply_settings=lambda: None)
    sw.app.calendar = types.SimpleNamespace(on_settings_changed=lambda: None)
    sw.app.notifier = types.SimpleNamespace(check_now=lambda: None)
    monkeypatch.setattr(
        settings_ui.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(good), "JSON (*.json)"),
    )
    monkeypatch.setattr(
        settings_ui.QMessageBox, "question", lambda *a, **k: settings_ui.QMessageBox.Yes,
    )
    monkeypatch.setattr(
        settings_ui.QMessageBox, "information", lambda *a, **k: None,
    )
    sw._import_data()
    assert len(sw.app.state.todos) == 1
    assert sw.app.state.todos[0].text == "来自备份"


def test_export_flow_writes_payload(qapp, tmp_path, monkeypatch):
    sw = settings_ui.SettingsWindow(_stub_app())
    target = tmp_path / "out.json"
    monkeypatch.setattr(
        settings_ui.QFileDialog, "getSaveFileName",
        lambda *a, **k: (str(target), "JSON (*.json)"),
    )
    boxes = {}
    monkeypatch.setattr(
        settings_ui.QMessageBox, "information",
        lambda *a, **k: boxes.setdefault("info", a),
    )
    sw._export_data()
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["app"] == "mytime"  # lowercase since the MyTime rename
    assert "info" in boxes
