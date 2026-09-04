"""Import/construction smoke gate for the UI split. Builds each window/dialog
headless (Qt offscreen) so a missing import or an import cycle introduced by the
module split fails CI. The core gate imports only the split UI modules — not app
or the D3D engine — so it stays green on machines without a GPU."""
import types

import pytest
from PySide6.QtCore import Qt

from state_store import AppState


def _stub_app():
    """Minimal duck-typed app: dialogs only read `state` at construction time."""
    return types.SimpleNamespace(state=AppState(), calendar=None, updater=None, tray=None)


def test_split_ui_modules_build_without_engine(qapp):
    import ui_common  # noqa: F401
    import calendar_manager
    import notify_manager
    import settings_ui
    import update_ui
    import updater

    app = _stub_app()

    sw = settings_ui.SettingsWindow(app)
    assert sw.nav.count() == 5  # 外观 / 行为 / 提醒 / 日历订阅 / 关于
    assert not (sw.windowFlags() & Qt.WindowStaysOnTopHint)  # layers like a normal window
    assert not sw.windowIcon().isNull()  # taskbar must not fall back to the pythonw icon
    assert sw.frame.graphicsEffect() is None  # full-bleed shadow effect = repaint tax, no visual

    # The combo drop-down must follow the settings control font instead of the library's
    # app-default small font (menu item text is painted with the view's own font).
    from ui_common import SETTING_CONTROL_FONT_PX

    combo_menu = sw.skin._createComboMenu()
    assert combo_menu.view.font().pixelSize() == SETTING_CONTROL_FONT_PX
    assert combo_menu.view._itemHeight == round(SETTING_CONTROL_FONT_PX * 1.55)
    combo_menu.deleteLater()

    release = updater.ReleaseInfo(
        tag="v9.9.9", version="9.9.9", notes="n", html_url="u",
        installer_url="", installer_name="", installer_size=0,
    )
    update_ui.UpdateDialog(app, release)
    update_ui.ChangelogDialog("notes")
    calendar_manager.CalendarManager(app)
    notify_manager.NotificationManager(app)


def test_status_labels_survive_theme_colour_change(qapp):
    """qfluentwidgets re-applies its base 14px qss on every setThemeColor/theme fire, which
    wipes plain setStyleSheet font overrides — the 当前版本/上次同步 labels shrank back. The
    setCustomStyleSheet path must survive a theme-colour change."""
    from qfluentwidgets import setTheme, setThemeColor, Theme

    import ui_common
    import settings_ui

    setTheme(Theme.LIGHT)
    app = _stub_app()
    sw = settings_ui.SettingsWindow(app)
    assert sw.update_status_label.font().pixelSize() == ui_common.SETTING_STATUS_FONT_PX
    assert sw.calendar_status_label.font().pixelSize() == ui_common.SETTING_STATUS_FONT_PX

    setThemeColor("#3D7AB3")  # what the app does at startup and on every ink-theme switch
    assert sw.update_status_label.font().pixelSize() == ui_common.SETTING_STATUS_FONT_PX
    assert sw.calendar_status_label.font().pixelSize() == ui_common.SETTING_STATUS_FONT_PX


def test_app_windows_build(qapp):
    try:
        import app as app_mod  # pulls in the D3D engine wrapper
    except Exception as exc:  # no engine/DLL on this host — skip, don't fail the gate
        pytest.skip(f"app/engine not importable here: {exc}")
    app = _stub_app()
    app_mod.HistoryWindow(app)
    editor = app_mod.TodoEditorPopup(None)
    # The editor is no longer fixed at 132: it keeps a 132 base but grows with the expanded
    # details/subtask/recur sections. Closed, it sits at its compact base height.
    assert editor.height() >= 132


def test_large_color_dialog_resized(qapp):
    """The stock qfluentwidgets ColorDialog is a 488x696 absolute-position canvas with 14px
    fonts — the app must present the enlarged re-laid-out subclass instead."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget

    from ui_common import LargeColorDialog

    parent = QWidget()
    parent.resize(1600, 900)
    dialog = LargeColorDialog(QColor("#C3FFFE"), "窗口颜色", parent)
    assert dialog.widget.width() >= 580
    assert dialog.widget.height() >= 850
    assert dialog.hexLineEdit.height() >= 44
    assert dialog.huePanel.width() >= 320
    assert dialog.yesButton.height() >= 44
    assert dialog.titleLabel.font().pixelSize() == 30
    # children must stay inside the scroll canvas
    assert dialog.blueLineEdit.geometry().bottom() <= dialog.scrollWidget.height()
    # alpha variant stays consistent too
    alpha_dialog = LargeColorDialog(QColor("#80C3FFFE"), "窗口颜色", parent, enableAlpha=True)
    assert alpha_dialog.opacityLineEdit.geometry().bottom() <= alpha_dialog.scrollWidget.height()
